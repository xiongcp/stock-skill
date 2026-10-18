from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from contracts import AnalysisRequest, MarketDataBundle, validate_bars
from market_rules import (
    DEFAULT_BENCHMARKS,
    KNOWN_INSTRUMENTS,
    MARKET_SESSIONS,
    TENCENT_INDEX_SOURCES,
    apply_point_in_time_adjustment,
    is_daily_bar_complete,
    latest_closed_session,
    is_st_instrument,
    resolve_instrument,
    tencent_quote_code_for,
    yahoo_events_symbol_for,
    yahoo_symbol_for,
)

MIN_LIVE_BARS = 130
# 取数长度固定（覆盖 --days 上限 500 + MA250 预热），保证所有计算与展示参数 --days 无关
FETCH_BARS = 760


def _http_get(url: str, encoding: str = "utf-8", timeout: int = 12) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Referer": "https://finance.sina.com.cn/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode(encoding, errors="ignore")


def _url_token(token: str) -> str:
    """代码拼进 URL 前做百分号编码（只保留行情代码会用到的 ^ . - 字符），杜绝路径或查询参数注入。"""
    return urllib.parse.quote(token, safe="^.-")


def fetch_sina_daily_bars(sina_symbol: str, datalen: int = 450) -> list[dict[str, Any]]:
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={_url_token(sina_symbol)}&scale=240&ma=no&datalen={datalen}"
    )
    raw = _http_get(url)
    rows = json.loads(raw)
    if not isinstance(rows, list) or len(rows) < 60:
        raise RuntimeError(f"Sina daily kline unavailable for {sina_symbol}")
    bars: list[dict[str, Any]] = []
    for row in rows:
        bars.append(
            {
                "day": str(row["day"])[:10],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume") or 0.0),
                "complete": True,
            }
        )
    return bars


def fetch_yahoo_daily_bars(
    yahoo_symbol: str,
    range_str: str = "5y",
    datalen: int | None = FETCH_BARS,
) -> list[dict[str, Any]]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{_url_token(yahoo_symbol)}?interval=1d&range={range_str}"
    raw = _http_get(url)
    payload = json.loads(raw)
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError(f"Yahoo chart empty for {yahoo_symbol}")
    r0 = result[0]
    # 按交易所时区换算交易日，避免 UTC 日期与本地交易日错位
    try:
        bar_tz: Any = ZoneInfo((r0.get("meta") or {}).get("exchangeTimezoneName") or "UTC")
    except Exception:
        bar_tz = timezone.utc
    timestamps = r0.get("timestamp") or []
    quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []
    bars: list[dict[str, Any]] = []
    seen_days: set[str] = set()
    for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, vols):
        if None in (o, h, l, c):
            continue
        day = datetime.fromtimestamp(int(ts), tz=bar_tz).strftime("%Y-%m-%d")
        if day in seen_days:
            continue
        seen_days.add(day)
        of, hf, lf, cf = float(o), float(h), float(l), float(c)
        if min(of, hf, lf, cf) <= 0:
            continue
        bars.append(
            {
                "day": day,
                "open": round(of, 4),
                "high": round(max(of, hf, lf, cf), 4),
                "low": round(min(of, hf, lf, cf), 4),
                "close": round(cf, 4),
                "volume": float(v or 0.0),
                "complete": True,
            }
        )
    bars.sort(key=lambda x: x["day"])
    if datalen is not None:
        bars = bars[-datalen:]
    return bars


