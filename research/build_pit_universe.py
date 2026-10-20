#!/usr/bin/env python3
"""Build point-in-time liquid universes from cached real bars.

At each rebalance day (every N benchmark trading days) rank symbols by trailing
60-session average turnover (close x volume) using only bars strictly before
that day, then hold the top-N membership until the next rebalance. This removes
look-ahead from the universe selection itself.

Survivorship bias remains: only currently listed symbols are cached. Historical
ST status is unavailable. Both limitations are reported.
"""
from __future__ import annotations

import argparse
import json
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path

from common import ART, BENCHMARKS, CACHE, load_cache, load_bars

WARMUP_BARS = 420
TRAIL_BARS = 60


def build_market(market: str, start: str, end: str, top_n: int, rebalance: int) -> dict:
    bench = load_bars(market, BENCHMARKS[market]["symbol"])
    days = [b["day"] for b in bench if start <= b["day"] <= end]
    if not days:
        raise RuntimeError(f"no benchmark days for {market} in [{start}, {end}]")
    # Rebalance on the first eligible day, then every `rebalance` sessions.
    rebal_days = [days[i] for i in range(0, len(days), rebalance)]
    specs = []
    for i, d in enumerate(rebal_days):
        start_i = days.index(d)
        next_i = days.index(rebal_days[i + 1]) if i + 1 < len(rebal_days) else len(days)
        specs.append({"rebalance_day": d, "start_day": days[start_i], "end_day": days[next_i - 1]})
    rankings: list[list[tuple[float, str]]] = [[] for _ in specs]

    files = sorted((CACHE / market).glob("*.json.gz"))
    use: list[Path] = []
    skip = {BENCHMARKS[m]["symbol"] for m in BENCHMARKS} | {"^VIX"}
    for path in files:
        if path.name.replace(".json.gz", "").replace("_", "^") in skip:
            continue
        use.append(path)
    for path in use:
        try:
            payload = json.loads(__import__("gzip").open(path, "rt", encoding="utf-8").read())
        except Exception:  # noqa: BLE001 - corrupt cache is reported by MANIFEST validation
            continue
        if payload.get("asset_type") != "equity":
            continue
        bars = payload["bars"]
        sym_days = [b["day"] for b in bars]
        turnover = [float(b["close"]) * float(b.get("volume") or 0.0) for b in bars]
        for j, spec in enumerate(specs):
            pos = bisect_left(sym_days, spec["rebalance_day"])
            if pos < WARMUP_BARS:
                continue
            window = turnover[max(0, pos - TRAIL_BARS):pos]
            if not window or sum(window) <= 0:
                continue
            rankings[j].append((sum(window) / len(window), payload["symbol"]))
    periods = []
    for j, spec in enumerate(specs):
        ranked = sorted(rankings[j], reverse=True)[:top_n]
        periods.append({
            **spec,
            "symbols": [s for _, s in ranked],
            "turnover": [round(t, 2) for t, _ in ranked],
        })
    return {
        "market": market,
        "benchmark": BENCHMARKS[market],
        "top_n": top_n,
        "rebalance_sessions": rebalance,
        "eligible_files": len(use),
        "periods": periods,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-04")
    ap.add_argument("--end", default="2026-10-16")
    ap.add_argument("--top-n", type=int, default=1500)
    ap.add_argument("--rebalance", type=int, default=60)
    ap.add_argument("--output", type=Path, default=ART / "pit_universe.json")
    args = ap.parse_args()
    out = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start": args.start,
        "end": args.end,
        "ranking": "trailing_60_session_mean(close*volume), strictly before rebalance_day",
        "survivorship_bias": "PRESENT(current listings only)",
        "st_history": "UNAVAILABLE(current name only)",
        "markets": {},
    }
    for market in ("CN", "HK", "US"):
        info = build_market(market, args.start, args.end, args.top_n, args.rebalance)
        out["markets"][market] = info
        sizes = [len(p["symbols"]) for p in info["periods"]]
        print(f"{market}: files={info['eligible_files']} periods={len(sizes)} "
              f"size_min={min(sizes)} size_max={max(sizes)} "
              f"first={info['periods'][0]['start_day']} last={info['periods'][-1]['end_day']}")
    args.output.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()