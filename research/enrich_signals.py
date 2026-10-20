#!/usr/bin/env python3
"""Enrich existing shard JSONL records with exit_day and 5-day limit-order outcomes.

For shards generated before `run_backtest.py` added `exit_day` and `limit_n5_*`,
this script reads the cached bars once per symbol and computes the exact
5-session limit-order fill and barrier outcome at `entry_high` without re-running
`analyze_bundle`.
"""
from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

from common import ART, BENCHMARKS, adjusted_bars, load_bars, load_cache
from run_backtest import make_pit_slice

SKILL_SCRIPTS = Path(__file__).resolve().parent.parent / "kline-structure-chart" / "scripts"
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

from evaluation import label_triple_barrier_event  # noqa: E402


def enrich_market(run_root: Path, market: str, horizon: int = 15) -> tuple[int, int]:
    shard_dir = run_root / market / "shards"
    if not shard_dir.exists():
        return 0, 0
    done_files = sorted(shard_dir.glob("shard_*.done"))
    bench_days = [b["day"] for b in load_bars(market, BENCHMARKS[market]["symbol"])]

    updated_shards = 0
    updated_records = 0
    for done_path in done_files:
        jsonl_path = done_path.with_suffix(".jsonl")
        if not jsonl_path.exists():
            continue
        lines = [ln for ln in jsonl_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        rows = [json.loads(ln) for ln in lines]
        if not rows or all(r.get("__error__") or "limit_n5_fill" in r for r in rows):
            continue

        by_sym: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            if not r.get("__error__") and r.get("symbol"):
                by_sym[r["symbol"]].append(r)

        sym_cache: dict[str, tuple[dict, list[dict], dict[str, int]]] = {}
        for sym in by_sym:
            payload = load_cache(market, sym)
            _, bars = adjusted_bars(payload, mode="total_return")
            idx = {b["day"]: i for i, b in enumerate(bars)}
            sym_cache[sym] = (payload, bars, idx)

        new_rows: list[dict] = []
        for r in rows:
            if r.get("__error__") or "limit_n5_fill" in r:
                new_rows.append(r)
                continue
            sym = r["symbol"]
            day = r["day"]
            payload, bars, idx = sym_cache[sym]
            t = idx[day]
            pit, cut = make_pit_slice(bars, t, horizon)
            b_hi = bisect_right(bench_days, day)
            next_bench_day = bench_days[b_hi] if b_hi < len(bench_days) else None
            is_stale = bool(next_bench_day and pit[cut + 1]["day"] > next_bench_day)

            entry_low, entry_high = float(r["entry_zone"][0]), float(r["entry_zone"][1])
            stop = float(r["stop"])
            tp1 = float(r["tp1"])

            res_open = label_triple_barrier_event(
                pit,
                entry_idx=cut + 1,
                tp_price=tp1,
                sl_price=stop,
                horizon_bars=horizon,
                market=market,
                asset_type="equity",
                symbol=sym,
                name=payload.get("name", ""),
            )
            r["exit_day"] = res_open.get("exit_day")
            r["exit_price"] = res_open.get("exit_price")
            if is_stale:
                r["fill"] = False
                r["fill_reason"] = "STALE_SIGNAL"
                r["outcome"] = "STALE_SIGNAL"

            lim_fill = False
            lim_reason = "NO_FILL_5D"
            lim_res: dict = {}
            max_wait = min(len(pit) - 1, cut + 5)
            max_bench_day = bench_days[min(len(bench_days) - 1, b_hi + 4)] if b_hi < len(bench_days) else "9999-12-31"
            for k_idx in range(cut + 1, max_wait + 1):
                b_k = pit[k_idx]
                if b_k["day"] > max_bench_day:
                    break
                op_k, lo_k = float(b_k["open"]), float(b_k["low"])
                if op_k <= entry_high:
                    fill_px = op_k
                elif lo_k <= entry_high:
                    fill_px = entry_high
                else:
                    continue
                if fill_px <= stop:
                    lim_reason = "GAP_BELOW_STOP"
                    break
                cand_res = label_triple_barrier_event(
                    pit,
                    entry_idx=k_idx,
                    tp_price=tp1,
                    sl_price=stop,
                    horizon_bars=horizon,
                    market=market,
                    asset_type="equity",
                    symbol=sym,
                    name=payload.get("name", ""),
                    entry_price_override=fill_px,
                )
                if cand_res.get("tradable"):
                    lim_fill = True
                    lim_reason = "FILLED_LIMIT"
                    lim_res = cand_res
                else:
                    lim_reason = str(cand_res.get("reason") or "UNTRADABLE")
                break

            r["limit_n5_fill"] = lim_fill
            r["limit_n5_reason"] = lim_reason
            r["limit_n5_entry_day"] = lim_res.get("entry_day")
            r["limit_n5_entry_price"] = lim_res.get("entry_price")
            r["limit_n5_exit_day"] = lim_res.get("exit_day")
            r["limit_n5_outcome"] = lim_res.get("outcome")
            r["limit_n5_net_return"] = lim_res.get("net_return")
            r["limit_n5_holding_bars"] = lim_res.get("holding_bars")
            new_rows.append(r)
            updated_records += 1

        tmp_path = jsonl_path.with_suffix(".jsonl.tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            for item in new_rows:
                fh.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
        tmp_path.replace(jsonl_path)
        updated_shards += 1
    return updated_shards, updated_records


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", type=Path, default=ART / "runs" / "baseline_v1")
    ap.add_argument("--market", choices=["CN", "HK", "US", "ALL"], default="ALL")
    args = ap.parse_args()
    markets = ["CN", "HK", "US"] if args.market == "ALL" else [args.market]
    for m in markets:
        shards, recs = enrich_market(args.run_root, m)
        print(f"[{m}] enriched shards={shards} records={recs}")


if __name__ == "__main__":
    main()