def fetch_tencent_index_daily_bars(tencent_code: str, datalen: int = 760) -> list[dict[str, Any]]:
    """腾讯指数日线（行字段顺序：日期, 开, 收, 高, 低, 成交量），用于 Yahoo 无历史的港股指数（如恒生科技指数）。"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={_url_token(tencent_code)},day,,,{datalen},qfq"
    payload = json.loads(_http_get(url))
    node = ((payload.get("data") or {}).get(tencent_code)) or {}
    rows = node.get("day") or node.get("qfqday") or []
    bars: list[dict[str, Any]] = []
    for row in rows:
        o, c, h, l = (float(row[1]), float(row[2]), float(row[3]), float(row[4]))
        if min(o, c, h, l) <= 0:
            continue
        bars.append(
            {
                "day": str(row[0])[:10],
                "open": o,
                "high": max(o, c, h, l),
                "low": min(o, c, h, l),
                "close": c,
                "volume": float(row[5]) if len(row) > 5 else 0.0,
                "complete": True,
            }
        )
    if not bars:
        raise RuntimeError(f"Tencent daily kline empty for {tencent_code}")
    return bars


def fetch_tencent_quote_meta(tencent_code: str) -> dict[str, Any]:
    """腾讯行情快照：返回证券简称与港股每手股数（字段 60）。仅用于标的元数据，不参与任何价格计算。"""
    raw = _http_get(f"https://qt.gtimg.cn/q={_url_token(tencent_code)}", encoding="gbk")
    _, _, value = raw.strip().partition("=")
    parts = value.strip().rstrip(";").strip('"').split("~")
    if len(parts) < 4 or not parts[1].strip():
        raise RuntimeError(f"Tencent quote empty for {tencent_code}")
    lot_size: int | None = None
    if tencent_code.startswith("hk") and len(parts) > 60:
        try:
            lot_size = int(float(parts[60])) or None
        except ValueError:
            lot_size = None
    return {"code": tencent_code, "name": re.sub(r"\s+", "", parts[1]), "lot_size": lot_size}


def _enrich_instrument(
    inst: dict[str, Any],
    request: AnalysisRequest,
    allow_network: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """用行情快照补全证券简称、A 股 ST 标识与港股每手股数；取不到时保留原值并在状态中写明原因（非致命）。

    名称优先级：用户 --name > 内置名称 > 行情快照简称 > 代码。ST 由显式 --st 或快照简称前缀识别。
    """
    status: dict[str, Any] = {"source": None, "vendor_name": None, "lot_size_source": None, "error": None}
    quote: dict[str, Any] | None = None
    if not allow_network:
        status["error"] = "网络已禁用(--offline)"
    else:
        try:
            code = tencent_quote_code_for(inst["symbol"], inst["market"])
            quote = fetch_tencent_quote_meta(code)
            status["source"] = f"TENCENT_QUOTE({code})"
            status["vendor_name"] = quote["name"]
        except Exception as exc:  # noqa: BLE001 - 元数据缺失不阻断分析，但必须披露
            status["error"] = f"{type(exc).__name__}: {exc}"
    out = dict(inst)
    if quote and not request.get("name") and inst["symbol"] not in KNOWN_INSTRUMENTS:
        out["name"] = quote["name"]
    if out["market"] == "CN" and out["asset_type"] != "index":
        out["is_st"] = is_st_instrument(
            out["symbol"], (quote or {}).get("name") or out.get("name", ""), bool(request.get("is_st")) or bool(out.get("is_st"))
        )
    if out["market"] == "HK" and out["asset_type"] != "index":
        builtin_lot = KNOWN_INSTRUMENTS.get(out["symbol"], {}).get("lot_size")
        if quote and quote.get("lot_size"):
            out["lot_size"] = out["min_order_shares"] = out["lot_step"] = int(quote["lot_size"])
            status["lot_size_source"] = "VENDOR_QUOTE"
        elif builtin_lot:
            out["lot_size"] = out["min_order_shares"] = out["lot_step"] = int(builtin_lot)
            status["lot_size_source"] = "BUILTIN"
        else:
            # 每手股数未知：不沿用 resolve_instrument 的 100 股猜测，交由规则层按假设值计算并在报告中披露
            for key in ("lot_size", "min_order_shares", "lot_step"):
                out.pop(key, None)
            status["lot_size_source"] = "ASSUMED_DEFAULT"
    return out, status


def fetch_yahoo_corporate_actions(yahoo_symbol: str, range_str: str = "5y") -> list[dict[str, Any]]:
    """Yahoo 除权除息事件：[{ex_date, split_ratio, dividend}]，日期按交易所时区换算。"""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{_url_token(yahoo_symbol)}"
        f"?interval=1d&range={range_str}&events=div%2Csplits"
    )
    payload = json.loads(_http_get(url))
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError(f"Yahoo events empty for {yahoo_symbol}")
    r0 = result[0]
    try:
        tz: Any = ZoneInfo((r0.get("meta") or {}).get("exchangeTimezoneName") or "UTC")
    except Exception:
        tz = timezone.utc
    events = r0.get("events") or {}
    actions: list[dict[str, Any]] = []
    for item in (events.get("splits") or {}).values():
        num, den = float(item.get("numerator") or 0), float(item.get("denominator") or 0)
        if num > 0 and den > 0:
            day = datetime.fromtimestamp(int(item["date"]), tz=tz).strftime("%Y-%m-%d")
            actions.append({"ex_date": day, "split_ratio": num / den, "dividend": 0.0})
    for item in (events.get("dividends") or {}).values():
        amount = float(item.get("amount") or 0)
        if amount > 0:
            day = datetime.fromtimestamp(int(item["date"]), tz=tz).strftime("%Y-%m-%d")
            actions.append({"ex_date": day, "split_ratio": 1.0, "dividend": amount})
    return sorted(actions, key=lambda a: a["ex_date"])


def build_corporate_actions(
    raw_bars: list[dict[str, Any]],
    events: list[dict[str, Any]],
    include_splits: bool,
) -> list[dict[str, Any]]:
    """把除权除息事件换算成复权参数：dividend_factor = (除息前一日收盘 - 每股股息) / 除息前一日收盘。

    - include_splits=False 用于 Yahoo 港美股行情（其 OHLC 与股息均已按拆股全历史调整，处于同一股本口径）。
    - include_splits=True 用于新浪 A 股不复权行情：Yahoo 返回的 dividend 已被 ex_date 当日及之后的拆送股比例除过，
      因此须乘回 ex_date 及之后的累计 split_ratio，才能与新浪除权前的原始收盘价 prev_close 口径一致。
    """
    if not raw_bars:
        return []
    first_day = raw_bars[0]["day"]
    splits = [s for s in events if float(s.get("split_ratio") or 1.0) != 1.0]
    actions: list[dict[str, Any]] = []
    for ev in events:
        ex_date = ev["ex_date"]
        if ex_date <= first_day:
            continue
        prev = [b for b in raw_bars if b["day"] < ex_date]
        if not prev:
            continue
        prev_close = float(prev[-1]["close"])
        split_ratio = float(ev["split_ratio"]) if include_splits else 1.0
        dividend_factor = 1.0
        if ev["dividend"] > 0 and prev_close > 0:
            subsequent_split = (
                math.prod(float(s["split_ratio"]) for s in splits if s["ex_date"] >= ex_date)
                if include_splits
                else 1.0
            )
            raw_div = float(ev["dividend"]) * subsequent_split
            dividend_factor = (prev_close - raw_div) / prev_close
            if dividend_factor <= 0:
                continue
        if split_ratio == 1.0 and dividend_factor == 1.0:
            continue
        actions.append({"ex_date": ex_date, "split_ratio": split_ratio, "dividend_factor": round(dividend_factor, 8)})
    return actions


def _load_corporate_actions(
    inst: dict[str, Any],
    raw_bars: list[dict[str, Any]],
    source: str,
    mode: str,
    allow_network: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """返回 (复权参数, 复权状态)。取不到事件时不复权，并如实记录原因。"""
    status: dict[str, Any] = {"requested": mode, "applied": "raw", "events_used": 0, "source": None, "error": None}
    if mode == "raw" or inst["asset_type"] == "index":
        status["requested"] = "raw"
        return [], status
    if not source.startswith(("SINA_", "YAHOO_")):
        status["error"] = f"数据源 {source} 无公司行动数据"
        return [], status
    if not allow_network:
        status["error"] = "网络已禁用(--offline)"
        return [], status
    try:
        y_sym = yahoo_events_symbol_for(inst["symbol"])
        events = fetch_yahoo_corporate_actions(y_sym)
    except Exception as exc:  # noqa: BLE001 - 失败原因写入复权状态
        status["error"] = f"{type(exc).__name__}: {exc}"
        return [], status
    # 新浪 A 股为不复权原始价，需同时处理送转与分红；Yahoo 港美股价格已按拆股调整，只处理分红
    actions = build_corporate_actions(raw_bars, events, include_splits=source.startswith("SINA_"))
    if mode == "split":
        actions = [a for a in actions if a["split_ratio"] != 1.0]
    status.update(applied=mode, events_used=len(actions), source=f"YAHOO_EVENTS({y_sym})")
    return actions, status


def _load_single_series(
    inst: dict[str, Any],
    datalen: int = 450,
    as_of: str | None = None,
    allow_network: bool = True,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """返回 (bars, source, errors)。真实行情不可用时直接报错，绝不生成或回退合成数据。"""
    market = inst["market"]
    symbol = inst["symbol"]
    errors: list[str] = []

    if not allow_network:
        errors.append("网络已禁用(--offline)")
    else:
        try:
            if market == "CN":
                sina_code = inst.get("sina", "")
                if not sina_code.startswith(("sh", "sz", "bj")):
                    raise ValueError(f"无法映射新浪代码: {sina_code!r}")
                bars = fetch_sina_daily_bars(sina_code, datalen=datalen)
                src = "SINA_LIVE_DAILY"
            elif symbol in TENCENT_INDEX_SOURCES:
                t_code = TENCENT_INDEX_SOURCES[symbol]
                bars = fetch_tencent_index_daily_bars(t_code, datalen=datalen)
                src = f"TENCENT_INDEX_DAILY({t_code})"
            else:
                y_sym = yahoo_symbol_for(symbol)
                bars = fetch_yahoo_daily_bars(y_sym, datalen=datalen)
                src = f"YAHOO_DAILY({y_sym})"
            validate_bars(bars, f"{src}:{symbol}")  # 厂商数据也要过 OHLC 包络 / 时间单调 / 正价格校验
            if as_of:
                bars = [b for b in bars if b["day"] <= as_of[:10]]
            if len(bars) < MIN_LIVE_BARS:
                raise RuntimeError(f"历史长度不足 ({len(bars)} < {MIN_LIVE_BARS})")
            return bars, src, errors
        except Exception as exc:  # noqa: BLE001 - 失败原因必须记录并向上抛出
            errors.append(f"{type(exc).__name__}: {exc}")

    raise RuntimeError(f"无法获取 {symbol} 的真实行情（{'; '.join(errors)}），拒绝使用合成数据")


def split_incomplete_last_bar(
    bars: list[dict[str, Any]],
    market: str,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """按交易所时区与收盘时间拆出尚未收盘的最后一根日线，返回 (已收盘K线, 盘中K线或 None)。"""
    if not bars or is_daily_bar_complete(bars[-1]["day"], market, now):
        return bars, None
    last = bars[-1]
    tz_name = MARKET_SESSIONS[market][0]
    stamp = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S")
    live = {
        "day": last["day"],
        "open": float(last["open"]),
        "high": float(last["high"]),
        "low": float(last["low"]),
        "close": float(last["close"]),
        "prev": float(bars[-2]["close"]) if len(bars) >= 2 else float(last["open"]),
        "volume": float(last.get("volume") or 0.0) or None,
        "stamp": stamp,
        "complete": False,
        "session_closed": False,
    }
    return bars[:-1], live


def _fetch_vix(as_of: str | None, allow_network: bool) -> tuple[float | None, str]:
    """拉取 CBOE VIX 最近收盘值（Yahoo ^VIX）；拿不到就返回 None，绝不填默认值。"""
    if not allow_network:
        return None, "NOT_FETCHED(offline)"
    try:
        bars = fetch_yahoo_daily_bars("^VIX", range_str="3mo", datalen=None)
        if as_of:
            bars = [b for b in bars if b["day"] <= as_of[:10]]
        if not bars:
            return None, "UNAVAILABLE(empty)"
        return round(float(bars[-1]["close"]), 2), f"YAHOO_DAILY(^VIX,{bars[-1]['day']})"
    except Exception as exc:  # noqa: BLE001
        return None, f"UNAVAILABLE({type(exc).__name__})"


def load_bundle(
    request: AnalysisRequest,
    input_path: Path | None = None,
    allow_network: bool = True,
    now: datetime | None = None,
) -> MarketDataBundle:
    """统一多市场取数入口：先加载或获取标的与对应市场基准指数（先大盘、后个股），执行点位时间对齐与复权处理。"""
    if input_path is not None:
        raw_data = json.loads(Path(input_path).read_text(encoding="utf-8"))
        if isinstance(raw_data, dict) and "schema_version" in raw_data and "target" in raw_data:
            validate_bars(raw_data["target"]["bars"], "bundle.target.bars")
            if raw_data.get("benchmark") and raw_data["benchmark"].get("bars"):
                validate_bars(raw_data["benchmark"]["bars"], "bundle.benchmark.bars")
            return raw_data  # type: ignore[return-value]
        if isinstance(raw_data, list):
            inst = resolve_instrument(
                request.get("symbol", "000001.SH"),
                market=request.get("market"),
                asset_type=request.get("asset_type"),
                name=request.get("name"),
                is_st=bool(request.get("is_st", False)),
            )
            bars = [
                {
                    "day": str(r["day"])[:10],
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": float(r.get("volume") or 0.0),
                    "complete": bool(r.get("complete", True)),
                }
                for r in raw_data
            ]
            if request.get("as_of"):
                bars = [b for b in bars if b["day"] <= str(request["as_of"])[:10]]
            validate_bars(bars, "input_bars")
            if len(bars) < MIN_LIVE_BARS:
                raise RuntimeError(f"历史长度不足 ({len(bars)} < {MIN_LIVE_BARS})")
            intraday = None
            if not bars[-1]["complete"]:
                last = bars.pop()
                prev = bars[-1]["close"] if bars else last["open"]
                intraday = {**last, "prev": prev, "stamp": f"{last['day']} (未收盘)", "session_closed": False}
            # 本地K线数组没有公司行动数据，按原样使用并在复权状态中注明
            bars = apply_point_in_time_adjustment(bars, mode="raw", as_of_day=bars[-1]["day"])
            local_adjustment = {
                "requested": request.get("adjustment", "raw"),
                "applied": "raw",
                "events_used": 0,
                "source": None,
                "error": "本地文件未附带除权除息数据，按原始价格使用",
            }
            return {
                "schema_version": "5.2.0",
                "market": inst["market"],
                "target": {"instrument": inst, "bars": bars, "intraday": intraday},
                "benchmark": None,
                "extra_context": {
                    "vix": None,
                    "vix_source": "NOT_FETCHED(local_file)",
                    "earnings_date": None,
                    "is_st": bool(inst.get("is_st", False)),
                    "is_ipo_no_limit": bool(request.get("is_ipo_no_limit", False)),
                },
                "provenance": {
                    "source": f"LOCAL_FILE({Path(input_path).name})",
                    "as_of": bars[-1]["day"],
                    "errors": [],
                    "adjustment": "raw",
                    "adjustment_status": local_adjustment,
                },
            }

    inst = resolve_instrument(
        request.get("symbol", "000001.SH"),
        market=request.get("market"),
        asset_type=request.get("asset_type"),
        name=request.get("name"),
        is_st=bool(request.get("is_st", False)),
    )
    inst, instrument_meta = _enrich_instrument(inst, request, allow_network)
    market = inst["market"]
    as_of = request.get("as_of")
    datalen = FETCH_BARS

    target_bars, target_src, target_errors = _load_single_series(
        inst,
        datalen=datalen,
        as_of=as_of,
        allow_network=allow_network,
    )
    # 未收盘K线不得当作已收盘参与指标/预测计算（指定历史 as_of 时不涉及）
    intraday = None
    missing_session = None
    if not as_of:
        target_bars, intraday = split_incomplete_last_bar(target_bars, market, now)
        # 已收盘的最近交易日不在数据里：数据源尚未更新或标的停牌，不补数，只披露
        latest_closed = latest_closed_session(market, now)
        if target_bars[-1]["day"] < latest_closed:
            missing_session = latest_closed
    adj_mode = request.get("adjustment", "total_return" if inst["asset_type"] != "index" else "raw")
    actions, adjustment_status = _load_corporate_actions(inst, target_bars, target_src, adj_mode, allow_network)
    target_bars = apply_point_in_time_adjustment(
        target_bars,
        corporate_actions=actions,
        mode=adjustment_status["applied"],
        as_of_day=target_bars[-1]["day"],
    )

    # 先大盘、后个股：若当前标的不是该市场默认基准指数，则同步加载基准指数（截至标的最后一根已收盘K线）
    bench_cfg = DEFAULT_BENCHMARKS[market]
    bench_symbol = request.get("benchmark_symbol") or bench_cfg["symbol"]
    benchmark_payload: dict[str, Any] | None = None
    if bench_symbol.upper() != inst["symbol"].upper():
        bench_inst = resolve_instrument(bench_symbol, market=market, asset_type="index" if market != "US" else "etf")
        bench_bars, bench_src, bench_errors = _load_single_series(
            bench_inst,
            datalen=datalen,
            as_of=target_bars[-1]["day"],
            allow_network=allow_network,
        )
        benchmark_payload = {
            "instrument": bench_inst,
            "bars": bench_bars,
            "source": bench_src,
            "errors": bench_errors,
        }

    vix_value, vix_source = (None, "NOT_APPLICABLE")
    if market == "US":
        vix_value, vix_source = _fetch_vix(as_of or target_bars[-1]["day"], allow_network)

    return {
        "schema_version": "5.2.0",
        "market": market,
        "target": {
            "instrument": inst,
            "bars": target_bars,
            "intraday": intraday,
        },
        "benchmark": benchmark_payload,
        "extra_context": {
            "vix": vix_value,
            "vix_source": vix_source,
            "earnings_date": None,
            "earnings_date_source": "NOT_AVAILABLE",
            "is_st": bool(inst.get("is_st", False)),
            "is_ipo_no_limit": bool(request.get("is_ipo_no_limit", False)),
        },
        "provenance": {
            "source": target_src,
            "as_of": target_bars[-1]["day"],
            "adjustment": adjustment_status["applied"],
            "adjustment_status": adjustment_status,
            "errors": target_errors,
            "intraday_excluded_day": intraday["day"] if intraday else None,
            "missing_closed_session": missing_session,
            "instrument_meta": instrument_meta,
        },
    }
