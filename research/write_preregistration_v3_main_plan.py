#!/usr/bin/env python3
"""生成主做多计划留出集 H1 的预注册 v3（主做多计划有效性检验设计第 6.3 节）。

把将要冻结的代码文件与数据清单的 sha256、主计划函数源码 hash、H1 区间、验收门槛、随机基准设置、
登记前试验次数与披露事项写入 research/preregistration/preregistration_v3_main_plan.json。
生成后需提交到 git 才算冻结；run_h1_main_plan.py 运行前会逐项校验。本脚本不读取任何 H1 结果。
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import main_plan_eval as ev
import run_dev_region_v2 as v2
from common import ART, LONG_ACTIONS, ROOT

import risk  # noqa: E402  (common 已把技能 scripts 加入 sys.path)

OUT = ROOT / "research" / "preregistration" / "preregistration_v3_main_plan.json"
RESEARCH_FILES = [
    "common.py", "run_backtest.py", "aggregate.py", "discover_alpha_dev.py", "run_dev_region_v2.py",
    "run_h1_holdout.py", "main_plan_scan.py", "main_plan_eval.py", "run_h1_main_plan.py",
    "write_preregistration_v3_main_plan.py",
]
DATA_FILES = ["artifacts/long_backtest/pit_universe_h1.json", "artifacts/long_backtest/cache/MANIFEST.json"]
DEV_RESULT = ART / "dev_main_plan_v3_metrics.json"
GATE_DEFS = {
    "P1": "扣费后单笔净收益均值 > 0，且按信号日期分块 bootstrap 95% 下限 > 0（5 日限价成交层，episode 口径）",
    "P2b": "每个信号 episode 的净收益 − 其当日同日随机基准（移植几何）平均净收益，对全部配对取平均；按信号日分块 bootstrap 95% 下限 > 0",
    "P3": "episode >= 100 且不同信号日 >= 30",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _trials() -> dict:
    ledger = ART / "trial_ledger.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()] if ledger.exists() else []
    mine = [r for r in rows if r.get("hypothesis") == ev.HYPOTHESIS and r.get("script") == "research/main_plan_eval.py"]
    return {
        "main_plan_runs": len(mine),
        "main_plan_configs_evaluated": sum(int(r.get("n_configs_evaluated") or 1) for r in mine),
        "other_hypothesis_ledger_rows": len(rows) - len(mine),
        "note": "主计划开发区运行（含冒烟）只评估现行默认规则；其余台账行属于反转层（另一假设）",
    }


def build() -> dict:
    files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "kline-structure-chart" / "scripts").glob("*.py"))
    files += [f"research/{f}" for f in RESEARCH_FILES]
    # bootstrap 依赖 numpy 的随机流，不保证跨版本一致：依赖锁文件一并冻结
    files += ["kline-structure-chart/pyproject.toml", "kline-structure-chart/uv.lock"]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    return {
        "schema_version": "3.0",
        "kind": "PREREGISTRATION",
        "hypothesis": ev.HYPOTHESIS,
        "registered_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_commit": head,
        "spec": ev.SPEC,
        "frozen_files_sha256": {f: _sha(ROOT / f) for f in files},
        "frozen_data_sha256": {f: _sha(ROOT / f) for f in DATA_FILES},
        "environment": {"python": platform.python_version(), "numpy": __import__("numpy").__version__},
        "run_tolerance": {"max_scan_errors": 0, "max_skipped_symbols": 0},
        "frozen_specification": {
            "plan": "risk.compute_triple_barrier_plan",
            "plan_source_sha256": hashlib.sha256(inspect.getsource(risk.compute_triple_barrier_plan).encode()).hexdigest(),
            "signal_actions": list(LONG_ACTIONS),
            "fill": f"5 日限价（{v2.LIMIT_SESSIONS} 个交易日内以入场区上沿成交，低开按开盘价）；成交价不高于止损记 GAP_BELOW_STOP",
            "exit": "evaluation.label_triple_barrier_event：止损/TP1、同根先判止损、保本上移参数取自计划 breakeven_ratchet、T+1 与一字跌停顺延",
            "horizon_bars": v2.HORIZON,
            "episode_gap_days": 21,
            "next_open_layer": "次日开盘落在入场区内才成交（LIVE_PLAN_REPLAY_V6 规则），只报告",
        },
        "h1": {
            "markets": ["CN", "US", "HK"],
            "first_day": v2.H1_FIRST_DAY,
            "last_day": v2.H1_LAST_DAY,
            "universe": DATA_FILES[0],
            "warmup_bars": v2.WARMUP_BARS,
        },
        "acceptance": {
            "acceptance_markets": ["CN", "US", "HK"],
            "diagnostic_markets": [],
            "gates": ["P1", "P2b", "P3"],
            "gate_definitions": GATE_DEFS,
            "report_only": {
                "P2a": "按同样方式配对的净正率差（是否盈利 − 当日基准盈利比例）的分块 95% 下限 > 0",
                "T": "episode 净正率点估计 >= 70%",
            },
            "rule": "每个市场单独判定，三项全部满足为通过；P3 不满足同样判为未通过。未通过的市场线上降级为观察位（设计 M4、第 7.1 节）；不得回到 H1 调参",
        },
        "baseline": {
            "kind": "same_day_random_transplanted_geometry",
            "definition": "信号日按 min(1, 10 × 当日信号数 / 当日进入评估的股票-日数) 抽股，按哈希对应当日一个信号，把其价位的 ATR 倍数套到被抽股票的收盘价与线上 ATR14 上并取整（止损原值 <= 0 或取整后几何自相矛盾的剔除）；同撮合、同出场、同费用；不做 episode 去重，按日汇总",
            "pairing": "每个信号 episode − 其当日基准平均值；当日无基准的信号 episode 不参与配对并单独计数",
            "sampling_multiple": ev.BASELINE_MULTIPLE,
            "seed": ev.SEED,
            "diagnostic": "own_plan：被抽股票使用自身计划价位，只报告",
            "bootstrap": {"block": "signal_day", "n_boot": 2000, "seed": 20261020},
        },
        "dev_region_evidence": {
            "file": str(DEV_RESULT.relative_to(ROOT)),
            "sha256": _sha(DEV_RESULT) if DEV_RESULT.exists() else None,
        },
        "trials_before_registration": _trials(),
        "disclosures": [
            "H1 区间已被反转层验收使用过一次（h1_result.json 不含任何主计划统计）",
            "开发区主计划结果 v1 已看过，开发区结果属样本内",
            "幸存者偏差：股票池只含当前上市标的，2017–2020 年退市股不在池中",
            "ST 历史不可得，按当前名称判断",
            "港股成交稀疏：H1 港股池每期只有 51.6%~57.9% 的成分股期内 ≥95% 交易日有真实成交",
            "交易日历只覆盖 2021 年起，不在本回测路径上",
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    if not DEV_RESULT.exists():
        raise SystemExit(f"缺少开发区结果 {DEV_RESULT}：按设计须先完成开发区诊断再冻结")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
