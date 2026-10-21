#!/usr/bin/env python3
"""留出集 H1（2017-10 ~ 2020-11）数据覆盖报告（回测与验收口径调整方案第 5 步）。

只统计数据是否存在、是否完整（K线先按线上口径剔除 Yahoo 占位K线）：每期股票池规模、成分股在该期的K线覆盖率、基准与 VIX 覆盖、
除权除息事件、未收盘或非正价格K线、零成交日。不计算任何收益、信号或规则结果，不会读取 H1 的结论。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from common import ART, BENCHMARKS, CACHE, VIX_SYMBOL, clean_bars, load_bars, load_cache

H1_UNIVERSE = ART / "pit_universe_h1.json"
OUT = ART / "h1_coverage.json"
MIN_BAR_COVERAGE = 0.95  # 成分股在一期内至少有 95% 的基准交易日有K线，才算"覆盖完整"


def _quarter(day: str) -> str:
    return f"{day[:4]}Q{(int(day[5:7]) - 1) // 3 + 1}"


def _max_gap_days(days: list[str]) -> int:
    ds = [date.fromisoformat(d) for d in days]
    return max(((b - a).days for a, b in zip(ds, ds[1:])), default=0)


def market_report(market: str, info: dict) -> dict:
    bench = load_bars(market, BENCHMARKS[market]["symbol"])
    b_days = [b["day"] for b in bench]
    lo, hi = info["periods"][0]["start_day"], info["periods"][-1]["end_day"]
    h1_days = b_days[bisect_left(b_days, lo):bisect_right(b_days, hi)]

    vix_cov = None
    if market == "US":
        try:
            vix_days = {b["day"] for b in load_bars("US", VIX_SYMBOL)}
            vix_cov = round(sum(1 for d in h1_days if d in vix_days) / len(h1_days) * 100, 2)
        except Exception:  # noqa: BLE001 - 缺失如实报告
            vix_cov = None

    members = sorted({s for p in info["periods"] for s in p["symbols"]})
    sym_stats: dict[str, dict] = {}
    for sym in members:
        try:
            payload = load_cache(market, sym)
        except Exception:  # noqa: BLE001
            sym_stats[sym] = {"missing_cache": True}
            continue
        raw_h1 = sum(1 for b in payload["bars"] if lo <= b["day"] <= hi)
        bars = clean_bars(payload)  # 与线上、回测同口径：Yahoo 占位K线（成交量 0 且四价相同）已剔除
        days = [b["day"] for b in bars]
        h1_bars = [b for b in bars if lo <= b["day"] <= hi]
        events = [e for e in (payload.get("events") or []) if lo <= str(e.get("ex_date") or e.get("day") or "")[:10] <= hi]
        sym_stats[sym] = {
            "days": set(days),
            "bad_bars": sum(1 for b in h1_bars if not b.get("complete", True) or min(float(b[k]) for k in ("open", "high", "low", "close")) <= 0),
            "zero_volume_days": sum(1 for b in h1_bars if not float(b.get("volume") or 0.0)),
            "h1_bars": len(h1_bars),
            "h1_placeholder_removed": raw_h1 - len(h1_bars),
            "h1_events": len(events),
        }

    periods = []
    by_quarter: dict[str, dict] = defaultdict(lambda: {"periods": 0, "pool_size": [], "complete_pct": []})
    for p in info["periods"]:
        p_days = b_days[bisect_left(b_days, p["start_day"]):bisect_right(b_days, p["end_day"])]
        complete = 0
        for sym in p["symbols"]:
            st = sym_stats.get(sym) or {}
            if st.get("missing_cache"):
                continue
            covered = sum(1 for d in p_days if d in st["days"])
            if p_days and covered / len(p_days) >= MIN_BAR_COVERAGE:
                complete += 1
        row = {
            "start_day": p["start_day"], "end_day": p["end_day"], "bench_days": len(p_days),
            "pool_size": len(p["symbols"]),
            "complete_bar_coverage": complete,
            "complete_bar_coverage_pct": round(complete / max(1, len(p["symbols"])) * 100, 2),
        }
        periods.append(row)
        q = by_quarter[_quarter(p["start_day"])]
        q["periods"] += 1
        q["pool_size"].append(row["pool_size"])
        q["complete_pct"].append(row["complete_bar_coverage_pct"])

    ok = [s for s in sym_stats.values() if not s.get("missing_cache")]
    return {
        "h1_first_day": lo,
        "h1_last_day": hi,
        "benchmark": {
            "symbol": BENCHMARKS[market]["symbol"],
            "h1_days": len(h1_days),
            "max_calendar_gap_days": _max_gap_days(h1_days),
            "first_cached_day": b_days[0],
            "warmup_bars_before_h1": bisect_left(b_days, lo),
        },
        "vix_coverage_pct": vix_cov,
        "members_total": len(members),
        "members_missing_cache": sum(1 for s in sym_stats.values() if s.get("missing_cache")),
        "members_with_events_in_h1": sum(1 for s in ok if s["h1_events"] > 0),
        "incomplete_or_nonpositive_bars": sum(s["bad_bars"] for s in ok),
        "placeholder_bars_removed_in_h1": sum(s["h1_placeholder_removed"] for s in ok),
        "placeholder_share_of_raw_h1_bars_pct": round(
            sum(s["h1_placeholder_removed"] for s in ok)
            / max(1, sum(s["h1_bars"] + s["h1_placeholder_removed"] for s in ok)) * 100, 3),
        "zero_volume_bar_share_pct": round(
            sum(s["zero_volume_days"] for s in ok) / max(1, sum(s["h1_bars"] for s in ok)) * 100, 3),
        "periods": periods,
        "by_quarter": {
            q: {"periods": v["periods"], "pool_size_min": min(v["pool_size"]),
                "complete_bar_coverage_pct_min": min(v["complete_pct"])}
            for q, v in sorted(by_quarter.items())
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=Path, default=H1_UNIVERSE)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    uni = json.loads(args.universe.read_text(encoding="utf-8"))
    report = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "universe_file": args.universe.name,
        "universe_sha256": hashlib.sha256(args.universe.read_bytes()).hexdigest(),
        "cache_manifest_sha256": hashlib.sha256((CACHE / "MANIFEST.json").read_bytes()).hexdigest(),
        "note": "只统计数据覆盖，不含任何收益、信号或规则结果",
        "survivorship_bias": uni.get("survivorship_bias"),
        "st_history": uni.get("st_history"),
        "markets": {m: market_report(m, info) for m, info in uni["markets"].items()},
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for m, r in report["markets"].items():
        cov = [p["complete_bar_coverage_pct"] for p in r["periods"]]
        print(f"{m}: H1 {r['h1_first_day']}~{r['h1_last_day']} bench_days={r['benchmark']['h1_days']} "
              f"max_gap={r['benchmark']['max_calendar_gap_days']}d members={r['members_total']} "
              f"missing_cache={r['members_missing_cache']} complete_cov_min={min(cov)}% "
              f"placeholder_removed={r['placeholder_share_of_raw_h1_bars_pct']}% zero_vol={r['zero_volume_bar_share_pct']}% "
              f"vix={r['vix_coverage_pct']}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
