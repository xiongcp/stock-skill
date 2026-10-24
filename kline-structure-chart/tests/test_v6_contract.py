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


def _cn_moutai(tmp_path: Path, request_extra: dict | None = None, **kw) -> dict:
    return run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ_BASE, **(request_extra or {})},
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


KEYPATH_DIR = Path(__file__).resolve().parent / "fixtures" / "contract"


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


# 每个配置一份快照 result_v6_keypaths_<id>.json：
#   600519_plain           基线（无账户净值、无回测），即最初的单配置快照
#   600519_equity_backtest 带账户净值（position_sizing 字段）与回测（files.backtest_json），含 plan.short.*
#   00700_hk / nvda_us     港美股：market_microstructure.{lot_size_source,day_trading_rule} 等市场相关字段
#   000001_index           指数：asset_type=index 下没有交易计划的键形
KEYPATH_CONFIGS = {
    "600519_plain": ({"symbol": "600519.SH", "market": "CN", "asset_type": "equity"}, {}),
    "600519_equity_backtest": (
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "account_equity": 500000, "max_risk_pct": 0.01},
        {"run_backtest": True, "backtest_step": 20},
    ),
    "00700_hk": ({"symbol": "00700.HK", "market": "HK", "asset_type": "equity"}, {}),
    "nvda_us": ({"symbol": "NVDA.US", "market": "US", "asset_type": "equity"}, {}),
    "000001_index": ({"symbol": "000001.SH", "market": "CN", "asset_type": "index"}, {}),
}


@pytest.mark.parametrize("config_id", list(KEYPATH_CONFIGS))
def test_v6_result_keypaths_match_snapshot(replay_recorded, tmp_path: Path, config_id: str) -> None:
    """固定录制行情下各配置 result.json 的完整键集，防止内部新字段经透传投影静默进入稳定契约。

    确认是有意的契约变更后重新生成快照：
    UPDATE_V6_KEYPATHS=1 uv run pytest -q tests/test_v6_contract.py -k keypaths_match_snapshot
    """
    import os

    request, kw = KEYPATH_CONFIGS[config_id]
    rep = run_pipeline({**request, **REQ_BASE}, output_dir=tmp_path, **kw)
    result, _ = load_v6(rep)
    actual = sorted(set(_key_paths(result)))
    snapshot = KEYPATH_DIR / f"result_v6_keypaths_{config_id}.json"
    if os.environ.get("UPDATE_V6_KEYPATHS") == "1":
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    expected = json.loads(snapshot.read_text(encoding="utf-8"))
    added, removed = sorted(set(actual) - set(expected)), sorted(set(expected) - set(actual))
    assert not added and not removed, (
        f"[{config_id}] result.json 键路径与快照不一致：新增 {added}；删除 {removed}。"
        "新增或删除契约字段须同步 schema 的 x-stability 标注与 CHANGELOG\"契约变更\"，确认后再更新快照"
    )


def test_cli_success_envelope(replay_recorded, tmp_path: Path, capsys) -> None:
    from analyze_and_forecast import main

    rc = main(["--market", "CN", "--symbol", "600519.SH", "--asset-type", "equity", "--output-dir", str(tmp_path)])
    assert rc == 0
    env = json.loads(capsys.readouterr().out)
    assert env["status"] == "ok" and env["schema_version"] == "6.0.0"
    assert set(env["verdict"]) == {"action", "action_cn", "risk_veto", "veto_reasons", "rule_score", "rating_cn", "headline_cn"}
    assert env["files"]["result_json"] == "600519.SH_result.json"
    assert (tmp_path / env["files"]["result_json"]).exists()


def test_cli_insufficient_history_exit_code(replay_recorded, tmp_path: Path, capsys) -> None:
    from analyze_and_forecast import main

    rc = main(["--market", "CN", "--symbol", "600519.SH", "--asset-type", "equity", "--as-of", "2022-12-01", "--output-dir", str(tmp_path)])
    assert rc == 4
    captured = capsys.readouterr()
    env = json.loads(captured.out)
    assert env == {"status": "error", "error": {"code": "INSUFFICIENT_HISTORY", "message": env["error"]["message"]}}
    assert "历史长度不足" in env["error"]["message"] and "600519.SH" in env["error"]["message"]  # 消息带上标的代码
    assert captured.err.startswith("[INSUFFICIENT_HISTORY]")


