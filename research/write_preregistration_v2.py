#!/usr/bin/env python3
"""生成留出集 H1 的预注册 v2（回测与验收口径调整方案第 6 步）。

把将要冻结的代码文件与数据清单的 sha256、反转层的全部参数与关键函数源码 hash、H1 区间、
验收门槛与随机基准设置写入 research/preregistration/preregistration_v2.json。生成后需提交到 git 才算冻结；
run_h1_holdout.py 运行前会逐项校验。本脚本不读取任何 H1 结果。
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from common import ART, ROOT

import run_dev_region_v2 as v2
import risk  # noqa: E402  (common 已把技能 scripts 加入 sys.path)

OUT = ROOT / "research" / "preregistration" / "preregistration_v2.json"
RESEARCH_FILES = [
    "common.py", "run_backtest.py", "aggregate.py", "discover_alpha_dev.py", "run_dev_region_v2.py",
    "run_h1_holdout.py", "build_pit_universe.py", "h1_coverage_report.py", "write_preregistration_v2.py",
]
DATA_FILES = ["artifacts/long_backtest/pit_universe_h1.json", "artifacts/long_backtest/cache/MANIFEST.json"]
GATE_DEFS = {
    "P1": "扣费后单笔净收益均值 > 0，且按信号日期分块 bootstrap 95% 下限 > 0（5 日限价成交层，episode 口径）",
    "P2a": "episode 净正率 − 同日随机入场净正率（同几何、同撮合、同费用）的分块 95% 下限 > 0",
    "P2b": "单笔净收益均值 − 同日随机入场均值的分块 95% 下限 > 0",
    "P3": "episode >= 100 且不同信号日 >= 30",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _src_sha(fn) -> str:
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()


def _trials() -> dict:
    ledger = ART / "trial_ledger.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()] if ledger.exists() else []
    return {
        "ledger_rows": len(rows),
        "configs_evaluated_total": sum(int(r.get("n_configs_evaluated") or 1) for r in rows),
        "note": "开发区（含冒烟）全部运行，每次运行的配置数之和；H1 报告须如实写明",
    }


def build(gates: list[str]) -> dict:
    files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "kline-structure-chart" / "scripts").glob("*.py"))
    files += [f"research/{f}" for f in RESEARCH_FILES]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    return {
        "schema_version": "2.0",
        "kind": "PREREGISTRATION",
        "registered_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_commit": head,
        "spec": "docs/superpowers/specs/2026-10-20-backtest-acceptance-revision.md",
        "supersedes": ["artifacts/long_backtest/preregistration.json", "artifacts/long_backtest/preregistration_amendment_v1_1.json"],
        "frozen_files_sha256": {f: _sha(ROOT / f) for f in files},
        "frozen_data_sha256": {f: _sha(ROOT / f) for f in DATA_FILES},
        "frozen_specification": {
            "trigger": "risk.detect_high_conviction_rebound",
            "trigger_source_sha256": _src_sha(risk.detect_high_conviction_rebound),
            "plan_levels": "risk.high_conviction_plan_levels",
            "plan_levels_source_sha256": _src_sha(risk.high_conviction_plan_levels),
            "prefilter_source_sha256": _src_sha(v2.prefilter),
            "entry_zone_atr": list(risk.HC_ENTRY_ZONE_ATR),
            "tp1_atr": risk.HC_TP1_ATR,
            "sl_atr": risk.HC_SL_ATR,
            "limit_order_sessions": v2.LIMIT_SESSIONS,
            "horizon_bars": v2.HORIZON,
            "trailing_stop": False,
            "subtypes": ["BENCH_SYNC_REBOUND", "DEEP_OVERSOLD_TURN"],
            "benchmark_veto": "BENCHMARK_WEAK（大盘状态分 < 35）抑制，主指标只用否决后的信号",
            "paused_markets": sorted(risk.HC_PAUSED_MARKETS),
            "selection_from_step4": {"config": "tp0.85_sl2.20|ALL", "evidence": "artifacts/long_backtest/dev_region_v2_grid_postfix.json"},
        },
        "h1": {
            "markets": ["CN", "US", "HK"],
            "first_day": v2.H1_FIRST_DAY,
            "last_day": v2.H1_LAST_DAY,
            "universe": DATA_FILES[0],
            "warmup_bars": v2.WARMUP_BARS,
        },
        "acceptance": {
            "acceptance_markets": ["CN", "US"],
            "diagnostic_markets": ["HK"],
            "gates": gates,
            "gate_definitions": {g: GATE_DEFS[g] for g in gates},
            "report_only": {
                "T": "episode 净正率点估计 >= 70%（方案 D1=b：只报告，不作门槛）",
                **{g: GATE_DEFS[g] for g in GATE_DEFS if g not in gates},
            },
            "rule": "每个验收市场单独判定，门槛全部满足为通过；未通过如实报告，不得回到 H1 调参，下一轮只能用前向 H2 验收",
        },
        "baseline": {
            "kind": "same_day_random",
            "definition": "在（否决后）信号日从同一 PIT 股票池随机抽股，同入场区、同 5 日限价、同 TP/SL、同费用；同样做 21 天 episode 去重",
            "sampling_multiple": v2.SAME_DAY_MULTIPLE,
            "seed": v2.SEED,
            "bootstrap": {"block": "signal_day", "n_boot": 2000, "seed": 20261020},
        },
        "trials_before_registration": _trials(),
        "disclosures": [
            "幸存者偏差：股票池只含当前上市标的，2017–2020 年退市股不在池中，对超跌反转偏乐观",
            "ST 历史不可得，按当前名称判断",
            "港股：Yahoo 占位K线已按线上口径剔除；剔除后每期约一半成分股成交稀疏，港股只作诊断",
            "交易日历只覆盖 2021 年起，但不在反转层回测路径上",
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gates", default="P1,P2a,P2b,P3", help="验收门槛，逗号分隔（其余进入 report_only）")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    gates = [g.strip() for g in args.gates.split(",") if g.strip()]
    unknown = [g for g in gates if g not in GATE_DEFS]
    if unknown:
        raise SystemExit(f"unknown gates: {unknown}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(build(gates), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
