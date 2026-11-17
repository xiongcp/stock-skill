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
from conftest import load_v6
from contract_v6 import artifact_files, build_result
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
    assert report["triple_barrier_plan"]["position_sizing"]["lot_size"] == 200
    assert report["meta"]["name"] == "小米集团-W" and report["meta"]["board_lot"] == 200
    # 截至 2026-10-15 风控否决不给股数；2025-09-29 为未被否决的做多确认，股数按每手 200 股取整
    hist_req = {**req, "as_of": "2025-09-29"}
    hist = analyze_bundle(load_bundle(hist_req, now=NOW), hist_req, include_replay=False)
    assert hist["meta_labeling_decision"]["action"] == "LONG_CONFIRMED" and not hist["debate_and_veto"]["risk_officer_veto"]
    shares = hist["triple_barrier_plan"]["position_sizing"]["recommended_shares"]
    assert shares is not None and shares > 0 and shares % 200 == 0


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


@pytest.mark.slow
def test_report_md_numbers_match_json(cn_pipeline) -> None:
    report, md, backtest = cn_pipeline
    result, _ = load_v6(report)
    meta, fp = result["meta"], result["volatility_range"]
    assert md.startswith(f"# {meta['name']}（{meta['symbol']}）")
    assert f"数据截至 **{meta['as_of']}**" in md
    assert f"**{result['verdict']['rule_score']:.1f} / 100**" in md
    assert "不是胜率" in md
    plan = result["plan"]["long"]
    assert f"{plan['entry_zone'][0]:.2f} ~ {plan['entry_zone'][1]:.2f}" in md
    assert f"| {plan['stop_loss_barrier']:.2f} | {plan['take_profit_1']:.2f} |" in md
    last = len(fp["horizon_dates"]) - 1
    assert f"| {fp['horizon_dates'][last]} | {last + 1} | {fp['q10'][last]:.2f} |" in md
    assert f"{fp['q90'][last]:.2f} |" in md
    cov80 = fp["calibration_audit"]["nominal_80pct_empirical_coverage"]
    assert f"80% 区间 {cov80 * 100:.1f}%" in md
    # 审计期覆盖全部 760 根历史，15 日区间约 42 个独立样本、覆盖率达标：报告显示已达标并说明门槛
    assert fp["calibration_audit"]["effective_independent_samples"] >= 20
    assert fp["promotion_status"] == "PROMOTED"
    assert "状态：已达标" in md and "约 20 个以上独立样本" in md
    trades = backtest["backtest"]["all_long_signals"]["trades"]
    assert f"| 全部做多信号 | {trades} |" in md
    assert md.rstrip().endswith("*以上为规则化技术分析，仅供研究参考，不构成投资建议。*")


@pytest.mark.slow
def test_report_md_puts_veto_before_conclusions(cn_pipeline) -> None:
    report, md, _ = cn_pipeline
    verdict = load_v6(report)[0]["verdict"]
    if verdict["risk_veto"]:
        assert md.index("**风控否决**") < md.index("## 1. 结论速览")
        for reason in verdict["veto_reasons"]:
            assert reason in md
    else:
        assert "**风控否决**" not in md


@pytest.mark.slow
def test_report_md_hides_all_prices_when_data_unavailable(cn_pipeline, tmp_path: Path) -> None:
    from render_html import render_html

    report, _, _ = cn_pipeline
    result, chart = load_v6(report)
    broken = copy.deepcopy(result)
    broken["quality"]["status"] = "UNAVAILABLE"
    broken["quality"]["warnings"] = ["标的行情为合成数据，价格与形态均非真实"]
    md = render_report_markdown(broken)
    assert "数据不可用" in md and "标的行情为合成数据" in md
    for section in ("## 1.", "## 3.", "## 4.", "## 6."):
        assert section not in md
    stop = f"{result['plan']['long']['stop_loss_barrier']:.2f}"
    assert stop not in md
    html_out = render_html(broken, chart, tmp_path / "unavailable.html")
    html_text = html_out.read_text(encoding="utf-8")
    assert stop not in html_text
    assert 'data-quality-status="UNAVAILABLE"' in html_text


@pytest.mark.slow
def test_report_md_zero_trade_rows_show_dash(cn_pipeline) -> None:
    report, _, backtest = cn_pipeline
    bt = copy.deepcopy(backtest)
    bt["backtest"]["long_confirmed"] = {"trades": 0}
    md = render_report_markdown(load_v6(report)[0], bt)
    assert "| 确认做多 | 0 | — | — | — | — |" in md


