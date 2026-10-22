#!/usr/bin/env python3
"""缺陷 D1 的抽样等价核对（主做多计划有效性检验设计第 6.5 节）：替代修复后的开发区全量重跑。

从修复前（509e499）开发区第一遍扫描的记录里，每个市场按固定种子抽两组股票-日：全部记录、只抽主计划信号。
用当前代码重算第一遍记录，逐项比较评级、入场类型、入场区、止损、TP1 与保本上移参数；
修复前报错的股票-日全部重算，确认已不再报错。第一遍记录不含任何结局，本核对不接触收益。
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import main_plan_scan as scan
import run_dev_region_v2 as v2
from common import ART, LONG_ACTIONS

PRE_FIX_DIRS = {"CN": "dev_CN_5c70d51d5fee", "HK": "dev_HK_6042057c8c66", "US": "dev_US_3e5a1c72fcc7"}
PRE_FIX_COMMIT = "509e4998e078ab9ad1c9146f076ea9f7edc0a9a4"
SEED = 20261021
N_SAMPLE = 2000
KEYS = ("a", "st", "z", "sl", "tp", "rt")
OUT = ART / "d1_equivalence.json"


def _compare(rows: list[dict], cache: dict) -> dict:
    changed = []
    for r in sorted(rows, key=lambda x: x["sym"]):
        data = cache.get(r["sym"]) or cache.setdefault(r["sym"], v2._load_symbol(r["sym"]))
        new = scan.scan_stock_day(r["sym"], data, r["t"], r["day"], r["b"])
        diff = {k: [r[k], new[k]] for k in KEYS if new[k] != r[k]}
        if diff:
            changed.append({"sym": r["sym"], "day": r["day"], "old_action": r["a"], "new_action": new["a"], **diff})
    return {"n": len(rows), "n_changed": len(changed),
            "n_signal_changed": sum(1 for c in changed if c["old_action"] in LONG_ACTIONS or c["new_action"] in LONG_ACTIONS),
            "changed": changed}


def check_market(market: str) -> dict:
    scan.init(market, "dev")
    all_rows = list(scan.iter_rows(ART / "main_plan" / PRE_FIX_DIRS[market]))
    rows = [r for r in all_rows if not r.get("__error__")]
    signals = [r for r in rows if r["a"] in LONG_ACTIONS]
    cache: dict = {}
    result = {
        "pre_fix_rows": len(rows), "pre_fix_signals": len(signals),
        "random_rows": _compare(random.Random(SEED).sample(rows, N_SAMPLE), cache),
        "signal_rows": _compare(random.Random(SEED).sample(signals, N_SAMPLE), cache),
    }
    errors = [r for r in all_rows if r.get("__error__")]
    still_failing = []
    for e in errors:
        data = cache.get(e["sym"]) or cache.setdefault(e["sym"], v2._load_symbol(e["sym"]))
        t, b_pos = next((t, b) for d, t, b in v2._eligible_days(e["sym"], data) if d == e["day"])
        try:
            scan.scan_stock_day(e["sym"], data, t, e["day"], b_pos)
        except Exception as exc:  # noqa: BLE001 - 如实记录仍报错的股票-日
            still_failing.append({"sym": e["sym"], "day": e["day"], "err": f"{type(exc).__name__}: {exc}"})
    result["pre_fix_errors"] = {"n": len(errors), "still_failing": still_failing}
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    markets = list(PRE_FIX_DIRS)
    # 每个市场一个进程：run_dev_region_v2 的市场状态是模块级全局变量，不能在同一进程里混用
    with ProcessPoolExecutor(len(markets), mp_context=mp.get_context("spawn"), max_tasks_per_child=1) as pool:
        results = dict(zip(markets, pool.map(check_market, markets)))
    out = {"defect": "D1", "pre_fix_commit": PRE_FIX_COMMIT, "seed": SEED, "n_sample_per_group": N_SAMPLE,
           "compared_fields": list(KEYS), "markets": results}
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for m, r in results.items():
        print(m, "random", r["random_rows"]["n_changed"], "/", r["random_rows"]["n"],
              "signal", r["signal_rows"]["n_changed"], "/", r["signal_rows"]["n"],
              "pre_fix_errors", r["pre_fix_errors"]["n"], "still_failing", len(r["pre_fix_errors"]["still_failing"]))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
