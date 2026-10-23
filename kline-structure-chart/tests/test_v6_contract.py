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
    assert bt["schema_version"] == "6.0.0"
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


from conftest import load_v6  # noqa: E402

V6_TOP = {
    "schema_version", "meta", "quality", "verdict", "market_context", "structure", "indicators",
    "scenarios", "plan", "volatility_range", "quotable_prices", "files", "reproducibility",
}
FORBIDDEN_KEY_PARTS = ("probability", "frequency", "freq", "high_conviction", "judgment", "brier", "analogue")


def _walk_keys(obj, path="$"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{path}.{k}", k
            yield from _walk_keys(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_keys(v, f"{path}[{i}]")


@pytest.mark.slow
@pytest.mark.parametrize(
    ("market", "symbol", "asset_type"),
    [
        ("CN", "600519.SH", "equity"),
        ("CN", "000001.SH", "index"),
        ("HK", "00700.HK", "equity"),
        ("US", "NVDA.US", "equity"),
        ("US", "SPY.US", "etf"),
    ],
)
def test_v6_result_validates_and_fits_budget(replay_recorded, tmp_path: Path, market, symbol, asset_type) -> None:
    from contracts import validate_report

    rep = run_pipeline({"symbol": symbol, "market": market, "asset_type": asset_type, **REQ_BASE}, output_dir=tmp_path)
    result, chart = load_v6(rep)
    validate_report(result)
    assert set(result) == V6_TOP
    assert result["schema_version"] == "6.0.0"
    assert result["meta"]["timezone"] in ("Asia/Shanghai", "Asia/Hong_Kong", "America/New_York")
    assert all(not Path(p).is_absolute() for p in result["files"].values())
    assert (tmp_path / result["files"]["chart_data_json"]).exists()
    assert len(chart["bars"]) == REQ_BASE["display_bars"]
    size = Path(rep["rendered_paths"]["result_json"]).stat().st_size
    assert size <= 80 * 1024, f"result.json {size} 字节超过 80 KB 预算"
    bad = [p for p, k in _walk_keys(result) if any(part in k.lower() for part in FORBIDDEN_KEY_PARTS)]
    assert not bad, f"result.json 含预测性字段: {bad[:5]}"


def test_v6_result_validates_without_benchmark(tmp_path: Path) -> None:
    from conftest import load_recorded
    from contracts import validate_report

    bars_file = tmp_path / "600519_bars.json"
    rows = load_recorded("sina_daily/sh600519.json")
    bars_file.write_text(json.dumps([{k: r[k] for k in ("day", "open", "high", "low", "close", "volume")} for r in rows]))
    rep = run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "name": "贵州茅台", **REQ_BASE},
        output_dir=tmp_path / "out", input_path=bars_file, allow_network=False,
    )
    result, _ = load_v6(rep)
    validate_report(result)
    assert result["quality"]["status"] == "PARTIAL"
    assert result["market_context"]["benchmark_missing"] is True


def test_v6_schema_marks_stability_and_rejects_unknown_fields() -> None:
    from contracts import load_report_schema, schema_errors

    schema = load_report_schema()
    assert schema["additionalProperties"] is False
    for key, sub in schema["properties"].items():
        assert sub.get("x-stability") in ("stable", "experimental"), key
        for child, child_schema in (sub.get("properties") or {}).items():
            assert child_schema.get("x-stability") in ("stable", "experimental"), f"{key}.{child}"
    errs = schema_errors({"schema_version": "6.0.0", "surprise": 1}, schema)
    assert any("未声明的字段 surprise" in e for e in errs)
    assert any("缺少必填字段 verdict" in e for e in errs)
    assert schema_errors(1.5, {"type": "integer"}) and not schema_errors(2, {"type": "number"})
    assert schema_errors(True, {"type": "number"})


KEYPATH_SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "contract" / "result_v6_keypaths_600519.json"


def _key_paths(obj, path=""):
    """result.json 的全部键路径：字典键用 . 连接，列表下标统一折叠为 [*]。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{path}.{k}" if path else k
            yield sub
            yield from _key_paths(v, sub)
    elif isinstance(obj, list):
        for v in obj:
            yield from _key_paths(v, f"{path}[*]")


def test_v6_result_keypaths_match_snapshot(replay_recorded, tmp_path: Path) -> None:
    """固定 600519.SH 录制行情下 result.json 的完整键集，防止内部新字段经透传投影静默进入稳定契约。

    确认是有意的契约变更后重新生成快照：
    UPDATE_V6_KEYPATHS=1 uv run pytest -q tests/test_v6_contract.py -k keypaths_match_snapshot
    """
    import os

    result, _ = load_v6(_cn_moutai(tmp_path))
    actual = sorted(set(_key_paths(result)))
    if os.environ.get("UPDATE_V6_KEYPATHS") == "1":
        KEYPATH_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        KEYPATH_SNAPSHOT.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    expected = json.loads(KEYPATH_SNAPSHOT.read_text(encoding="utf-8"))
    added, removed = sorted(set(actual) - set(expected)), sorted(set(expected) - set(actual))
    assert not added and not removed, (
        f"result.json 键路径与快照不一致：新增 {added}；删除 {removed}。"
        "新增或删除契约字段须同步 schema 的 x-stability 标注与 CHANGELOG\"契约变更\"，确认后再更新快照"
    )