RANGE_FIRST_HEADERS = [
    "## 1. 结论速览", "## 2. 大盘环境", "## 3. 未来 ", "## 4. 风控计划", "## 5. 结构与条件推演",
    "## 6. 形态与信号证据", "## 7. 历史计划回放回测", "## 8. 产物文件",
]


def _lead_after(md: str, header: str) -> str:
    """标题行之后隔一个空行的那一行。"""
    lines = md.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith(header))
    assert lines[i + 1] == ""
    return lines[i + 2]


@pytest.mark.slow
def test_report_md_puts_range_and_risk_before_structure(cn_pipeline) -> None:
    _, md, _ = cn_pipeline
    headers = [ln for ln in md.splitlines() if ln.startswith("## ")]
    assert len(headers) == len(RANGE_FIRST_HEADERS)
    for got, want in zip(headers, RANGE_FIRST_HEADERS):
        assert got.startswith(want), (got, want)
    assert "正常波动范围" in headers[2]


@pytest.mark.slow
def test_report_md_lead_sentences_in_both_veto_states(cn_pipeline) -> None:
    from render_report_md import RISK_LEAD_CN, STRUCTURE_LEAD_CN

    assert RISK_LEAD_CN == "做多计划的盈亏主要取决于持有期大盘涨跌；计划的作用是把亏损限定在预算内，不代表方向判断。"
    assert STRUCTURE_LEAD_CN == "以下只描述价格结构和条件路径，不预示涨跌方向。"
    report, _, backtest = cn_pipeline
    result = load_v6(report)[0]
    flipped = copy.deepcopy(result)
    flipped["verdict"]["risk_veto"] = not result["verdict"]["risk_veto"]
    flipped["verdict"]["veto_reasons"] = [] if result["verdict"]["risk_veto"] else ["大盘环境评分偏弱"]
    for res in (result, flipped):
        md = render_report_markdown(res, backtest)
        assert _lead_after(md, "## 4. 风控计划") == RISK_LEAD_CN
        assert _lead_after(md, "## 5. 结构与条件推演") == STRUCTURE_LEAD_CN
        assert md.count(RISK_LEAD_CN) == 1 and md.count(STRUCTURE_LEAD_CN) == 1


@pytest.mark.slow
def test_report_md_digest_and_summary_order(cn_pipeline) -> None:
    report, md, _ = cn_pipeline
    digest = next(ln for ln in md.splitlines() if "直出摘要" in ln)
    order = [digest.index(k) for k in ("最新收盘", "正常波动范围", "三重屏障计划", "当前评级为")]
    assert order == sorted(order)
    if load_v6(report)[0]["verdict"]["risk_veto"]:
        assert digest.split("：", 1)[1].startswith("【风控否决")
    bullets = [md.index(k) for k in ("- 直出摘要", "- 最新收盘：", "- 结论：", "- 规则分：")]
    assert bullets == sorted(bullets)


