#!/usr/bin/env python3
"""Cache real daily bars + corporate actions for long-horizon point-in-time backtests.

Networking is required; run this from a normal terminal (the managed sandbox may
block vendor domains). Never fabricates or backfills bars: failures are logged
and the symbol is skipped.

Sources
- CN prices: Sina unadjusted daily kline (datalen=2600, ~2016-01 onward)
- CN events / HK+US prices+events: Yahoo chart API (range=10y, div+splits)
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import random
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_SCRIPTS = ROOT / "kline-structure-chart" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

from contracts import validate_bars  # noqa: E402
from market_rules import is_daily_bar_complete, yahoo_events_symbol_for, yahoo_symbol_for  # noqa: E402

ART = ROOT / "artifacts" / "long_backtest"
CACHE = ART / "cache"
UNIVERSE = ART / "universe.csv"
FAILURES = ART / "cache_failures.jsonl"
MANIFEST = CACHE / "MANIFEST.json"
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Referer": "https://finance.sina.com.cn/",
}
SINA = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
HISTORY_BARS = 2600
BENCH_ROWS = [
    {"symbol": "000300.SH", "market": "CN", "asset_type": "index", "name": "沪深300"},
    {"symbol": "HSI.HK", "market": "HK", "asset_type": "index", "name": "恒生指数"},
    {"symbol": "SPY.US", "market": "US", "asset_type": "etf", "name": "标普500 ETF (SPY)"},
    {"symbol": "^VIX", "market": "US", "asset_type": "index", "name": "VIX"},
]

_print_lock = threading.Lock()
_rate_lock = threading.Lock()
_last_request: dict[str, float] = {}


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _throttle(host: str, min_interval: float) -> None:
    with _rate_lock:
        wait = _last_request.get(host, 0.0) + min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[host] = time.monotonic()


def http_get(url: str, host: str, min_interval: float, encoding: str = "utf-8", timeout: int = 40) -> str:
    last: Exception | None = None
    for attempt in range(4):
        _throttle(host, min_interval)
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode(encoding, "ignore")
        except Exception as exc:  # noqa: BLE001 - log and retry, never synthesize
            last = exc
            time.sleep(1.5 * (2**attempt) + random.uniform(0, 0.4))
    raise RuntimeError(f"{type(last).__name__}: {last}")


def sina_code(symbol: str) -> str:
    code, _, suffix = symbol.upper().partition(".")
    return {"SH": "sh", "SZ": "sz", "BJ": "bj"}[suffix] + code


def cache_path(market: str, symbol: str) -> Path:
    return CACHE / market / f"{symbol.replace('^', '_')}.json.gz"


def fetch_sina_bars(symbol: str) -> list[dict]:
    url = (SINA + "CN_MarketData.getKLineData?"
           f"symbol={sina_code(symbol)}&scale=240&ma=no&datalen={HISTORY_BARS}")
    rows = json.loads(http_get(url, "money.finance.sina.com.cn", 0.15))
    if not isinstance(rows, list) or len(rows) < 60:
        raise RuntimeError(f"Sina daily unavailable ({len(rows) if isinstance(rows, list) else 'non-list'})")
    bars = [
        {
            "day": str(r["day"])[:10],
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r.get("volume") or 0.0),
            "complete": True,
        }
        for r in rows
    ]
    return bars


def _parse_yahoo(payload: dict, market: str) -> tuple[list[dict], list[dict]]:
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError("Yahoo chart empty")
    r0 = result[0]
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo((r0.get("meta") or {}).get("exchangeTimezoneName") or "UTC")
    except Exception:  # noqa: BLE001
        tz = timezone.utc
    stamps = r0.get("timestamp") or []
    quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs, lows, closes, vols = (quote.get(k) or [] for k in ("open", "high", "low", "close", "volume"))
    bars: list[dict] = []
    seen: set[str] = set()
    for ts, o, h, l, c, v in zip(stamps, opens, highs, lows, closes, vols):
        if None in (o, h, l, c):
            continue
        day = datetime.fromtimestamp(int(ts), tz=tz).strftime("%Y-%m-%d")
        if day in seen:
            continue
        seen.add(day)
        of, hf, lf, cf = float(o), float(h), float(l), float(c)
        if min(of, hf, lf, cf) <= 0:
            continue
        bars.append({
            "day": day,
            "open": round(of, 4),
            "high": round(max(of, hf, lf, cf), 4),
            "low": round(min(of, hf, lf, cf), 4),
            "close": round(cf, 4),
            "volume": float(v or 0.0),
            "complete": True,
        })
    bars.sort(key=lambda b: b["day"])

    events = r0.get("events") or {}
    actions: list[dict] = []
    for item in (events.get("splits") or {}).values():
        num, den = float(item.get("numerator") or 0), float(item.get("denominator") or 0)
        if num > 0 and den > 0:
            actions.append({
                "ex_date": datetime.fromtimestamp(int(item["date"]), tz=tz).strftime("%Y-%m-%d"),
                "split_ratio": num / den,
                "dividend": 0.0,
            })
    for item in (events.get("dividends") or {}).values():
        amount = float(item.get("amount") or 0)
        if amount > 0:
            actions.append({
                "ex_date": datetime.fromtimestamp(int(item["date"]), tz=tz).strftime("%Y-%m-%d"),
                "split_ratio": 1.0,
                "dividend": amount,
            })
    actions.sort(key=lambda a: a["ex_date"])
    return bars, actions


def fetch_yahoo(symbol: str) -> tuple[list[dict], list[dict], str]:
    y_sym = "^VIX" if symbol == "^VIX" else yahoo_symbol_for(symbol)
    encoded = urllib.parse.quote(y_sym, safe="^.-")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
           f"?interval=1d&range=10y&events=div%2Csplits")
    payload = json.loads(http_get(url, "query1.finance.yahoo.com", 0.25))
    bars, actions = _parse_yahoo(payload, symbol.rsplit(".", 1)[-1])
    return bars, actions, f"YAHOO_DAILY({y_sym})+EVENTS(10y)"


def fetch_cn_events(symbol: str) -> tuple[list[dict], str]:
    try:
        ev_sym = yahoo_events_symbol_for(symbol)
    except ValueError:
        return [], "NO_YAHOO_EVENTS(BJ)"
    encoded = urllib.parse.quote(ev_sym, safe="^.-")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
           f"?interval=1d&range=10y&events=div%2Csplits")
    payload = json.loads(http_get(url, "query1.finance.yahoo.com", 0.25))
    _, actions = _parse_yahoo(payload, "CN")
    return actions, f"YAHOO_EVENTS({ev_sym})"


def drop_incomplete_tail(bars: list[dict], market: str) -> tuple[list[dict], str | None]:
    if not bars:
        return bars, None
    last = bars[-1]["day"]
    if is_daily_bar_complete(last, market):
        return bars, None
    return bars[:-1], last


def cache_one(row: dict, force: bool) -> dict:
    symbol, market = row["symbol"], row["market"]
    path = cache_path(market, symbol)
    if path.exists() and not force:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                old = json.load(fh)
            return {"symbol": symbol, "status": "cached", "bars": len(old["bars"]), "events": len(old.get("events") or [])}
        except Exception:  # noqa: BLE001 - corrupt cache gets refetched
            pass

    if market == "CN" and row.get("asset_type", "equity") == "equity":
        bars = fetch_sina_bars(symbol)
        events, event_src = fetch_cn_events(symbol)
        src = f"SINA_LIVE_DAILY+{event_src}"
    elif market == "CN":
        bars = fetch_sina_bars(symbol)
        events, event_src = [], "NOT_APPLICABLE(index)"
        src = "SINA_LIVE_DAILY(index)"
    else:
        bars, events, src = fetch_yahoo(symbol)
        event_src = src

    bars, dropped = drop_incomplete_tail(bars, market)
    validate_bars(bars, f"{src}:{symbol}")
    if len(bars) < 60:
        raise RuntimeError(f"insufficient bars ({len(bars)})")

    payload = {
        "schema_version": "1.0",
        "symbol": symbol,
        "market": market,
        "asset_type": row.get("asset_type", "equity"),
        "name": row.get("name", ""),
        "source": src,
        "events_source": event_src,
        "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "first_day": bars[0]["day"],
        "last_day": bars[-1]["day"],
        "dropped_incomplete_day": dropped,
        "bars": bars,
        "events": events,
    }
    canonical = json.dumps({"bars": bars, "events": events}, sort_keys=True, separators=(",", ":"))
    payload["sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.gz.tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)
    return {"symbol": symbol, "status": "ok", "bars": len(bars), "events": len(events),
            "first_day": bars[0]["day"], "last_day": bars[-1]["day"]}


def load_universe(limit: int) -> list[dict]:
    with UNIVERSE.open(encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["list_eligible"] == "1"]
    rows.sort(key=lambda r: (r["market"], -float(r["rank_value"] or 0)))
    out: list[dict] = []
    for market in ("CN", "HK", "US"):
        sub = [r for r in rows if r["market"] == market]
        out.extend(sub[:limit] if limit else sub)
    return out


def write_manifest(rows: list[dict]) -> None:
    files = []
    for market in ("CN", "HK", "US"):
        for path in sorted((CACHE / market).glob("*.json.gz")):
            try:
                with gzip.open(path, "rt", encoding="utf-8") as fh:
                    d = json.load(fh)
                files.append({
                    "file": str(path.relative_to(CACHE)),
                    "symbol": d["symbol"],
                    "source": d["source"],
                    "first_day": d["first_day"],
                    "last_day": d["last_day"],
                    "bars": len(d["bars"]),
                    "events": len(d.get("events") or []),
                    "sha256": d["sha256"],
                    "bytes": path.stat().st_size,
                })
            except Exception as exc:  # noqa: BLE001
                files.append({"file": str(path.relative_to(CACHE)), "error": str(exc)})
    MANIFEST.write_text(json.dumps({
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "universe": str(UNIVERSE),
        "note": "Real vendor data only. Current-listing universe -> survivorship bias present.",
        "counts": {"files": len(files)},
        "files": files,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"manifest: {len(files)} files -> {MANIFEST}")


def main() -> None:
    global UNIVERSE
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=Path, default=UNIVERSE)
    ap.add_argument("--limit", type=int, default=0, help="per-market row cap (0=all eligible)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--benchmarks", action="store_true", help="also cache CN/HK/US benchmarks and ^VIX")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    UNIVERSE = args.universe
    rows = load_universe(args.limit)
    log(f"universe rows={len(rows)} workers={args.workers}")
    if args.dry_run:
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    ok = cached = failed = 0
    started = time.time()
    with FAILURES.open("a", encoding="utf-8") as fail_fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(cache_one, row, args.force): row for row in rows}
        for i, fut in enumerate(as_completed(futures), 1):
            row = futures[fut]
            try:
                res = fut.result()
                if res["status"] == "cached":
                    cached += 1
                else:
                    ok += 1
                if i % 25 == 0 or i == len(rows):
                    elapsed = time.time() - started
                    log(f"[{i}/{len(rows)}] ok={ok} cached={cached} failed={failed} "
                        f"elapsed={elapsed:.0f}s rate={i/max(elapsed,1):.2f}/s")
            except Exception as exc:  # noqa: BLE001 - record and continue
                failed += 1
                fail_fh.write(json.dumps({
                    "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "symbol": row["symbol"], "market": row["market"], "error": f"{type(exc).__name__}: {exc}",
                }, ensure_ascii=False) + "\n")
                fail_fh.flush()
                log(f"FAIL {row['symbol']}: {type(exc).__name__}: {exc}")
    if args.benchmarks:
        for row in BENCH_ROWS:
            try:
                res = cache_one(row, args.force)
                log(f"BENCH {row['symbol']}: {res['status']} bars={res.get('bars')} events={res.get('events')}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                with FAILURES.open("a", encoding="utf-8") as fail_fh:
                    fail_fh.write(json.dumps({
                        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "symbol": row["symbol"], "market": row["market"], "error": f"{type(exc).__name__}: {exc}",
                    }, ensure_ascii=False) + "\n")
                log(f"FAIL BENCH {row['symbol']}: {type(exc).__name__}: {exc}")
    write_manifest(rows)
    log(f"done ok={ok} cached={cached} failed={failed} elapsed={time.time()-started:.0f}s")


if __name__ == "__main__":
    main()