"""v6 输出契约：去预测化、schema、体积预算与命令行信封（只用录制的真实行情）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from analyze_and_forecast import run_pipeline  # noqa: E402

REQ_BASE = {"display_bars": 120, "structure_bars": 120, "horizon_bars": 15}


def _cn_moutai(tmp_path: Path, **kw) -> dict:
    return run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ_BASE},
        output_dir=tmp_path,
        **kw,
    )


def test_reversal_layer_removed(replay_recorded, tmp_path: Path) -> None:
    import risk

    rep = _cn_moutai(tmp_path)
    assert "high_conviction_plan" not in rep["triple_barrier_plan"]
    dec = rep["meta_labeling_decision"]
    assert not any(k.startswith("high_conviction") for k in dec)
    assert "decision_probability" not in dec
    for name in ("detect_high_conviction_rebound", "high_conviction_plan_levels", "HC_PAUSE_REASONS"):
        assert not hasattr(risk, name)