@pytest.mark.slow
def test_report_md_unavailable_has_no_lead_sentences(cn_pipeline) -> None:
    from render_report_md import RISK_LEAD_CN, STRUCTURE_LEAD_CN

    broken = copy.deepcopy(load_v6(cn_pipeline[0])[0])
    broken["quality"]["status"] = "UNAVAILABLE"
    md = render_report_markdown(broken)
    assert RISK_LEAD_CN not in md and STRUCTURE_LEAD_CN not in md and "## " not in md


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
    ("args", "code", "exit_code", "message"),
    [
        (["--symbol", "600519"], "INVALID_ARGUMENT", 2, "Cannot resolve market"),
        (["--symbol", "000001"], "INVALID_ARGUMENT", 2, "Ambiguous symbol"),
        (["--symbol", "600519.SH", "--days", "30"], "INVALID_ARGUMENT", 2, "--days must be between 60 and 500"),
        (["--symbol", "600519.SH", "--offline"], "DATA_UNAVAILABLE", 3, "网络已禁用"),
    ],
)
def test_cli_errors_are_labelled_without_traceback(tmp_path: Path, args: list[str], code: str, exit_code: int, message: str) -> None:
    import subprocess
    import sys

    script = Path(__file__).resolve().parent.parent / "scripts" / "analyze_and_forecast.py"
    proc = subprocess.run(
        [sys.executable, str(script), *args, "--output-dir", str(tmp_path / "out")],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == exit_code
    assert f"[{code}]" in proc.stderr and message in proc.stderr
    assert "Traceback" not in proc.stderr
    envelope = json.loads(proc.stdout)
    assert envelope["status"] == "error" and envelope["error"]["code"] == code
    assert message in envelope["error"]["message"]


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
    assert note in render_report_markdown(build_result(report, artifact_files("600519.SH", False)))
    # 收盘前 2026-10-15 就是最近已收盘交易日，不应提示
    early = load_bundle(req, now=datetime(2026, 10, 16, 6, 59, tzinfo=timezone.utc))
    assert early["provenance"]["missing_closed_session"] is None
    # 指定历史 as_of 复盘时不做时效判断
    hist = load_bundle({**req, "as_of": "2026-09-30"}, now=NOW)
    assert hist["provenance"]["missing_closed_session"] is None


def test_delisted_or_stale_ticker_and_delisting_period_trigger_risk_veto(
    monkeypatch: pytest.MonkeyPatch, replay_recorded
) -> None:
    """退市/长期停牌僵尸行情（落后基准 > 5 个交易日）与退市整理期简称，必须降级为 PARTIAL 并触发独立风控官否决。"""
    from conftest import load_recorded
    from evaluation import _causal_is_st_at_cutoff
    from market_rules import count_missing_trading_sessions, is_delisting_instrument

    assert is_delisting_instrument("600086.SH", "退市金钰")
    assert is_delisting_instrument("002477.SZ", "雏鹰退")
    assert is_delisting_instrument("000001.US", "ACME (DELISTED)")
    assert not is_delisting_instrument("600519.SH", "贵州茅台")
    assert not is_delisting_instrument("600289.SH", "ST信通")
    # 2026-09-30 与 2026-10-08 之间隔着国庆长假（10-01..10-07 休市），只差 1 个交易日
    assert count_missing_trading_sessions("2026-09-30", "2026-10-08", "CN") == 1

    orig_sina = data_gateway.fetch_sina_daily_bars

    def stale_target_sina(code: str, datalen: int = 450) -> list[dict]:
        rows = orig_sina(code, datalen)
        if code == "sh600519":
            return [b for b in rows if b["day"] <= "2026-08-14"]
        return rows

    monkeypatch.setattr(data_gateway, "fetch_sina_daily_bars", stale_target_sina)
    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "account_equity": 500000.0, **REQ_BASE}
    bundle = load_bundle(req, now=NOW)
    assert bundle["provenance"]["stale_sessions"] > 5
    rep = analyze_bundle(bundle, req, include_replay=False)
    assert rep["quality"]["status"] == "PARTIAL"
    assert any("疑似已退市或长期停牌" in w for w in rep["quality"]["warnings"])
    assert rep["debate_and_veto"]["risk_officer_veto"] is True
    assert "STALE_OR_DELISTED" in rep["debate_and_veto"]["veto_codes"]
    assert rep["meta_labeling_decision"]["action"] == "DEFENSIVE_AVOID"
    assert rep["triple_barrier_plan"]["position_sizing"]["recommended_shares"] is None

    # 若用户显式指定 --as-of 2026-08-14 复盘当时截面，则基准同步对齐至 2026-08-14，不误判为停牌/退市
    hist_bundle = load_bundle({**req, "as_of": "2026-08-14"}, now=NOW)
    assert hist_bundle["provenance"]["stale_sessions"] == 0
    hist_rep = analyze_bundle(hist_bundle, {**req, "as_of": "2026-08-14"}, include_replay=False)
    assert "STALE_OR_DELISTED" not in hist_rep["debate_and_veto"]["veto_codes"]

    # 退市整理期简称（如「退市某某」「某某退」）直接触发 DELISTING_RISK 否决
    delist_req = {**req, "as_of": "2026-08-14", "name": "退市测试"}
    delist_rep = analyze_bundle(hist_bundle, delist_req, include_replay=False)
    assert delist_rep["quality"]["status"] == "PARTIAL"
    assert "DELISTING_RISK" in delist_rep["debate_and_veto"]["veto_codes"]
    assert delist_rep["meta_labeling_decision"]["action"] == "DEFENSIVE_AVOID"
    assert delist_rep["triple_barrier_plan"]["position_sizing"]["recommended_shares"] is None

    # 因果 ST 校验：比亚迪 (002594.SZ) 在 2026-07-03 前存在 >5.5% 真实波动，即使假定今日为 ST 也不能回填到当时；而真实 ST信通 (600289.SH) 全程无 >5.5% 波动，保持 True
    byd_bars = [b for b in load_recorded("sina_daily/sz002594.json") if b["day"] <= "2026-07-03"]
    st_bars = [b for b in load_recorded("sina_daily/sh600289.json") if b["day"] <= "2026-07-03"]
    assert _causal_is_st_at_cutoff(byd_bars, len(byd_bars) - 1, "CN", "002594.SZ", base_is_st=True) is False
    assert _causal_is_st_at_cutoff(st_bars, len(st_bars) - 1, "CN", "600289.SH", base_is_st=True) is True


