#!/usr/bin/env python3
"""Development-set (2021-01-01 .. 2024-12-09) diagnostic & mechanism study.

Strictly inspects DEV split only (with 15-trading-day embargo before 2025) first,
then evaluates the single frozen rule on VAL (2025-01-02 .. 2025-06-09) without
touching TEST.
"""
from __future__ import annotations

import json
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from aggregate import SPLITS, _non_overlapping_episodes, wilson
from common import ART, BENCHMARKS, adjusted_bars, load_bars, load_cache
from run_backtest import make_pit_slice

SKILL_SCRIPTS = Path(__file__).resolve().parent.parent / "kline-structure-chart" / "scripts"
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

from evaluation import label_triple_barrier_event  # noqa: E402


def load_completed_signals(run_root: Path, market: str) -> list[dict]:
    shard_dir = run_root / market / "shards"
    rows: list[dict] = []
    if not shard_dir.exists():
        return rows
    for p in sorted(shard_dir.glob("shard_*.jsonl")):
        # Allow reading completed or large active shards for dev diagnostics
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line.startswith("{") or not line.endswith("}"):
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("__error__") or not r.get("symbol"):
                continue
            rows.append(r)
    return rows


def summarize_subset(rows: list[dict], label: str, field: str = "net_return") -> None:
    sub = [r for r in rows if r.get(field) is not None]
    n = len(sub)
    if n == 0:
        print(f"  {label:42s}: n=0")
        return
    pos = sum(1 for r in sub if float(r[field]) > 0)
    eps = _non_overlapping_episodes(sub)
    ep_pos = sum(1 for r in eps if float(r[field]) > 0)
    mean_ret = sum(float(r[field]) for r in sub) / n * 100
    ci = wilson(pos, n)
    ep_ci = wilson(ep_pos, len(eps))
    outcomes = Counter(r.get("outcome") for r in sub)
    tp = outcomes.get("TP_BARRIER", 0)
    sl = sum(v for k, v in outcomes.items() if str(k).startswith("SL_BARRIER"))
    tr = outcomes.get("TRAILING_PROFIT_STOP", 0)
    tm = outcomes.get("TIME_BARRIER", 0)
    print(
        f"  {label:42s}: n={n:6d} (ep={len(eps):5d}) | "
        f"net>0={pos/n*100:5.2f}% CI={ci} | ep_net>0={ep_pos/len(eps)*100 if eps else 0:5.2f}% epCI={ep_ci} | "
        f"mean={mean_ret:+6.2f}% | TP={tp/n*100:4.1f}% SL={sl/n*100:4.1f}% TR={tr/n*100:4.1f}% TM={tm/n*100:4.1f}%"
    )


def main() -> None:
    run_root = ART / "runs" / "baseline_v1"
    dev_lo, dev_hi = SPLITS["dev"]
    val_lo, val_hi = SPLITS["val"]

    for market in ("CN", "HK", "US"):
        rows = load_completed_signals(run_root, market)
        dev = [r for r in rows if dev_lo <= r["day"] <= dev_hi]
        val = [r for r in rows if val_lo <= r["day"] <= val_hi]
        print(f"\n==================== {market} (total={len(rows)}, dev={len(dev)}, val={len(val)}) ====================")
        if not dev:
            continue
        summarize_subset(dev, "DEV all long signals (next_open)")
        summarize_subset([r for r in dev if r.get("fill")], "DEV filled inside entry_zone")
        if any("limit_n5_fill" in r for r in dev):
            summarize_subset(
                [r for r in dev if r.get("limit_n5_fill")],
                "DEV limit_n5 filled",
                field="limit_n5_net_return",
            )
        summarize_subset([r for r in dev if r["action"] == "LONG_CONFIRMED"], "DEV LONG_CONFIRMED")
        summarize_subset([r for r in dev if r["action"] == "WATCH_LONG_SETUP"], "DEV WATCH_LONG_SETUP")
        summarize_subset([r for r in dev if r.get("setup_type") == "LEFT_SIDE_PULLBACK"], "DEV LEFT_SIDE_PULLBACK")
        summarize_subset([r for r in dev if r.get("setup_type") == "RIGHT_SIDE_BREAKOUT"], "DEV RIGHT_SIDE_BREAKOUT")

        for rs_min in (35, 50, 60, 70, 80):
            summarize_subset(
                [r for r in dev if float(r.get("regime_score") or 0) >= rs_min and r.get("fill")],
                f"DEV fill & bench_regime>={rs_min}",
            )
        for sc_min in (60, 65, 70, 75, 80, 85):
            summarize_subset(
                [r for r in dev if float(r.get("rule_score") or 0) >= sc_min and r.get("fill")],
                f"DEV fill & rule_score>={sc_min}",
            )
        for rr_band in ((1.2, 1.35), (1.35, 1.6), (1.6, 2.0), (2.0, 99.0)):
            summarize_subset(
                [r for r in dev if rr_band[0] <= float(r.get("rr") or 0) < rr_band[1] and r.get("fill")],
                f"DEV fill & rr in [{rr_band[0]},{rr_band[1]})",
            )
        for rel_band in ((0.0, 0.96), (0.96, 1.00), (1.00, 1.04), (1.04, 9.0)):
            summarize_subset(
                [
                    r for r in dev
                    if r.get("relative_strength") is not None
                    and rel_band[0] <= float(r["relative_strength"]) < rel_band[1]
                    and r.get("fill")
                ],
                f"DEV fill & RS in [{rel_band[0]},{rel_band[1]})",
            )


if __name__ == "__main__":
    main()
