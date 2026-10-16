"""标的元数据（简称 / ST / 港股每手股数）、代码校验与 Markdown 分析报告的回归测试（全部基于真实录制数据回放）。"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import data_gateway
from analysis import analyze_bundle
from analyze_and_forecast import run_pipeline
from data_gateway import load_bundle
from market_rules import (
    get_daily_price_limit_pct,
    is_st_instrument,
    latest_closed_session,
    tencent_quote_code_for,
    yahoo_symbol_for,
)
from render_report_md import render_report_markdown

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
REQ_BASE = {"display_bars": 120, "horizon_bars": 15}


# ---------------------------------------------------------------------------
# 代码校验（安全审查：代码会被拼进请求 URL）
# ---------------------------------------------------------------------------
def test_us_ticker_validation_and_share_classes() -> None:
    assert yahoo_symbol_for("NVDA.US") == "NVDA"
    assert yahoo_symbol_for("BRK.B.US") == "BRK-B"
    assert yahoo_symbol_for("01810.HK") == "1810.HK"
    for bad in ("A&B=1.US", "../x.US", "NVDA?x=1.US", "TOOLONGTICKER.US", ".US"):
        with pytest.raises(ValueError):
            yahoo_symbol_for(bad)
    assert tencent_quote_code_for("BRK.B.US", "US") == "usBRK.B"
    assert tencent_quote_code_for("01810.HK", "HK") == "hk01810"
    assert tencent_quote_code_for("HSTECH.HK", "HK") == "hkHSTECH"
    assert tencent_quote_code_for("600289.SH", "CN") == "sh600289"
    with pytest.raises(ValueError):
        tencent_quote_code_for("A&B.US", "US")


def test_url_tokens_are_percent_encoded() -> None:
    assert data_gateway._url_token("^HSI") == "^HSI"
    assert data_gateway._url_token("BRK-B") == "BRK-B"
    assert data_gateway._url_token("A&B?/x") == "A%26B%3F%2Fx"


def test_st_detection_only_by_a_share_name_prefix() -> None:
    assert is_st_instrument("600289.SH", "ST信通")
    assert is_st_instrument("002569.SZ", "*ST步森")
    assert is_st_instrument("600000.SH", "", is_st=True)
    assert not is_st_instrument("600519.SH", "贵州茅台")
    assert not is_st_instrument("MA.US", "MASTERCARD")
    assert not is_st_instrument("00700.HK", "ST HOLDINGS")


# ---------------------------------------------------------------------------
# 行情快照补全标的元数据
# ---------------------------------------------------------------------------
def test_vendor_name_fills_unregistered_symbol(replay_recorded) -> None:
    bundle = load_bundle({"symbol": "601318.SH", "market": "CN", "asset_type": "equity"}, now=NOW)
    assert bundle["target"]["instrument"]["name"] == "中国平安"
    assert bundle["provenance"]["instrument_meta"]["source"] == "TENCENT_QUOTE(sh601318)"
    # 用户显式 --name 优先于行情快照
    named = load_bundle({"symbol": "601318.SH", "market": "CN", "asset_type": "equity", "name": "平安"}, now=NOW)
    assert named["target"]["instrument"]["name"] == "平安"


def test_hk_board_lot_comes_from_vendor_quote(replay_recorded) -> None:
    req = {"symbol": "01810.HK", "market": "HK", "asset_type": "equity", "account_equity": 300000.0, **REQ_BASE}
    bundle = load_bundle(req, now=NOW)
    assert bundle["target"]["instrument"]["lot_size"] == 200
    report = analyze_bundle(bundle, req, include_replay=False)
    rules = report["triple_barrier_plan"]["market_microstructure"]
    assert rules["lot_size"] == 200 and rules["lot_size_source"] == "PROVIDED"
    shares = report["triple_barrier_plan"]["position_sizing"]["recommended_shares"]
    assert shares is not None and shares % 200 == 0
    assert report["meta"]["name"] == "小米集团-W" and report["meta"]["board_lot"] == 200


def test_hk_board_lot_fallback_is_disclosed(monkeypatch: pytest.MonkeyPatch, replay_recorded) -> None:
    def quote_down(code: str) -> dict:
        raise OSError("quote endpoint down")

    monkeypatch.setattr(data_gateway, "fetch_tencent_quote_meta", quote_down)
    req = {"symbol": "01810.HK", "market": "HK", "asset_type": "equity", "account_equity": 300000.0, **REQ_BASE}
    report = analyze_bundle(load_bundle(req, now=NOW), req, include_replay=False)
    rules = report["triple_barrier_plan"]["market_microstructure"]
    assert rules["lot_size"] == 100 and rules["lot_size_source"] == "ASSUMED_DEFAULT"
    assert any("港股每手股数未能" in n and "quote endpoint down" in n for n in report["quality"]["notes"])
    assert report["quality"]["status"] == "OK"


def test_st_flag_from_vendor_name_drives_historical_limits(replay_recorded) -> None:
    req = {"symbol": "600289.SH", "market": "CN", "asset_type": "equity", **REQ_BASE}
    bundle = load_bundle(req, now=NOW)
    assert bundle["target"]["instrument"]["name"] == "ST信通"
    assert bundle["target"]["instrument"]["is_st"] is True
    report = analyze_bundle(bundle, req, include_replay=False)
    assert report["meta"]["is_st"] is True
    # 主板 ST：2026-07-06 前 ±5%，之后 ±10%
    assert get_daily_price_limit_pct("CN", "equity", "600289.SH", is_st=True, day_str="2026-06-01") == 0.05
    assert report["triple_barrier_plan"]["market_microstructure"]["daily_price_limit_pct"] == 0.10


# ---------------------------------------------------------------------------
# Markdown 分析报告
# ---------------------------------------------------------------------------
@pytest.fixture
def cn_pipeline(replay_recorded, tmp_path: Path) -> tuple[dict, str, dict]:
    report = run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "account_equity": 500000.0, **REQ_BASE},
        output_dir=tmp_path,
        run_backtest=True,
        backtest_step=10,
    )
    md = Path(report["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    backtest = json.loads(Path(report["rendered_paths"]["backtest_json"]).read_text(encoding="utf-8"))
    return report, md, backtest


def test_report_md_numbers_match_json(cn_pipeline) -> None:
    report, md, backtest = cn_pipeline
    meta, fp = report["meta"], report["future_projection_15d"]
    assert md.startswith(f"# {meta['name']}（{meta['symbol']}）")
    assert f"数据截至 **{meta['as_of']}**" in md
    assert f"**{report['multi_timeframe_score']['rule_score']:.1f} / 100**" in md
    assert "不是胜率" in md
    plan = report["triple_barrier_plan"]
    assert f"{plan['entry_zone'][0]:.2f} ~ {plan['entry_zone'][1]:.2f}" in md
    assert f"| {plan['stop_loss_barrier']:.2f} | {plan['take_profit_1']:.2f} |" in md
    last = len(fp["horizon_dates"]) - 1
    assert f"| {fp['horizon_dates'][last]} | {last + 1} | {fp['q10'][last]:.2f} |" in md
    assert f"{fp['q90'][last]:.2f} |" in md
    cov80 = fp["calibration_audit"]["nominal_80pct_empirical_coverage"]
    assert f"80% 区间 {cov80 * 100:.1f}%" in md
    trades = backtest["backtest"]["all_long_signals"]["trades"]
    assert f"| 全部做多信号 | {trades} |" in md
    assert md.rstrip().endswith("*以上为规则化技术分析，仅供研究参考，不构成投资建议。*")


def test_report_md_puts_veto_before_conclusions(cn_pipeline) -> None:
    report, md, _ = cn_pipeline
    if report["debate_and_veto"]["risk_officer_veto"]:
        assert md.index("**风控否决**") < md.index("## 1. 结论速览")
        for reason in report["debate_and_veto"]["veto_reasons"]:
            assert reason in md
    else:
        assert "**风控否决**" not in md


def test_report_md_hides_all_prices_when_data_unavailable(cn_pipeline) -> None:
    report, _, _ = cn_pipeline
    broken = copy.deepcopy(report)
    broken["quality"]["status"] = "UNAVAILABLE"
    broken["quality"]["warnings"] = ["标的行情为合成数据，价格与形态均非真实"]
    md = render_report_markdown(broken)
    assert "数据不可用" in md and "标的行情为合成数据" in md
    for section in ("## 1.", "## 3.", "## 4.", "## 6."):
        assert section not in md
    assert f"{report['triple_barrier_plan']['stop_loss_barrier']:.2f}" not in md


def test_report_md_zero_trade_rows_show_dash(cn_pipeline) -> None:
    report, _, backtest = cn_pipeline
    bt = copy.deepcopy(backtest)
    bt["backtest"]["long_confirmed"] = {"trades": 0}
    md = render_report_markdown(report, bt)
    assert "| 确认做多 | 0 | — | — | — | — |" in md


def test_report_md_uses_market_tick_precision(replay_recorded, tmp_path: Path) -> None:
    hk = run_pipeline(
        {"symbol": "01810.HK", "market": "HK", "asset_type": "equity", **REQ_BASE}, output_dir=tmp_path / "hk"
    )
    md_hk = Path(hk["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    sl = hk["triple_barrier_plan"]["stop_loss_barrier"]
    assert f"| {sl:.2f} |" in md_hk and f"| {sl:.3f} |" not in md_hk  # 20~50 港元档最小价位 0.02，不应出现 3 位小数
    etf = run_pipeline(
        {"symbol": "510300.SH", "market": "CN", "asset_type": "etf", **REQ_BASE}, output_dir=tmp_path / "etf"
    )
    md_etf = Path(etf["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    assert f"| {etf['triple_barrier_plan']['stop_loss_barrier']:.3f} |" in md_etf  # A 股 ETF 最小价位 0.001


def test_missing_benchmark_blocks_long_confirmation(tmp_path: Path) -> None:
    from conftest import load_recorded

    bars_file = tmp_path / "600519_bars.json"
    rows = load_recorded("sina_daily/sh600519.json")
    bars_file.write_text(json.dumps([{k: r[k] for k in ("day", "open", "high", "low", "close", "volume")} for r in rows]))
    report = run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "name": "贵州茅台", **REQ_BASE},
        output_dir=tmp_path / "out",
        input_path=bars_file,
        allow_network=False,
    )
    assert report["quality"]["status"] == "PARTIAL"
    assert report["market_benchmark"]["benchmark_missing"] is True
    assert report["debate_and_veto"]["risk_officer_veto"] is True
    assert any("缺少大盘基准" in r for r in report["debate_and_veto"]["veto_reasons"])
    assert report["meta_labeling_decision"]["action"] != "LONG_CONFIRMED"
    md = Path(report["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    assert "**缺少大盘基准行情**" in md and "标的本身即市场基准" not in md


def test_default_benchmark_itself_needs_no_external_benchmark(replay_recorded, tmp_path: Path) -> None:
    report = run_pipeline(
        {"symbol": "000300.SH", "market": "CN", "asset_type": "index", **REQ_BASE}, output_dir=tmp_path
    )
    assert report["market_benchmark"]["benchmark_missing"] is False
    assert not any("缺少大盘基准" in r for r in report["debate_and_veto"]["veto_reasons"])
    md = Path(report["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    assert "标的本身即市场基准" in md


@pytest.mark.parametrize(
    ("args", "prefix", "message"),
    [
        (["--symbol", "600519"], "[INVALID_ARGUMENT]", "Cannot resolve market"),
        (["--symbol", "000001"], "[INVALID_ARGUMENT]", "Ambiguous symbol"),
        (["--symbol", "600519.SH", "--days", "30"], "[INVALID_ARGUMENT]", "--days must be between 60 and 500"),
        (["--symbol", "600519.SH", "--offline"], "[DATA_UNAVAILABLE]", "网络已禁用"),
    ],
)
def test_cli_errors_are_labelled_without_traceback(tmp_path: Path, args: list[str], prefix: str, message: str) -> None:
    import subprocess
    import sys

    script = Path(__file__).resolve().parent.parent / "scripts" / "analyze_and_forecast.py"
    proc = subprocess.run(
        [sys.executable, str(script), *args, "--output-dir", str(tmp_path / "out")],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode != 0
    assert prefix in proc.stderr and message in proc.stderr
    assert "Traceback" not in proc.stderr


def test_cli_help_renders() -> None:
    import subprocess
    import sys

    script = Path(__file__).resolve().parent.parent / "scripts" / "analyze_and_forecast.py"
    proc = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and "--max-risk-pct" in proc.stdout


# ---------------------------------------------------------------------------
# 数据时效：最近已收盘交易日不在数据里时只披露、不补数
# ---------------------------------------------------------------------------
def test_latest_closed_session_follows_exchange_clock_and_calendar() -> None:
    sh, hk, ny = ZoneInfo("Asia/Shanghai"), ZoneInfo("Asia/Hong_Kong"), ZoneInfo("America/New_York")
    assert latest_closed_session("CN", datetime(2026, 10, 16, 15, 19, tzinfo=sh)) == "2026-10-16"
    assert latest_closed_session("CN", datetime(2026, 10, 16, 14, 59, tzinfo=sh)) == "2026-10-15"
    assert latest_closed_session("CN", datetime(2026, 10, 17, 10, 0, tzinfo=sh)) == "2026-10-16"  # 周六
    assert latest_closed_session("CN", datetime(2026, 10, 8, 9, 0, tzinfo=sh)) == "2026-09-30"  # 国庆休市后
    assert latest_closed_session("HK", datetime(2026, 10, 16, 16, 5, tzinfo=hk)) == "2026-10-15"  # 收市竞价未结束
    assert latest_closed_session("US", datetime(2026, 10, 16, 3, 19, tzinfo=ny)) == "2026-10-15"


def test_missing_closed_session_is_disclosed_not_filled(replay_recorded) -> None:
    # 录制数据最后一根为 2026-10-15；北京时间 10-16 15:19 已收盘，但新浪日线尚未入库
    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ_BASE}
    bundle = load_bundle(req, now=datetime(2026, 10, 16, 7, 19, tzinfo=timezone.utc))
    assert bundle["target"]["bars"][-1]["day"] == "2026-10-15"
    assert bundle["provenance"]["missing_closed_session"] == "2026-10-16"
    report = analyze_bundle(bundle, req, include_replay=False)
    assert report["quality"]["status"] == "OK"
    note = next(n for n in report["quality"]["notes"] if n.startswith("数据只到 2026-10-15"))
    assert "2026-10-16" in note and "不含该日行情" in note
    assert note in render_report_markdown(report)
    # 收盘前 2026-10-15 就是最近已收盘交易日，不应提示
    early = load_bundle(req, now=datetime(2026, 10, 16, 6, 59, tzinfo=timezone.utc))
    assert early["provenance"]["missing_closed_session"] is None
    # 指定历史 as_of 复盘时不做时效判断
    hist = load_bundle({**req, "as_of": "2026-09-30"}, now=NOW)
    assert hist["provenance"]["missing_closed_session"] is None


def test_win_rate_interval_does_not_collapse_on_small_samples() -> None:
    from evaluation import _summarize_returns, _wilson_ci

    # 3 笔全亏：Bootstrap 会给出 0%~0% 的虚假确定性，Wilson 区间如实反映小样本的不确定性
    assert _wilson_ci(0, 3) == [0.0, 56.15]
    assert _wilson_ci(3, 3) == [43.85, 100.0]
    assert _wilson_ci(1, 1) is None
    row = _summarize_returns([-0.02, -0.05, -0.03], years=1.0)
    assert row["win_rate_pct"] == 0.0 and row["win_rate_95ci_pct"] == [0.0, 56.15]
    assert row["mean_return_95ci_pct"][0] < row["mean_return_95ci_pct"][1] < 0


def test_report_md_explains_settlement_codes_in_chinese() -> None:
    from render_report_md import SETTLEMENT_CN

    from market_rules import get_market_microstructure_rules

    codes = {
        get_market_microstructure_rules(m, a, s)["settlement"]
        for m, a, s in (("CN", "equity", "600519.SH"), ("CN", "index", "000300.SH"), ("HK", "equity", "00700.HK"), ("US", "equity", "NVDA.US"))
    }
    assert codes <= set(SETTLEMENT_CN), f"报告缺少交收制度的中文说明: {codes - set(SETTLEMENT_CN)}"