@pytest.mark.slow
def test_backtest_does_not_backfill_current_delisting_name_into_history(replay_recorded) -> None:
    """退市整理期简称只反映当下：回测历史截面不得因当前简称/厂商快照简称含「退市」而全部被 DELISTING_RISK 否决。"""
    from evaluation import run_plan_backtest
    from market_rules import is_delisting_instrument, strip_delisting_marks

    assert strip_delisting_marks("退市金钰") == "金钰"
    assert strip_delisting_marks("雏鹰退") == "雏鹰"
    assert strip_delisting_marks("ACME (DELISTED)") == "ACME"
    assert strip_delisting_marks("贵州茅台") == "贵州茅台"
    assert not is_delisting_instrument("", strip_delisting_marks("*ST退市X"))

    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ_BASE}
    bundle = load_bundle(req, now=NOW)
    base = run_plan_backtest(bundle, req, horizon_bars=15, step=10)
    delisted = copy.deepcopy(bundle)
    delisted["target"]["instrument"]["name"] = "退市茅台"
    delisted["provenance"]["instrument_meta"] = {**(delisted["provenance"].get("instrument_meta") or {}), "vendor_name": "退市茅台"}
    bt = run_plan_backtest(delisted, req, horizon_bars=15, step=10)
    assert "DELISTING_RISK" not in bt["veto_reason_counts"]
    assert bt["actions_seen"] == base["actions_seen"]


def test_backtest_suspension_uses_benchmark_sessions_not_calendar() -> None:
    """停牌判定以基准的下一个交易日为准：休市表覆盖范围外（2021 年前）的节假日不得误判为停牌。"""
    from evaluation import _suspended_after

    # 2019 年国庆：基准 09-30 之后的下一个交易日是 10-08，标的同日开盘不算停牌
    bench_2019 = ["2019-09-27", "2019-09-30", "2019-10-08", "2019-10-09"]
    assert _suspended_after("2019-09-30", "2019-10-08", bench_2019, "CN") is False
    assert _suspended_after("2019-09-30", "2019-10-09", bench_2019, "CN") is True
    # 无基准时：覆盖范围外不判停牌，覆盖范围内按交易日历
    assert _suspended_after("2019-09-30", "2019-10-08", [], "CN") is False
    assert _suspended_after("2026-09-30", "2026-10-08", [], "CN") is False
    assert _suspended_after("2026-09-30", "2026-10-09", [], "CN") is True


def test_vix_fetch_range_only_widens_for_historical_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    """最新截面只拉 3 个月 VIX；只有 as_of 早于 60 天前的历史截面才拉 10 年。"""
    ranges: list[str] = []

    def fake_yahoo(symbol: str, range_str: str = "5y", datalen: int | None = None) -> list[dict]:
        ranges.append(range_str)
        return [{"day": "2024-06-14", "open": 12.0, "high": 12.5, "low": 11.8, "close": 12.2, "volume": 0}]

    monkeypatch.setattr(data_gateway, "fetch_yahoo_daily_bars", fake_yahoo)
    data_gateway._fetch_vix("2026-10-15", True, now=NOW)
    data_gateway._fetch_vix("2024-06-14", True, now=NOW)
    assert ranges == ["3mo", "10y"]


