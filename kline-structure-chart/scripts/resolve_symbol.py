#!/usr/bin/env python3
"""证券中文名 / 拼音 / 代码 → 本技能使用的代码（数据源：腾讯证券搜索 smartbox.gtimg.cn）。

用法：
  uv run python scripts/resolve_symbol.py 茅台
  uv run python scripts/resolve_symbol.py xiaomi --market HK

输出一段 JSON：
  status = RESOLVED   唯一结果，symbol / market / asset_type 可直接传给 analyze_and_forecast.py
  status = AMBIGUOUS  多个候选，列在 candidates 里，必须请用户选择，不得自行挑选
找不到时以 [INVALID_ARGUMENT] 非零退出；搜索接口不可用时以 [DATA_UNAVAILABLE] 非零退出。不凭记忆猜测代码。
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.parse
from typing import Any

import data_gateway
from market_rules import US_TICKER_PATTERN, is_hk_etp_code, resolve_instrument

SOURCE = "TENCENT_SMARTBOX"
MAX_QUERY_LEN = 32

# 腾讯搜索的证券类型 → 本技能的 asset_type；其他类型（权证/牛熊证 QZ、场外基金 KJ、B 股等）不支持
_TYPE_MAP = {
    "GP-A": "equity",
    "GP": "equity",
    "ZS": "index",
    "ETF": "etf",
    "QDII-ETF": "etf",
    "LOF": "etf",
    "QDII-LOF": "etf",
}
# 港股指数只支持有数据源的两个（见 references/hk-market.md）
_HK_INDICES = ("HSI", "HSTECH")


def fetch_tencent_search_raw(query: str) -> str:
    """调用腾讯证券搜索接口，返回原始响应文本（形如 v_hint="sh~600519~\\u8d35...~gzmt~GP-A^..."）。"""
    url = f"https://smartbox.gtimg.cn/s3/?v=2&q={urllib.parse.quote(query, safe='')}&t=all&c=1"
    try:
        return data_gateway._http_get(url, encoding="gbk", timeout=8)
    except Exception as exc:  # noqa: BLE001 - 网络/限流统一转成数据不可用
        raise RuntimeError(f"腾讯证券搜索接口不可用: {type(exc).__name__}: {exc}") from exc


def _unescape(text: str) -> str:
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)


def parse_search_response(raw: str) -> list[dict[str, str]]:
    """解析原始响应为 [{exchange, code, name, pinyin, type}]；无结果（v_hint="N"）返回空列表。"""
    m = re.search(r'v_hint="(.*?)"', raw, re.S)
    if not m:
        raise RuntimeError(f"腾讯证券搜索响应格式无法识别: {raw[:80]!r}")
    body = m.group(1).strip()
    if body in ("", "N"):
        return []
    rows: list[dict[str, str]] = []
    for item in body.split("^"):
        parts = item.split("~")
        if len(parts) < 5:
            continue
        rows.append(
            {
                "exchange": parts[0].strip().lower(),
                "code": parts[1].strip(),
                "name": _unescape(parts[2]).strip(),
                "pinyin": parts[3].strip(),
                "type": parts[4].strip(),
            }
        )
    return rows


def to_candidate(row: dict[str, str]) -> dict[str, Any] | None:
    """把一条搜索结果映射为本技能支持的代码；不支持的品种返回 None。"""
    asset_type = _TYPE_MAP.get(row["type"])
    if not asset_type:
        return None
    exchange, code = row["exchange"], row["code"]
    if exchange in ("sh", "sz", "bj"):
        if not re.fullmatch(r"\d{6}", code):
            return None
        symbol, market = f"{code}.{exchange.upper()}", "CN"
    elif exchange == "hk":
        if asset_type == "index":
            if code.upper() not in _HK_INDICES:
                return None
            symbol = f"{code.upper()}.HK"
        elif re.fullmatch(r"\d{5}", code) and not code.startswith("8"):
            # 80000~89999 为人民币柜台，与港币柜台是同一只股票，只保留港币柜台
            symbol = f"{code}.HK"
            if "etf" in row["name"].lower() or is_hk_etp_code(code):
                asset_type = "etf"
        else:
            return None
        market = "HK"
    elif exchange == "us":
        if asset_type == "index":
            return None  # 美股指数在 Yahoo 的代码（如 ^GSPC）与腾讯不同，请用 SPY / QQQ 等 ETF 代理
        ticker, _, venue = code.upper().rpartition(".")
        if venue == "PS" or not US_TICKER_PATTERN.fullmatch(ticker):
            return None  # .ps 为场外粉单（OTC），本技能不支持
        symbol, market = f"{ticker}.US", "US"
    else:
        return None
    try:
        resolve_instrument(symbol, market=market, asset_type=asset_type)
    except ValueError:
        return None
    return {"symbol": symbol, "market": market, "asset_type": asset_type, "name": row["name"], "vendor_type": row["type"]}


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def resolve(
    query: str,
    market: str | None = None,
    asset_type: str | None = None,
    raw: str | None = None,
) -> dict[str, Any]:
    q = query.strip()
    if not q or len(q) > MAX_QUERY_LEN:
        raise ValueError(f"查询词长度须为 1~{MAX_QUERY_LEN} 个字符: {query!r}")
    rows = parse_search_response(raw if raw is not None else fetch_tencent_search_raw(q))
    candidates: list[dict[str, Any]] = []
    for row in rows:
        cand = to_candidate(row)
        if not cand or any(c["symbol"] == cand["symbol"] for c in candidates):
            continue
        if market and cand["market"] != market:
            continue
        if asset_type and cand["asset_type"] != asset_type:
            continue
        candidates.append(cand)
    if not candidates:
        scope = "".join(f" {k}={v}" for k, v in (("market", market), ("asset_type", asset_type)) if v)
        raise ValueError(f"找不到与 {q!r}{scope} 对应的 A股/港股/美股 代码，请提供完整代码（如 600519.SH、00700.HK、NVDA.US）")
    nq = _norm(q)
    # 名称或代码完全一致的候选（如"平安银行"、"00700"、"nvda"）优先于模糊匹配
    exact = [c for c in candidates if nq in (_norm(c["name"]), c["symbol"].lower(), c["symbol"].rsplit(".", 1)[0].lower())]
    chosen = exact if len(exact) == 1 else candidates if len(candidates) == 1 else None
    out: dict[str, Any] = {"query": q, "source": SOURCE, "status": "RESOLVED" if chosen else "AMBIGUOUS"}
    if chosen:
        c = chosen[0]
        out.update(c)
        out["command"] = (
            "uv run python scripts/analyze_and_forecast.py "
            f"--market {c['market']} --symbol {c['symbol']} --asset-type {c['asset_type']} --output-dir artifacts/{c['symbol']}"
        )
    else:
        out["message"] = "匹配到多个证券，请让用户从 candidates 中选择后再分析"
    out["candidates"] = candidates
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve a Chinese name / pinyin / code to a CN/HK/US symbol via Tencent search")
    parser.add_argument("query", help="证券名称、拼音首字母或代码，如 茅台 / gzmt / 00700 / nvda")
    parser.add_argument("--market", choices=["CN", "HK", "US"], help="只保留某个市场的结果")
    parser.add_argument("--asset-type", choices=["index", "equity", "etf"], help="只保留某类品种")
    args = parser.parse_args()
    try:
        result = resolve(args.query, market=args.market, asset_type=args.asset_type)
    except RuntimeError as exc:
        raise SystemExit(f"[DATA_UNAVAILABLE] {exc}") from exc
    except ValueError as exc:
        raise SystemExit(f"[INVALID_ARGUMENT] {exc}") from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
