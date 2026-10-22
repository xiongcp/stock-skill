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


def test_judgment_layer_removed(replay_recorded, tmp_path: Path) -> None:
    rep = _cn_moutai(tmp_path)
    assert "judgment_pack" not in rep
    assert not list(tmp_path.glob("*_judgment_pack.json"))
    for name in ("judgment.py", "validate_judgment.py", "score_judgments.py"):
        assert not (SCRIPTS / name).exists()
    md = Path(rep["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    assert "研判" not in md


def test_price_whitelist_moved_to_price_text() -> None:
    from price_text import collect_price_whitelist, price_in_whitelist

    wl = collect_price_whitelist(
        {"plan": {"stop_loss_barrier": 12.346, "risk_reward_ratio": 1.4, "entry_zone": [12.5, 12.8]}},
        price_decimals=2,
        recent_bars=[{"close": 13.0, "volume": 1000.0}],
    )
    assert wl["prices"] == [12.35, 12.5, 12.8, 13.0]  # 盈亏比（ratio）与成交量不是价格
    assert price_in_whitelist(12.34, wl["prices"], wl["tolerance"])
    assert not price_in_whitelist(1.4, wl["prices"], wl["tolerance"])


def test_historical_frequency_removed(replay_recorded, tmp_path: Path) -> None:
    rep = _cn_moutai(tmp_path, run_backtest=True, backtest_step=20)
    # pytest 的 tmp_path 目录名由测试函数名截断而来，会含 "historical_frequency"，先剔除再查字段
    text = json.dumps({k: v for k, v in rep.items() if k != "rendered_paths"}, ensure_ascii=False).replace(str(tmp_path), "")
    for token in ("historical_frequency", "time_window_bars", "target_freq", "bars_to_target"):
        assert token not in text
    bt = json.loads(Path(rep["rendered_paths"]["backtest_json"]).read_text(encoding="utf-8"))
    assert "scenario_frequency_brier" not in bt["backtest"]
    assert "multi_scenarios_base_rates" not in bt["causal_replay_audit"]["modules_audited"]
    assert not (SCRIPTS / "base_rates.py").exists()


def test_scenario_fan_removed(replay_recorded, tmp_path: Path) -> None:
    rep = _cn_moutai(tmp_path)
    assert "scenario_conditional_fan" not in rep["future_projection_15d"]
    html_text = Path(rep["rendered_paths"]["dashboard_html"]).read_text(encoding="utf-8")
    assert 'data-layer="scenario_fan"' not in html_text and "情景混合" not in html_text
    md = Path(rep["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    assert "情景混合" not in md