@pytest.mark.slow
def test_report_md_translates_delisting_veto_codes(cn_pipeline) -> None:
    report, _, backtest = cn_pipeline
    bt = copy.deepcopy(backtest)
    bt["backtest"]["veto_reason_counts"] = {"DELISTING_RISK": 2, "STALE_OR_DELISTED": 1}
    md = render_report_markdown(load_v6(report)[0], bt)
    assert "退市整理期或已终止上市 2 次" in md
    assert "行情长期断更（疑似退市或长期停牌） 1 次" in md
    assert "DELISTING_RISK" not in md and "STALE_OR_DELISTED" not in md


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


# ---------------------------------------------------------------------------
# 回测拆出大盘贡献（backtest.market_attribution）
# ---------------------------------------------------------------------------
MA_KEYS = {
    "available", "reason_cn", "basis", "benchmark_symbol", "trades", "mean_net_return_pct",
    "mean_benchmark_hold_return_pct", "mean_excess_vs_benchmark_pct", "mean_excess_95ci_pct",
    "benchmark_up", "benchmark_down_or_flat", "note_cn",
}
MA_NOTE = (
    "超额是相对同一批持有期内的基准收益，不是相对随机选股；单只股票的回测没有股票池，无法判断选股本身有没有贡献。"
    "持有期大盘涨跌是事后才知道的，两组数字只用于拆解已发生的成交，不能用来择时。"
    "基准收益不含交易成本；个股用含分红的复权价，基准若是价格指数则不含分红，两者口径略有差异。"
)


def _moutai_backtest():
    from evaluation import run_plan_backtest

    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ_BASE}
    bundle = load_bundle(req, now=NOW)
    return bundle, run_plan_backtest(bundle, req, horizon_bars=15, step=10)


@pytest.mark.slow
def test_market_attribution_conserves_and_matches_hand_computation(replay_recorded) -> None:
    from evaluation import _market_attribution

    bundle, bt = _moutai_backtest()
    ma = bt["market_attribution"]
    assert set(ma) == MA_KEYS and ma["note_cn"] == MA_NOTE and ma["basis"] == "ALL_LONG_SIGNALS"
    assert ma["available"] is True and ma["reason_cn"] is None and ma["benchmark_symbol"] == "000300.SH"
    assert ma["trades"] == bt["all_long_signals"]["trades"] >= 3
    assert ma["benchmark_up"]["trades"] + ma["benchmark_down_or_flat"]["trades"] == ma["trades"]
    lo, hi = ma["mean_excess_95ci_pct"]
    assert lo - 0.01 <= ma["mean_excess_vs_benchmark_pct"] <= hi + 0.01
    # 用回放给出的最近三笔真实成交，在测试里按定义独立算一遍
    trades = bt["sample_trades"]
    bars = bundle["benchmark"]["bars"]
    days = [b["day"] for b in bars]
    close = {b["day"]: float(b["close"]) for b in bars}
    nets, holds = [], []
    for t in trades:
        i = days.index(t["entry_day"])  # A 股标的的交易日都是沪深300 的交易日
        nets.append(float(t["net_return"]))
        holds.append(close[t["exit_day"]] / close[days[i - 1]] - 1.0)
    got = _market_attribution(trades, bundle["benchmark"], "600519.SH", "CN")
    n = len(trades)
    assert got["trades"] == n
    assert got["mean_net_return_pct"] == pytest.approx(sum(nets) / n * 100.0, abs=5e-4)
    assert got["mean_benchmark_hold_return_pct"] == pytest.approx(sum(holds) / n * 100.0, abs=5e-4)
    assert got["mean_excess_vs_benchmark_pct"] == pytest.approx(sum(a - b for a, b in zip(nets, holds)) / n * 100.0, abs=5e-4)
    up = [a for a, b in zip(nets, holds) if b > 0]
    assert got["benchmark_up"]["trades"] == len(up)
    assert got["benchmark_up"]["mean_net_return_pct"] == (pytest.approx(sum(up) / len(up) * 100.0, abs=5e-4) if up else None)


