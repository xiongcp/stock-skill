#!/usr/bin/env python3
"""录制真实行情 fixture（需要联网，手动执行；测试本身离线回放这些文件）。

用法：
  uv run python tests/record_fixtures.py                    # 全量重录，写入 tests/fixtures/recorded/
  uv run python tests/record_fixtures.py --only quotes      # 只录指定分组（sina / yahoo / tencent / events / quotes / search，可逗号分隔）

录制内容：新浪不复权日线、Yahoo 日线、腾讯指数日线、Yahoo 除权除息事件、腾讯行情快照中的证券简称与港股每手股数，
以及腾讯证券搜索（名称转代码）的原始响应。
只保存已收盘K线（按交易所时区与收盘时间判定），并在 MANIFEST.json 记录每个文件的录制时间、来源与 sha256。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from data_gateway import (  # noqa: E402
    fetch_sina_daily_bars,
    fetch_tencent_index_daily_bars,
    fetch_tencent_quote_meta,
    fetch_yahoo_corporate_actions,
    fetch_yahoo_daily_bars,
)
from market_rules import is_daily_bar_complete  # noqa: E402
from resolve_symbol import fetch_tencent_search_raw  # noqa: E402

OUT = Path(__file__).resolve().parent / "fixtures" / "recorded"
SINA = {
    "sh000001": "CN",
    "sz399001": "CN",
    "sz399006": "CN",
    "sh000688": "CN",
    "sh000300": "CN",
    "sh600519": "CN",
    "sh601318": "CN",
    "sz000002": "CN",
    "sz002594": "CN",
    "sh510300": "CN",
    "sh600289": "CN",  # ST信通：风险警示股票样本
}
YAHOO = {"NVDA": "US", "SPY": "US", "^VIX": "US", "0700.HK": "HK", "1810.HK": "HK", "^HSI": "HK"}
TENCENT = {"hkHSTECH": "HK"}
EVENTS = ("600519.SS", "601318.SS", "000002.SZ", "002594.SZ", "510300.SS", "600289.SS", "NVDA", "SPY", "0700.HK", "1810.HK")
# 腾讯行情快照：只取证券简称与港股每手股数（标的元数据，不含价格）
QUOTES = (
    "sh000001", "sh000300", "sh000688", "sz399001", "sz399006",
    "sh600519", "sh601318", "sz000002", "sz002594", "sh510300", "sh600289",
    "hkHSI", "hkHSTECH", "hk00700", "hk01810", "hk00005",
    "usNVDA", "usSPY", "usBRK.B",
)
# 腾讯证券搜索（名称/拼音/代码 → 候选列表）：文件名 -> 查询词，保存原始响应供 resolve_symbol 离线回放
SEARCH = {
    "maotai_zh": "茅台",
    "maotai_py": "maotai",
    "xiaomi_zh": "小米",
    "tencent_zh": "腾讯",
    "pingan_bank_zh": "平安银行",
    "code_000001": "000001",
    "hs300_zh": "沪深300",
    "nvda": "nvda",
    "brk": "brk",
    "not_found": "zzqqxxnotexist",
}
GROUPS = ("sina", "yahoo", "tencent", "events", "quotes", "search")


def _completed(bars: list[dict], market: str, now: datetime) -> list[dict]:
    return [b for b in bars if is_daily_bar_complete(b["day"], market, now)]


def _write(rel: str, payload: object, manifest: dict, source: str, now: datetime) -> None:
    path = OUT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    rows = payload if isinstance(payload, list) else []
    manifest["files"][rel] = {
        "kind": "rows" if isinstance(payload, list) else "object",
        "source": source,
        "recorded_at_utc": now.isoformat(timespec="seconds"),
        "rows": len(rows) if isinstance(payload, list) else 1,
        "first_day": rows[0].get("day") or rows[0].get("ex_date") if rows else None,
        "last_day": rows[-1].get("day") or rows[-1].get("ex_date") if rows else None,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Record real market-data fixtures (network required)")
    parser.add_argument("--only", default=",".join(GROUPS), help=f"Comma-separated groups to record: {', '.join(GROUPS)}")
    groups = {g.strip() for g in parser.parse_args().only.split(",") if g.strip()}
    unknown = groups - set(GROUPS)
    if unknown:
        raise SystemExit(f"unknown groups: {sorted(unknown)}")

    now = datetime.now(timezone.utc)
    manifest_path = OUT / "MANIFEST.json"
    full = groups == set(GROUPS)
    if full or not manifest_path.exists():
        manifest: dict = {"recorded_at_utc": now.isoformat(timespec="seconds"), "files": {}}
    else:
        # 部分重录：保留其他文件的原始记录，只更新本次录制的条目
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    written: list[str] = []
    if "sina" in groups:
        for code, market in SINA.items():
            bars = _completed(fetch_sina_daily_bars(code, datalen=1023), market, now)
            _write(f"sina_daily/{code}.json", bars, manifest, f"SINA getKLineData symbol={code} scale=240 (不复权)", now)
            written.append(f"sina_daily/{code}.json")
    if "yahoo" in groups:
        for sym, market in YAHOO.items():
            bars = _completed(fetch_yahoo_daily_bars(sym, range_str="5y", datalen=None), market, now)
            _write(f"yahoo_daily/{sym.replace('^', '')}.json", bars, manifest, f"YAHOO chart {sym} 1d range=5y", now)
            written.append(f"yahoo_daily/{sym.replace('^', '')}.json")
    if "tencent" in groups:
        for code, market in TENCENT.items():
            bars = _completed(fetch_tencent_index_daily_bars(code, datalen=1000), market, now)
            _write(f"tencent_daily/{code}.json", bars, manifest, f"TENCENT fqkline {code} day qfq", now)
            written.append(f"tencent_daily/{code}.json")
    if "events" in groups:
        for sym in EVENTS:
            _write(f"yahoo_events/{sym}.json", fetch_yahoo_corporate_actions(sym), manifest, f"YAHOO chart {sym} events=div,splits", now)
            written.append(f"yahoo_events/{sym}.json")
    if "quotes" in groups:
        for code in QUOTES:
            _write(f"tencent_quote/{code}.json", fetch_tencent_quote_meta(code), manifest, f"TENCENT qt.gtimg.cn q={code}（简称/每手股数）", now)
            written.append(f"tencent_quote/{code}.json")
    if "search" in groups:
        for fname, query in SEARCH.items():
            payload = {"query": query, "raw": fetch_tencent_search_raw(query)}
            _write(f"tencent_search/{fname}.json", payload, manifest, f"TENCENT smartbox.gtimg.cn/s3 q={query}（原始响应）", now)
            written.append(f"tencent_search/{fname}.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: (manifest["files"][k]["rows"], manifest["files"][k]["last_day"]) for k in written}, ensure_ascii=False))


if __name__ == "__main__":
    main()
