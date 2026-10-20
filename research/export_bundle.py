#!/usr/bin/env python3
"""Export a point-in-time bundle JSON from the real cache for offline rendering/audit."""
from __future__ import annotations

import argparse
import json
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

from common import BENCHMARKS, VIX_SYMBOL, adjusted_bars, load_bars, load_cache

WINDOW_BARS = 760


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["CN", "HK", "US"])
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    payload = load_cache(args.market, args.symbol)
    _, bars = adjusted_bars(payload, mode="total_return")
    days = [b["day"] for b in bars]
    t = bisect_right(days, args.as_of) - 1
    if t < 420:
        raise SystemExit(f"not enough history before {args.as_of}: index {t}")
    from market_rules import is_st_instrument, rescale_bars_as_of

    lo = max(0, t - WINDOW_BARS + 1)
    pit = rescale_bars_as_of(bars[lo:t + 1], t - lo)
    bench_payload = load_cache(args.market, BENCHMARKS[args.market]["symbol"])
    bench = bench_payload["bars"]
    b_days = [b["day"] for b in bench]
    b_hi = bisect_right(b_days, args.as_of)
    bench = bench[max(0, b_hi - WINDOW_BARS):b_hi]
    vix = None
    vix_src = "NOT_APPLICABLE"
    if args.market == "US":
        try:
            vix_bars = load_bars("US", VIX_SYMBOL)
            vix = next((float(b["close"]) for b in reversed(vix_bars) if b["day"] <= args.as_of), None)
            vix_src = f"CACHE(^VIX,{args.as_of})" if vix is not None else "CACHE_MISS"
        except Exception:  # noqa: BLE001
            vix_src = "CACHE_MISS"

    sym_name = str(payload.get("name") or args.symbol)
    sym_is_st = is_st_instrument(args.symbol, name=sym_name)
    inst = {"symbol": args.symbol, "market": args.market, "asset_type": "equity",
            "name": sym_name, "currency": {"CN": "CNY", "HK": "HKD", "US": "USD"}[args.market],
            "is_st": sym_is_st}
    bundle = {
        "schema_version": "5.3.0",
        "market": args.market,
        "target": {"instrument": inst, "bars": pit, "intraday": None},
        "benchmark": {
            "instrument": {"symbol": BENCHMARKS[args.market]["symbol"], "market": args.market,
                           "asset_type": BENCHMARKS[args.market]["asset_type"],
                           "name": BENCHMARKS[args.market]["name"], "is_st": False},
            "bars": bench, "source": bench_payload.get("source", f"CACHED_{args.market}_BENCHMARK"), "errors": [],
        },
        "extra_context": {"vix": vix, "vix_source": vix_src, "earnings_date": None,
                          "earnings_date_source": "NOT_AVAILABLE", "is_st": sym_is_st, "is_ipo_no_limit": False},
        "provenance": {
            "source": payload.get("source", f"CACHED_{args.market}_DAILY"),
            "as_of": args.as_of,
            "adjustment": "total_return",
            "adjustment_status": {"requested": "total_return", "applied": "total_return",
                                  "events_used": len(payload.get("events") or []),
                                  "source": payload.get("events_source")},
            "errors": [], "intraday_excluded_day": None, "instrument_meta": {},
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.out} target_bars={len(pit)} bench_bars={len(bench)} as_of={args.as_of} "
          f"exported_at={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")


if __name__ == "__main__":
    main()