def test_market_attribution_uses_previous_benchmark_close_on_missing_days(replay_recorded) -> None:
    """入场日、出场日落在基准没有K线的日历日（用周六代表）：取之前最近一个基准收盘，不报错。"""
    from datetime import date, timedelta

    from evaluation import _market_attribution

    req = {"symbol": "00700.HK", "market": "HK", "asset_type": "equity", **REQ_BASE}
    bench = load_bundle(req, now=NOW)["benchmark"]
    days = [b["day"] for b in bench["bars"]]
    close = [float(b["close"]) for b in bench["bars"]]
    i = next(k for k in range(len(days) - 30, 20, -1) if date.fromisoformat(days[k]).weekday() == 4)  # 某个周五
    sat = (date.fromisoformat(days[i]) + timedelta(days=1)).isoformat()
    assert sat not in days
    # 周六"入场"：之前最近的基准收盘是周五 days[i]；周六"出场"：当天或之前最近的也是 days[i]
    t_in = {"entry_day": sat, "exit_day": days[i + 5], "net_return": 0.01}
    t_out = {"entry_day": days[i - 5], "exit_day": sat, "net_return": -0.02}
    got = _market_attribution([t_in, t_out], bench, "00700.HK", "HK")
    want = ((close[i + 5] / close[i] - 1.0) + (close[i] / close[i - 6] - 1.0)) / 2 * 100.0
    assert got["available"] is True and got["benchmark_symbol"] == "HSI.HK"
    assert got["mean_benchmark_hold_return_pct"] == pytest.approx(want, abs=5e-4)


def test_market_attribution_unavailable_cases(replay_recorded) -> None:
    from evaluation import _market_attribution

    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ_BASE}
    bench = load_bundle(req, now=NOW)["benchmark"]
    trade = {"entry_day": bench["bars"][-20]["day"], "exit_day": bench["bars"][-10]["day"], "net_return": 0.01}
    early = {"entry_day": bench["bars"][0]["day"], "exit_day": bench["bars"][5]["day"], "net_return": 0.01}
    cases = [
        (_market_attribution([trade], None, "600519.SH", "CN"), "没有大盘基准行情"),
        (_market_attribution([trade], None, "000300.SH", "CN"), "标的自身即基准"),
        (_market_attribution([trade], bench, "000300.SH", "CN"), "标的自身即基准"),
        (_market_attribution([], bench, "600519.SH", "CN"), "没有成交"),
        (_market_attribution([early], bench, "600519.SH", "CN"), "之前没有基准收盘"),
    ]
    for got, reason in cases:
        assert set(got) == MA_KEYS and got["available"] is False and reason in got["reason_cn"], reason
        assert got["trades"] == 0 and got["mean_excess_95ci_pct"] is None
        assert got["mean_net_return_pct"] is None and got["mean_benchmark_hold_return_pct"] is None
        assert got["benchmark_up"] == {"trades": 0, "mean_net_return_pct": None} == got["benchmark_down_or_flat"]
    one = _market_attribution([trade], bench, "600519.SH", "CN")
    assert one["available"] is True and one["trades"] == 1 and one["mean_excess_95ci_pct"] is None  # 少于 2 笔不给区间


@pytest.mark.slow
def test_report_md_backtest_shows_market_attribution(cn_pipeline) -> None:
    report, md, backtest = cn_pipeline
    ma = backtest["backtest"]["market_attribution"]
    assert ma["available"] is True
    line = next(ln for ln in md.splitlines() if ln.startswith("- 大盘贡献拆解"))
    assert md.index("- 同期买入持有收益") < md.index(line)
    for key in ("mean_net_return_pct", "mean_benchmark_hold_return_pct", "mean_excess_vs_benchmark_pct"):
        assert f"{ma[key]:.3f}%" in line, key
    assert f"{ma['benchmark_up']['trades']} 笔" in line and f"{ma['benchmark_down_or_flat']['trades']} 笔" in line
    assert "不是相对随机选股" in line
    # 不可用时写一行原因，不给数字
    off = copy.deepcopy(backtest)
    off["backtest"]["market_attribution"] = {**ma, "available": False, "reason_cn": "没有大盘基准行情，无法拆出大盘贡献"}
    md_off = render_report_markdown(load_v6(report)[0], off)
    assert "- 大盘贡献拆解：未计算（没有大盘基准行情，无法拆出大盘贡献）。" in md_off


@pytest.mark.slow
def test_report_md_backtest_without_market_attribution_still_renders(cn_pipeline) -> None:
    """旧版本生成的 backtest.json 没有 market_attribution：重渲染不报错，也不出现归因一行。"""
    report, _, backtest = cn_pipeline
    old = copy.deepcopy(backtest)
    old["backtest"].pop("market_attribution")
    md = render_report_markdown(load_v6(report)[0], old)
    assert "## 7. 历史计划回放回测" in md and "大盘贡献拆解" not in md