@pytest.mark.slow
def test_lint_reads_v6_and_blocks_any_probability(replay_recorded, tmp_path: Path) -> None:
    from lint_response import lint_agent_response

    rep = _cn_moutai(tmp_path, run_backtest=True, backtest_step=10)
    result, _ = load_v6(rep)
    bt = json.loads(Path(rep["rendered_paths"]["backtest_json"]).read_text(encoding="utf-8"))
    head = "风控否决：大盘偏弱。" if result["verdict"]["risk_veto"] else ""
    stop = result["plan"]["long"]["stop_loss_barrier"]
    ok = f"{head}止损 {stop:.2f}。以上为规则化技术分析，不构成投资建议。"
    assert lint_agent_response(ok, result, bt) == []
    for bad in ("上涨概率 62%", "历史条件频率 55%", "主观研判概率 40%", "命中率约 70%"):
        errs = lint_agent_response(f"{head}{bad}。不构成投资建议。", result, bt)
        assert errs, bad
    wr = bt["backtest"]["all_long_signals"].get("win_rate_pct")
    if wr is not None:
        n = bt["backtest"]["all_long_signals"]["trades"]
        good_bt = f"{head}回测胜率 {wr}%（n={n}，95% 区间见报告）。不构成投资建议。"
        assert lint_agent_response(good_bt, result, bt) == []
    fake = f"{head}止损 {stop * 1.37:.2f}。不构成投资建议。"
    assert any("白名单" in e for e in lint_agent_response(fake, result, bt))


@pytest.mark.slow
def test_report_digest_line_passes_lint_against_v6(replay_recorded, tmp_path: Path) -> None:
    """report.md 的「直出摘要」加免责声明作为短回复，必须通过对 v6 result.json 的回复校验（B2 遗失的覆盖）。"""
    from lint_response import lint_agent_response

    rep = _cn_moutai(tmp_path)
    result, _ = load_v6(rep)
    md = Path(rep["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    line = next(ln for ln in md.splitlines() if "直出摘要" in ln)
    digest = line.split("：", 1)[1].strip()
    assert len(digest) > 20
    reply = f"{digest}\n以上为规则化技术分析，不构成投资建议。"
    assert lint_agent_response(reply, result) == []
    # 同一摘要篡改一个价位数字后必须被拦截
    import re

    m = re.search(r"(?:目标|止损|入场|收盘)[^\d\n]{0,4}(\d+\.\d+)", digest)
    assert m, digest
    tampered = reply.replace(m.group(1), f"{float(m.group(1)) * 1.37:.2f}", 1)
    assert lint_agent_response(tampered, result)


def test_v6_html_reads_result_and_chart(replay_recorded, tmp_path: Path) -> None:
    from render_html import render_html

    rep = _cn_moutai(tmp_path)
    result, chart = load_v6(rep)
    out = render_html(result, chart, tmp_path / "dash.html").read_text(encoding="utf-8")
    assert '"chart":{' in out.replace(" ", "") and '"result":{' in out.replace(" ", "")
    assert "正常波动范围" in out and "保形预测" not in out


def test_v6_report_md_reads_result(replay_recorded, tmp_path: Path) -> None:
    from render_report_md import render_report_markdown

    rep = _cn_moutai(tmp_path)
    result, _ = load_v6(rep)
    md = render_report_markdown(result)
    vr = result["volatility_range"]
    assert f"**{result['verdict']['rule_score']:.1f} / 100**" in md
    assert f"{vr['q10'][-1]:.2f}" in md and f"{vr['q90'][-1]:.2f}" in md
    assert "正常波动范围" in md
    assert md == Path(rep["rendered_paths"]["report_md"]).read_text(encoding="utf-8")


def test_lint_rejects_share_and_ratio_frequency_numbers_but_allows_factor_wording() -> None:
    """「上涨占比」「先到目标的比例」是频率数字，与概率同样不得对外；实体占比等因子、参数用语不受影响。"""
    from lint_response import _unvalidated_probability_hits

    for bad in ("上涨占比 53.8%", "上涨占比为53.8%", "先到目标的比例为 62%", "触及止损的比例约 30%"):
        assert _unvalidated_probability_hits(bad + "。"), bad
    for ok in (
        "K线实体占比 0.35，上影线占比偏高。",
        "实体占比 35%，影线占比 20%。",
        "单笔风险比例 1%，斐波那契回撤比例 61.8%。",
        "涨跌停比例 10%，隔夜跳空占比 2%。",
        "回测胜率 55.0%（n=40，95% 区间 40.0%~69.0%）。",
    ):
        assert _unvalidated_probability_hits(ok) == [], ok


@pytest.mark.slow
def test_report_md_has_no_frequency_numbers_with_backtest(replay_recorded, tmp_path: Path) -> None:
    """带回测的完整 report.md 不得出现任何未经校验的频率/概率数字，也不再有「上涨占比」列。"""
    from lint_response import _unvalidated_probability_hits

    rep = _cn_moutai(
        tmp_path, {"account_equity": 500000, "max_risk_pct": 0.01}, run_backtest=True, backtest_step=20
    )
    md = Path(rep["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    assert "回测" in md and "胜率" in md  # 确认回测章节确实渲染了
    assert "上涨占比" not in md
    assert _unvalidated_probability_hits(md) == []
