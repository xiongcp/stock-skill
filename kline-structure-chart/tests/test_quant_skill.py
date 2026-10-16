from __future__ import annotations

import ast
import copy
import json
import math
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from PIL import Image
from conftest import RECORDED_DIR, load_recorded

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import contracts  # noqa: E402
import data_gateway  # noqa: E402
import render_structure_chart  # noqa: E402
from analysis import analyze_bundle  # noqa: E402
from analyze_and_forecast import run_pipeline  # noqa: E402
from contracts import (  # noqa: E402
    assess_data_quality,
    classify_data_source,
    sanitize_filename,
    validate_report,
)
from data_gateway import load_bundle  # noqa: E402
from evaluation import (  # noqa: E402
    label_triple_barrier_event,
    run_causal_replay_audit,
    run_plan_backtest,
)
from forecast import compute_conformal_forecast, match_historical_analogues  # noqa: E402
from indicators import compute_all_indicators, rsi  # noqa: E402
from market_rules import (  # noqa: E402
    CALENDAR_COVERAGE_END,
    CALENDAR_COVERAGE_START,
    apply_point_in_time_adjustment,
    calendar_covers,
    get_daily_price_limit_pct,
    get_market_microstructure_rules,
    is_daily_bar_complete,
    is_trading_day,
    next_trading_dates,
    rescale_bars_as_of,
    resolve_instrument,
    round_shares_to_lot,
    round_to_tick,
    round_trip_cost_rate,
    yahoo_symbol_for,
)
from patterns import (  # noqa: E402
    causal_nadaraya_watson,
    causal_swing_pivots,
    compute_volume_profile,
    detect_candlestick_patterns,
    detect_geometric_patterns,
    detect_smc_structures,
)
from render_html import render_html  # noqa: E402
from render_structure_chart import render as render_png  # noqa: E402
from structure import compute_structure_layer, fmt_price  # noqa: E402

UTC = timezone.utc


def test_contracts_reject_nan_and_quantile_crossing(replay_recorded) -> None:
    bundle = load_bundle(
        {"symbol": "000001.SH", "market": "CN", "asset_type": "index", "display_bars": 120, "horizon_bars": 15}
    )
    report = analyze_bundle(bundle, {"symbol": "000001.SH", "display_bars": 120, "horizon_bars": 15}, include_replay=False)
    validate_report(report)

    bad_nan = json.loads(json.dumps(report))
    bad_nan["multi_timeframe_score"]["rule_score"] = float("nan")
    with pytest.raises(ValueError, match="Non-finite float"):
        validate_report(bad_nan)

    bad_q = json.loads(json.dumps(report))
    bad_q["future_projection_15d"]["q25"][0] = bad_q["future_projection_15d"]["optimistic_q75"][0] + 10.0
    with pytest.raises(ValueError, match="Quantile crossing"):
        validate_report(bad_q)


def test_ambiguous_symbol_and_filename_sanitization() -> None:
    with pytest.raises(ValueError, match="Ambiguous symbol '000001'"):
        resolve_instrument("000001")

    inst_sh = resolve_instrument("000001.SH")
    assert inst_sh["asset_type"] == "index"
    inst_sz = resolve_instrument("000001.SZ")
    assert inst_sz["asset_type"] == "equity"
    assert sanitize_filename("600519.SH") == "600519.SH"
    with pytest.raises(ValueError):
        sanitize_filename("..")


def test_market_rules_price_limits_and_holidays() -> None:
    assert get_daily_price_limit_pct("CN", "index", "000001.SH") is None
    assert get_daily_price_limit_pct("CN", "equity", "600519.SH") == 0.10
    assert get_daily_price_limit_pct("CN", "equity", "300750.SZ", day_str="2026-10-15") == 0.20
    assert get_daily_price_limit_pct("CN", "equity", "300750.SZ", day_str="2020-01-15") == 0.10
    assert get_daily_price_limit_pct("CN", "equity", "688981.SH") == 0.20
    assert get_daily_price_limit_pct("CN", "equity", "830799.BJ") == 0.30
    assert get_daily_price_limit_pct("CN", "equity", "600000.SH", is_st=True, day_str="2026-07-03") == 0.05
    assert get_daily_price_limit_pct("HK", "equity", "00700.HK") is None
    assert get_daily_price_limit_pct("US", "equity", "NVDA.US") is None

    dates = next_trading_dates("2026-09-30", 3, market="CN")
    assert "2026-10-01" not in dates
    assert dates[0] == "2026-10-08"

    assert round_to_tick(412.23, market="HK", asset_type="equity") == 412.20


def test_point_in_time_adjustment_no_future_leakage() -> None:
    # 使用比亚迪 (002594.SZ) 2025-07-29 真实 10 送转 20（split_ratio=3.0）及分红事件验证零前视复权
    raw_bars = load_recorded("sina_daily/sz002594.json")
    raw_events = load_recorded("yahoo_events/002594.SZ.json")
    actions = data_gateway.build_corporate_actions(raw_bars, raw_events, include_splits=True)

    idx_pre = next(i for i, b in enumerate(raw_bars) if b["day"] == "2025-07-28")
    idx_ex = next(i for i, b in enumerate(raw_bars) if b["day"] == "2025-07-29")
    assert raw_bars[idx_pre]["close"] == 337.0
    assert raw_bars[idx_ex]["open"] == 112.01

    # 截至 2025-07-28（除权日前一日），2025-07-29 的拆送股尚未发生，当日收盘必须保持原始 337.0
    before = apply_point_in_time_adjustment(raw_bars[: idx_pre + 1], actions, mode="split", as_of_day="2025-07-28")
    assert before[-1]["close"] == 337.0

    # 截至 2025-07-29（除权当日），前一日按 1/3 拆股调整为 112.3333，且 raw_close 保留 337.0
    after_split = apply_point_in_time_adjustment(raw_bars[: idx_ex + 1], actions, mode="split", as_of_day="2025-07-29")
    assert after_split[idx_pre]["close"] == pytest.approx(337.0 / 3.0, abs=1e-4)
    assert after_split[idx_pre]["raw_close"] == 337.0

    # total_return 模式下同时扣除 2025-07-29 现金分红（每股 3.974 元），除权前一日复权价 (337.0 - 3.974)/3 = 111.0087
    after_tr = apply_point_in_time_adjustment(raw_bars[: idx_ex + 1], actions, mode="total_return", as_of_day="2025-07-29")
    assert after_tr[idx_pre]["close"] == pytest.approx((337.0 - 1.32466 * 3.0) / 3.0, abs=1e-3)


def test_indicators_and_algebraic_patterns_and_smc() -> None:
    # 使用真实录制的贵州茅台 (sh600519) 与小米集团 (1810.HK) 日线
    bars = load_recorded("sina_daily/sh600519.json")
    ind = compute_all_indicators(bars)
    snap = ind["snapshot"]
    assert snap["trend"]["ema20"] is not None and snap["trend"]["ema200"] is not None
    assert snap["trend"]["supertrend_dir"] in (-1, 1)
    assert 0.0 <= float(snap["momentum"]["rsi14"]) <= 100.0
    assert -1.0 <= float(snap["qlib_factors"]["corr_close_logvol_20d"]) <= 1.0

    # 茅台真实行情在末尾 10 根内自然出现看涨锤头线 (bar_idx=1017)
    cdl = detect_candlestick_patterns(bars, ind["series"]["atr14"], ind["series"]["rvol20"], lookback=10)
    assert any(p["pattern"] == "CDL_BULLISH_HAMMER" for p in cdl)

    # 小米集团真实行情自然检出波段双顶结构
    hk_bars = load_recorded("yahoo_daily/1810.HK.json")
    hk_ind = compute_all_indicators(hk_bars)
    hk_pivs = causal_swing_pivots(hk_bars, 0.035)
    geo = detect_geometric_patterns(hk_bars, hk_pivs, hk_ind["series"]["atr14"], hk_ind["series"]["rvol20"])
    assert any(g["pattern"].startswith("SWING_DOUBLE_TOP") for g in geo)

    pivs = causal_swing_pivots(bars, 0.035)
    smc = detect_smc_structures(bars, pivs, ind["series"]["atr14"])
    vp = compute_volume_profile(bars, lookback=120)
    assert vp["val_price"] <= vp["poc_price"] <= vp["vah_price"]
    assert "fvgs" in smc and "order_blocks" in smc


def test_causal_replay_invariance_no_lookahead() -> None:
    bars = load_recorded("sina_daily/sh000300.json")
    nw_short = causal_nadaraya_watson([b["close"] for b in bars[:251]], bandwidth=3.0)
    nw_full = causal_nadaraya_watson([b["close"] for b in bars], bandwidth=3.0)
    assert math.isclose(nw_short[250], nw_full[250], rel_tol=1e-12)

    audit = run_causal_replay_audit(bars, check_steps=6)
    assert audit["passed"] is True
    assert audit["mismatches_count"] == 0
    assert "conformal_forecast" in audit["modules_audited"]

    # C-08：历史相似 Patch 终点满足 cand_idx + H <= cur_end - L_patch（严格无重叠）
    ana = match_historical_analogues(bars, horizon_bars=15, patch_len=20, top_k=5)
    assert ana["available"] is True
    day_to_idx = {b["day"]: idx for idx, b in enumerate(bars)}
    cand_indices = [day_to_idx[m["end_day"]] for m in ana["matches"]]
    for cand_idx in cand_indices:
        assert cand_idx + 15 <= len(bars) - 1 - 20
    for i, idx_a in enumerate(cand_indices):
        for idx_b in cand_indices[i + 1 :]:
            assert abs(idx_a - idx_b) >= 20


def test_triple_barrier_t_plus_1_and_same_bar_conservative_sl() -> None:
    # 取贵州茅台 2026-09-24..2026-10-08 真实日线切片：
    # idx=1 (2026-09-28): O=1236.0, H=1244.01, L=1228.10, C=1243.88
    # idx=2 (2026-09-29): O=1244.6, H=1245.87, L=1230.88, C=1235.58
    bars = load_recorded("sina_daily/sh600519.json")[-10:-5]
    assert bars[1]["day"] == "2026-09-28" and bars[2]["day"] == "2026-09-29"
    # 设 TP=1244.0, SL=1232.0：idx=1 当日虽同时触及，但 A 股 T+1 禁止当日卖出；
    # idx=2 再次同时触及 H=1245.87>=1244.0 与 L=1230.88<=1232.0，按保守规则判先止损
    res_cn = label_triple_barrier_event(
        bars,
        entry_idx=1,
        tp_price=1244.0,
        sl_price=1232.0,
        horizon_bars=3,
        market="CN",
        asset_type="equity",
        symbol="600519.SH",
    )
    assert res_cn["exit_idx"] == 2
    assert res_cn["outcome"] == "SL_BARRIER_SAME_BAR_CONSERVATIVE"
    assert res_cn["barrier_label"] == -1

    # C-14：时间屏障最多持有 horizon_bars 根（entry_idx=1, horizon_bars=3 -> exit_idx=3, holding_bars=3，而非 4）
    res_time = label_triple_barrier_event(
        bars,
        entry_idx=1,
        tp_price=2000.0,
        sl_price=800.0,
        horizon_bars=3,
        market="HK",
        asset_type="equity",
        symbol="00700.HK",
    )
    assert res_time["outcome"] == "TIME_BARRIER"
    assert res_time["exit_idx"] == 3 and res_time["holding_bars"] == 3


@pytest.mark.parametrize(
    ("market", "symbol", "asset_type", "expected_scheme"),
    [
        ("CN", "000001.SH", "index", "cn_red_up"),
        ("CN", "600519.SH", "equity", "cn_red_up"),
        ("HK", "HSI.HK", "index", "cn_red_up"),
        ("HK", "00700.HK", "equity", "cn_red_up"),
        ("US", "SPY.US", "etf", "us_green_up"),
        ("US", "NVDA.US", "equity", "us_green_up"),
    ],
)
def test_multi_market_pipeline_and_backtest(
    replay_recorded,
    tmp_path: Path,
    market: str,
    symbol: str,
    asset_type: str,
    expected_scheme: str,
) -> None:
    req = {
        "market": market,  # type: ignore[typeddict-item]
        "symbol": symbol,
        "asset_type": asset_type,  # type: ignore[typeddict-item]
        "display_bars": 120,
        "structure_bars": 120,
        "horizon_bars": 15,
        "account_equity": 200000.0,
        "max_risk_pct": 0.01,
    }
    report = run_pipeline(req, output_dir=tmp_path / market, run_backtest=True, backtest_step=10)
    paths = report["rendered_paths"]
    assert Path(paths["result_json"]).exists()
    assert Path(paths["preview_png"]).exists()
    assert Path(paths["dashboard_html"]).exists()
    assert Path(paths["backtest_json"]).exists()

    # T-04：强语义与数值断言
    assert report["schema_version"] == "5.1.0"
    assert report["quality"]["status"] == "OK"
    assert report["quality"]["data_source_class"] == "LIVE"
    assert report["meta"]["color_scheme"] == expected_scheme
    assert report["meta"]["as_of"] == "2026-10-15"
    assert report["mathematical_indicators"]["trend"]["ema200"] is not None
    assert 0.0 <= report["multi_timeframe_score"]["rule_score"] <= 100.0
    fc = report["future_projection_15d"]
    assert fc["calibration_audit"]["calibrated"] is True
    assert fc["calibration_audit"]["q50_mae_pct"] == fc["calibration_audit"]["random_walk_mae_pct"]
    if asset_type == "index":
        assert report["triple_barrier_plan"]["position_sizing"]["recommended_shares"] is None
    else:
        assert report["triple_barrier_plan"]["position_sizing"]["recommended_shares"] is not None

    html_text = Path(paths["dashboard_html"]).read_text(encoding="utf-8")
    assert "https://cdn" not in html_text
    assert "Time-Travel Replay" in html_text


def test_html_xss_escaping(replay_recorded, tmp_path: Path) -> None:
    bundle = load_bundle({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15})
    bundle["target"]["instrument"]["name"] = "</script><script>window.__XSS=1;</script>"
    report = analyze_bundle(bundle, {"symbol": "600519.SH", "display_bars": 120, "horizon_bars": 15}, include_replay=False)
    out_html = render_html(report, tmp_path / "xss_test.html")
    content = out_html.read_text(encoding="utf-8")
    assert "</script><script>window.__XSS=1;" not in content
    assert "\\u003c/script\\u003e" in content


# ---------------------------------------------------------------------------
# P0 数据真实性回归测试（A-01 ~ A-06，全部使用真实录制数据）
# ---------------------------------------------------------------------------


def test_a01_unregistered_symbols_map_to_real_vendor_codes() -> None:
    assert resolve_instrument("601318.SH")["sina"] == "sh601318"
    assert resolve_instrument("300059.SZ")["sina"] == "sz300059"
    assert resolve_instrument("830799.BJ")["sina"] == "bj830799"
    assert yahoo_symbol_for("01810.HK") == "1810.HK"
    assert yahoo_symbol_for("00700.HK") == "0700.HK"
    assert yahoo_symbol_for("HSI.HK") == "^HSI"
    assert yahoo_symbol_for("NVDA.US") == "NVDA"


def test_a01_unregistered_a_share_and_hk_are_fetched_live(replay_recorded) -> None:
    bundle_cn = load_bundle(
        {"symbol": "601318.SH", "market": "CN", "asset_type": "equity"},
        now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC),
    )
    assert bundle_cn["provenance"]["source"] == "SINA_LIVE_DAILY"
    assert bundle_cn["benchmark"]["source"] == "SINA_LIVE_DAILY"
    assert bundle_cn["target"]["bars"][-1]["day"] == "2026-10-15"

    bundle_hk = load_bundle(
        {"symbol": "01810.HK", "market": "HK", "asset_type": "equity"},
        now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC),
    )
    assert bundle_hk["provenance"]["source"] == "YAHOO_DAILY(1810.HK)"
    assert bundle_hk["benchmark"]["source"] == "YAHOO_DAILY(^HSI)"


def test_a02_synthetic_fallback_is_refused_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def network_down(*_args, **_kwargs):
        raise OSError("network down")

    monkeypatch.setattr(data_gateway, "fetch_sina_daily_bars", network_down)
    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    with pytest.raises(RuntimeError, match="network down"):
        load_bundle(req)
    with pytest.raises(RuntimeError, match="拒绝使用合成数据"):
        load_bundle(req, allow_network=False)
    with pytest.raises(RuntimeError):
        run_pipeline(req, output_dir=tmp_path / "out", allow_network=False)
    assert not any((tmp_path / "out").iterdir())

    cli = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "analyze_and_forecast.py"),
            "--market",
            "CN",
            "--symbol",
            "600519.SH",
            "--asset-type",
            "equity",
            "--offline",
            "--output-dir",
            str(tmp_path / "cli"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert cli.returncode != 0
    assert "DATA_UNAVAILABLE" in cli.stderr


def test_a03_png_and_html_flag_non_live_data(tmp_path: Path) -> None:
    # 使用真实录制的茅台日线通过 --input 本地文件加载（缺少大盘基准 -> PARTIAL 告警横幅与水印）
    input_file = RECORDED_DIR / "sina_daily" / "sh600519.json"
    report = run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path,
        input_path=input_file,
        allow_network=False,
    )
    assert report["quality"]["status"] == "PARTIAL"
    assert report["legacy_summary"]["data_warning"]
    html_text = Path(report["rendered_paths"]["dashboard_html"]).read_text(encoding="utf-8")
    static_part = html_text.split('<script id="report-data"')[0]
    assert 'data-quality-status="PARTIAL"' in static_part
    assert "缺少大盘基准行情" in static_part

    # PNG：有告警时主图顶部为红色横幅，无告警时同一位置为深色背景
    bars = load_recorded("sina_daily/sh600519.json")[-450:]
    clean = render_png(bars, None, "t", tmp_path / "clean.png", show=120)
    warned = render_png(bars, None, "t", tmp_path / "warned.png", show=120, data_source="LOCAL_FILE", data_warning="缺少大盘基准")
    assert clean["data_warning"] is None and warned["data_warning"] == "缺少大盘基准"
    px_clean = Image.open(tmp_path / "clean.png").convert("RGB").getpixel((80, 160))
    px_warned = Image.open(tmp_path / "warned.png").convert("RGB").getpixel((80, 160))
    assert px_warned[0] > 150 and px_warned[1] < 80
    assert px_clean[0] < 80


def test_a04_quality_status_uses_source_enum() -> None:
    ok = assess_data_quality("YAHOO_DAILY(NVDA)", "YAHOO_DAILY(SPY)", needs_benchmark=True)
    assert ok["status"] == "OK" and ok["warnings"] == []
    assert assess_data_quality("SINA_LIVE_DAILY", "DETERMINISTIC_SYNTHETIC_FIXTURE", needs_benchmark=True)["status"] == "UNAVAILABLE"
    assert assess_data_quality("DETERMINISTIC_OFFLINE_FIXTURE", None, needs_benchmark=False)["status"] == "UNAVAILABLE"
    assert assess_data_quality("LOCAL_FILE(bars.json)", None, needs_benchmark=True)["status"] == "PARTIAL"
    assert assess_data_quality("SINA_LIVE_DAILY", None, needs_benchmark=False)["status"] == "OK"
    assert assess_data_quality("SOMETHING_ELSE", None, needs_benchmark=False)["status"] == "PARTIAL"


def test_a05_vix_is_fetched_or_null_never_hardcoded(monkeypatch: pytest.MonkeyPatch, replay_recorded) -> None:
    after_close_ny = datetime(2026, 10, 16, 23, 0, tzinfo=UTC)
    # 1. 真实录制的 VIX 在 2026-06-10 收于 22.22 (>20)，触发波动率放大
    req_high_vix = {
        "symbol": "NVDA.US",
        "market": "US",
        "asset_type": "equity",
        "as_of": "2026-06-10",
        "display_bars": 120,
        "horizon_bars": 15,
    }
    live_high = load_bundle(req_high_vix, now=after_close_ny)
    assert live_high["extra_context"]["vix"] == 22.22
    rep_high = analyze_bundle(live_high, req_high_vix, include_replay=False)
    scaling = rep_high["future_projection_15d"]["volatility_scaling"]
    assert scaling["vix"] == 22.22 and "^VIX" in scaling["vix_source"]
    assert scaling["vol_scale_multiplier"] > 1.0

    # 2. 本地文件输入或 VIX 接口异常时，vix 必须为 None，绝不硬编码 19.5
    local_bundle = load_bundle(
        {"symbol": "NVDA.US", "market": "US", "asset_type": "equity"},
        input_path=RECORDED_DIR / "yahoo_daily" / "NVDA.json",
        allow_network=False,
    )
    assert local_bundle["extra_context"]["vix"] is None


def test_a06_incomplete_last_bar_is_excluded(replay_recorded) -> None:
    hk_morning_oct15 = datetime(2026, 10, 15, 3, 12, tzinfo=UTC)  # 2026-10-15 11:12 HKT（早盘未收盘）
    assert not is_daily_bar_complete("2026-10-15", "HK", hk_morning_oct15)
    assert is_daily_bar_complete("2026-10-14", "HK", hk_morning_oct15)
    assert is_daily_bar_complete("2026-10-15", "HK", datetime(2026, 10, 15, 8, 30, tzinfo=UTC))
    assert not is_daily_bar_complete("2026-10-15", "US", datetime(2026, 10, 15, 18, 0, tzinfo=UTC))
    assert not is_daily_bar_complete("2026-10-15", "CN", datetime(2026, 10, 15, 3, 0, tzinfo=UTC))

    req = {"symbol": "00700.HK", "market": "HK", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    bundle = load_bundle(req, now=hk_morning_oct15)
    assert bundle["target"]["bars"][-1]["day"] == "2026-10-14"
    assert bundle["target"]["intraday"]["day"] == "2026-10-15"
    assert bundle["benchmark"]["bars"][-1]["day"] == "2026-10-14"
    report = analyze_bundle(bundle, req, include_replay=False)
    assert report["meta"]["as_of"] == "2026-10-14"
    assert report["series_export"]["intraday"]["day"] == "2026-10-15"
    assert report["quality"]["status"] == "OK"
    assert any("2026-10-15" in note for note in report["quality"]["notes"])


# ---------------------------------------------------------------------------
# P1/P2 制度规则回归测试（R-01 ~ R-12，全部使用真实录制数据）
# ---------------------------------------------------------------------------


def test_p1_st_and_ipo_price_limits_by_board_date_and_pipeline(replay_recorded) -> None:
    # 主板 ST：2026-07-06 前 ±5%，2026-07-06 起 ±10%
    assert get_daily_price_limit_pct("CN", "equity", "600000.SH", is_st=True, day_str="2026-07-03") == 0.05
    assert get_daily_price_limit_pct("CN", "equity", "600000.SH", is_st=True, day_str="2026-07-06") == 0.10
    assert get_daily_price_limit_pct("CN", "equity", "000002.SZ", is_st=True, day_str="2026-10-15") == 0.10
    assert get_daily_price_limit_pct("CN", "equity", "300001.SZ", is_st=True, day_str="2026-10-15") == 0.20
    assert get_daily_price_limit_pct("CN", "equity", "300001.SZ", is_st=True, day_str="2020-01-15") == 0.05
    assert get_daily_price_limit_pct("CN", "equity", "688001.SH", is_st=True, day_str="2026-10-15") == 0.20
    assert get_daily_price_limit_pct("CN", "equity", "830799.BJ", is_st=True, day_str="2026-10-15") == 0.30

    # R-01 全链路透传：通过 name="*ST万科" 在 2026-07-03 截面运行，自动识别 ST 并应用 ±5% 涨跌停
    req_st = {"symbol": "000002.SZ", "name": "*ST万科", "market": "CN", "asset_type": "equity", "as_of": "2026-07-03"}
    bundle_st = load_bundle(req_st, now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
    rep_st = analyze_bundle(bundle_st, req_st, include_replay=False)
    assert rep_st["meta"]["is_st"] is True
    assert rep_st["triple_barrier_plan"]["market_microstructure"]["daily_price_limit_pct"] == 0.05
    assert rep_st["future_projection_15d"]["daily_price_limit_pct"] == 0.05

    # R-11：新股前 5 个交易日无涨跌停（is_ipo_no_limit=True）
    req_ipo = {"symbol": "000002.SZ", "market": "CN", "asset_type": "equity", "is_ipo_no_limit": True}
    bundle_ipo = load_bundle(req_ipo, now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
    rep_ipo = analyze_bundle(bundle_ipo, req_ipo, include_replay=False)
    assert rep_ipo["triple_barrier_plan"]["market_microstructure"]["daily_price_limit_pct"] is None
    assert rep_ipo["future_projection_15d"]["daily_price_limit_pct"] is None


def test_p1_hk_spread_table_full_bands_and_reductions() -> None:
    assert round_to_tick(612.30, "HK", "equity", "2026-10-15") == 612.5
    assert round_to_tick(1234.3, "HK", "equity", "2026-10-15") == 1234.0
    assert round_to_tick(3001.0, "HK", "equity", "2026-10-15") == 3002.0
    assert round_to_tick(6003.0, "HK", "equity", "2026-10-15") == 6005.0
    assert round_to_tick(15.01, "HK", "equity", "2025-08-01") == 15.02
    assert round_to_tick(15.01, "HK", "equity", "2025-08-04") == 15.01
    assert round_to_tick(27.03, "HK", "equity", "2025-08-01") == 27.05
    assert round_to_tick(27.03, "HK", "equity", "2025-08-04") == 27.04
    assert round_to_tick(77.03, "HK", "equity", "2026-10-15") == 77.05
    assert round_to_tick(5.003, "HK", "equity", "2026-07-31") == 5.0
    assert round_to_tick(5.003, "HK", "equity", "2026-08-03") == 5.005
    assert round_to_tick(15.01, "HK", "etf", "2026-10-15") == 15.02
    assert round_to_tick(412.23, "HK", "equity", "2026-10-15") == 412.2


def test_p1_hk_stamp_duty_both_sides() -> None:
    hk = get_market_microstructure_rules("HK", "equity", "00700.HK")
    assert hk["stamp_duty_buy_bps"] == 10.0 and hk["stamp_duty_sell_bps"] == 10.0
    assert round_trip_cost_rate(hk) == pytest.approx((3.0 * 2 + 10.0 + 10.0) / 10000.0)
    hk_etf = get_market_microstructure_rules("HK", "etf", "02800.HK")
    assert hk_etf["stamp_duty_buy_bps"] == 0.0 and hk_etf["stamp_duty_sell_bps"] == 0.0
    cn = get_market_microstructure_rules("CN", "equity", "600519.SH")
    assert cn["stamp_duty_buy_bps"] == 0.0 and cn["stamp_duty_sell_bps"] == 5.0


def test_p1_trading_calendar_fixes_and_coverage() -> None:
    assert CALENDAR_COVERAGE_START == "2021-01-01"
    assert not is_trading_day(date(2022, 10, 3), "CN")  # 2022 国庆
    assert not is_trading_day(date(2023, 1, 23), "CN")  # 2023 春节
    assert not is_trading_day(date(2025, 6, 2), "CN")   # 2025 端午
    assert not is_trading_day(date(2026, 2, 23), "CN")  # 2026 春节休市至 2-23
    assert not is_trading_day(date(2025, 1, 9), "US")   # 卡特国葬日
    assert not is_trading_day(date(2027, 1, 1), "US")
    assert not is_trading_day(date(2027, 12, 24), "US")
    assert not is_trading_day(date(2027, 2, 9), "HK")
    assert not is_trading_day(date(2027, 12, 27), "HK")
    assert next_trading_dates("2026-02-13", 1, market="CN") == ["2026-02-24"]
    assert CALENDAR_COVERAGE_END["CN"] == "2026-12-31"
    assert calendar_covers("2026-12-31", "CN") and not calendar_covers("2027-01-04", "CN")
    assert calendar_covers("2027-06-01", "US")

    # 使用真实录制的茅台日线，当预测步数跨过 2026-12-31（例如 horizon_bars=60）时，自动标注超出覆盖范围的估算日期
    bars = load_recorded("sina_daily/sh600519.json")
    meta = {"market": "CN", "asset_type": "equity", "symbol": "600519.SH"}
    fc = compute_conformal_forecast(bars, meta=meta, horizon_bars=60)
    assert fc["calendar_coverage"]["covered"] is False
    assert fc["calendar_coverage"]["estimated_dates"][0] >= "2027-01-01"


def test_p1_us_pdt_rule_updated() -> None:
    us = get_market_microstructure_rules("US", "equity", "NVDA.US")
    assert us["settlement"] == "T+1_SEC_SETTLEMENT"
    assert "PDT" in us["day_trading_rule"] and "2026-06-04" in us["day_trading_rule"]


def test_p1_asset_type_lot_step_and_tick_precision() -> None:
    assert resolve_instrument("000002.SZ")["asset_type"] == "equity"
    assert resolve_instrument("000858.SZ")["asset_type"] == "equity"
    assert resolve_instrument("002594.SZ")["asset_type"] == "equity"
    assert resolve_instrument("000905.SH")["asset_type"] == "index"
    assert resolve_instrument("399300.SZ")["asset_type"] == "index"
    etf = resolve_instrument("510300.SH")
    assert etf["asset_type"] == "etf" and etf["lot_size"] == 100
    assert resolve_instrument("159915.SZ")["asset_type"] == "etf"
    assert resolve_instrument("000002.SZ")["lot_size"] == 100
    etf_rules = get_market_microstructure_rules("CN", "etf", "510300.SH")
    assert etf_rules["settlement"] == "T+1" and etf_rules["lot_size"] == 100 and etf_rules["stamp_duty_sell_bps"] == 0.0

    # R-09：科创板最低 200 股 + 1 股递增；北交所最低 100 股 + 1 股递增；主板整 100 股
    star_rules = get_market_microstructure_rules("CN", "equity", "688981.SH")
    assert star_rules["min_order_shares"] == 200 and star_rules["lot_step"] == 1
    assert round_shares_to_lot(199, star_rules) == 0
    assert round_shares_to_lot(237, star_rules) == 237

    bse_rules = get_market_microstructure_rules("CN", "equity", "830799.BJ")
    assert bse_rules["min_order_shares"] == 100 and bse_rules["lot_step"] == 1
    assert round_shares_to_lot(99, bse_rules) == 0
    assert round_shares_to_lot(145, bse_rules) == 145

    main_rules = get_market_microstructure_rules("CN", "equity", "600519.SH")
    assert round_shares_to_lot(237, main_rules) == 200

    # R-10：A 股 ETF 最小报价单位 0.001 元；美股 <$1 最小报价单位 0.0001 美元
    assert round_to_tick(4.1264, "CN", "etf") == 4.126
    assert round_to_tick(0.45678, "US", "equity") == 0.4568
    assert round_to_tick(125.678, "US", "equity") == 125.68


# ---------------------------------------------------------------------------
# P1/P2/P3 计算类回归测试（C-01 ~ C-14，全部使用真实录制数据）
# ---------------------------------------------------------------------------


def test_c01_ema200_is_warmed_up_and_never_substituted(replay_recorded) -> None:
    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    bundle = load_bundle(req, now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
    report = analyze_bundle(bundle, req, include_replay=False)
    trend = report["mathematical_indicators"]["trend"]
    assert trend["ema200"] is not None and trend["sma250"] is not None
    formula = next(s["formula_verified"] for s in report["strategy_signals"] if s["strategy_id"] == "supertrend_ema_trend")
    assert f"EMA200({trend['ema200']:.2f})" in formula

    short = copy.deepcopy(bundle)
    short["target"]["bars"] = short["target"]["bars"][-150:]
    short["benchmark"]["bars"] = short["benchmark"]["bars"][-150:]
    rep_short = analyze_bundle(short, req, include_replay=False)
    assert rep_short["mathematical_indicators"]["trend"]["ema200"] is None
    formula_short = next(s["formula_verified"] for s in rep_short["strategy_signals"] if s["strategy_id"] == "supertrend_ema_trend")
    assert "EMA200(N/A" in formula_short
    assert rep_short["market_regime"]["evidence"]["close_vs_ema200_pct"] is None


def test_c02_c03_conformal_bands_are_calibrated_and_q50_has_no_drift() -> None:
    bars = load_recorded("sina_daily/sh600519.json")
    meta = {"market": "CN", "asset_type": "equity", "symbol": "600519.SH"}
    fc = compute_conformal_forecast(bars, meta=meta, horizon_bars=15)
    audit = fc["calibration_audit"]
    assert audit["calibrated"] is True and audit["evaluation_samples"] >= 60
    assert abs(audit["nominal_50pct_empirical_coverage"] - 0.50) <= 0.10
    assert abs(audit["nominal_80pct_empirical_coverage"] - 0.80) <= 0.10
    assert fc["promotion_status"] == "PROMOTED"
    assert len(audit["per_horizon_coverage"]) == 15
    assert audit["q50_mae_pct"] == audit["random_walk_mae_pct"]

    # 展示区间与审计区间使用完全相同的 _build_bands_from_multipliers 构造
    last_c = bars[-1]["close"]
    sigma = fc["volatility_scaling"]["sigma_1d"]
    m50, m80 = audit["q50_residual_multiplier"], audit["q80_residual_multiplier"]
    assert fc["optimistic_q75"][-1] == pytest.approx(last_c * (1 + m50 * sigma * math.sqrt(15)), abs=0.05)
    assert fc["q10"][-1] == pytest.approx(last_c * (1 - m80 * sigma * math.sqrt(15)), abs=0.05)
    # C-03：中位数恒为最新收盘价，不受规则分漂移影响
    assert set(fc["base_median_q50"]) == {round(last_c, 2)}
    assert fc["q50_method"] == "MARTINGALE_LAST_CLOSE"


def test_c04_c05_c10_backtest_replays_live_plan_with_bootstrap_ci(replay_recorded) -> None:
    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    bundle = load_bundle(req, now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
    bt = run_plan_backtest(bundle, req, horizon_bars=15, step=5)
    assert bt["method"] == "LIVE_PLAN_REPLAY_V6"
    assert "meta_weights_learned" not in bt and "primary_strategy_oos" not in bt
    assert sum(bt["actions_seen"].values()) == bt["decision_window"]["decisions"]
    trades = bt["sample_trades"]
    assert trades, "真实茅台历史样本上应产生可核对的计划成交"
    # C-10：样本数 >= 2 时提供胜率 Wilson 区间与均值收益 Bootstrap 95% 区间
    assert bt["all_long_signals"]["win_rate_95ci_pct"] is not None
    assert bt["all_long_signals"]["mean_return_95ci_pct"] is not None
    for key in ("all_long_signals", "long_confirmed", "watch_long_setup"):
        row = bt[key]
        if row["trades"] >= 2:
            lo, hi = row["win_rate_95ci_pct"]
            assert lo <= row["win_rate_pct"] <= hi and hi - lo > 0, row
    for tr in trades:
        t = next(i for i, b in enumerate(bundle["target"]["bars"]) if b["day"] == tr["signal_day"])
        pit_bars = rescale_bars_as_of(bundle["target"]["bars"][: t + 2], t)
        assert pit_bars[t]["close"] == bundle["target"]["bars"][t]["raw_close"]
        sub = {
            **bundle,
            "target": {**bundle["target"], "bars": pit_bars[: t + 1]},
            "benchmark": {**bundle["benchmark"], "bars": [b for b in bundle["benchmark"]["bars"] if b["day"] <= tr["signal_day"]]},
            "extra_context": {"vix": None, "earnings_date": None, "is_st": False, "is_ipo_no_limit": False},
        }
        plan = analyze_bundle(sub, {**req, "account_equity": None}, include_replay=False)["triple_barrier_plan"]
        assert plan["stop_loss_barrier"] == tr["plan_stop_loss"]
        assert plan["take_profit_1"] == tr["plan_take_profit_1"]
        assert tr["plan_entry_zone"][0] <= tr["entry_price"] <= tr["plan_entry_zone"][1]
        assert tr["entry_price"] == round(float(pit_bars[t + 1]["open"]), 4)
    for prev, nxt in zip(trades, trades[1:]):
        assert nxt["entry_idx"] > prev["exit_idx"]
    if bt["all_long_signals"]["trades"] < 30:
        assert bt["sample_size_warning"]


def test_c06_v5_pipeline_is_days_invariant(replay_recorded, tmp_path: Path) -> None:
    keys = (
        "meta",
        "market_benchmark",
        "mathematical_indicators",
        "algebraic_patterns_detected",
        "smc_structures",
        "volume_profile",
        "market_regime",
        "strategy_signals",
        "multi_timeframe_score",
        "meta_labeling_decision",
        "triple_barrier_plan",
        "rule_scenarios",
        "future_projection_15d",
        "debate_and_veto",
        "replay_snapshots",
    )
    sigs = {}
    for days in (60, 120, 300, 500):
        rep = run_pipeline(
            {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": days, "structure_bars": 120, "horizon_bars": 15},
            output_dir=tmp_path / str(days),
        )
        sigs[days] = {k: rep[k] for k in keys}
        sigs[days]["report_hash"] = rep["reproducibility"]["report_hash"]
        assert len(rep["series_export"]["bars"]) == days
    assert sigs[60] == sigs[120] == sigs[300] == sigs[500]


def test_c06_fetch_length_does_not_depend_on_days(monkeypatch: pytest.MonkeyPatch, replay_recorded) -> None:
    seen: list[int] = []
    orig_sina = data_gateway.fetch_sina_daily_bars

    def tracked_sina(code: str, datalen: int = 450) -> list[dict]:
        seen.append(datalen)
        return orig_sina(code, datalen)

    monkeypatch.setattr(data_gateway, "fetch_sina_daily_bars", tracked_sina)
    for days in (60, 500):
        load_bundle(
            {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": days},
            now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC),
        )
    assert set(seen) == {data_gateway.FETCH_BARS}


def test_c14_momentum_10d_and_flat_rsi_initial_value() -> None:
    bars = load_recorded("sina_daily/sh600519.json")
    ind = compute_all_indicators(bars)
    atr_last = ind["series"]["atr14"][-1]
    expected_mom10 = round((bars[-1]["close"] - bars[-11]["close"]) / (atr_last * math.sqrt(10)), 4)
    assert ind["snapshot"]["qlib_factors"]["atr_norm_momentum_10d"] == expected_mom10

    # 平盘序列首根 RSI 应为 50.0（中性），而非 100.0
    flat_rsi = rsi([bars[0]["close"]] * 20, period=14)
    assert flat_rsi[14] == 50.0 and flat_rsi[-1] == 50.0


# ---------------------------------------------------------------------------
# P1/P2/P3 渲染与数据源回归测试（G-01 ~ G-07、R-07、R-08、R-12）
# ---------------------------------------------------------------------------


def _count_color(path: Path, rgb: tuple[int, int, int]) -> int:
    import numpy as np

    arr = np.asarray(Image.open(path).convert("RGB"))
    return int((arr == np.array(rgb, dtype=arr.dtype)).all(axis=-1).sum())


def test_g01_and_r12_png_follows_market_and_cli_color_scheme(replay_recorded, tmp_path: Path) -> None:
    red, green = render_structure_chart.UP, render_structure_chart.DOWN
    bars = load_recorded("yahoo_daily/SPY.json")[-450:]
    cn = render_png(bars, None, "t", tmp_path / "cn.png", show=120, color_scheme="cn_red_up")
    us = render_png(bars, None, "t", tmp_path / "us.png", show=120, color_scheme="us_green_up")
    assert cn["color_scheme"] == "cn_red_up" and us["color_scheme"] == "us_green_up"
    cn_red, cn_green = _count_color(tmp_path / "cn.png", red), _count_color(tmp_path / "cn.png", green)
    us_red, us_green = _count_color(tmp_path / "us.png", red), _count_color(tmp_path / "us.png", green)
    assert cn_red > 0 and cn_green > 0
    assert abs(cn_red - us_green) <= cn_red * 0.10
    assert abs(cn_green - us_red) <= cn_green * 0.10

    report = run_pipeline(
        {"symbol": "NVDA.US", "market": "US", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path / "us_run",
    )
    assert report["meta"]["color_scheme"] == "us_green_up"
    assert report["legacy_summary"]["color_scheme"] == "us_green_up"

    # R-12：显式指定 color_scheme 覆盖默认配色
    rep_override = run_pipeline(
        {"symbol": "00700.HK", "market": "HK", "asset_type": "equity", "color_scheme": "us_green_up", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path / "hk_override",
    )
    assert rep_override["meta"]["color_scheme"] == "us_green_up"


def test_g02_structure_is_computed_once_outside_the_renderer(monkeypatch: pytest.MonkeyPatch, replay_recorded, tmp_path: Path) -> None:
    imports = {
        n.module
        for n in ast.walk(ast.parse((SCRIPTS_DIR / "analysis.py").read_text(encoding="utf-8")))
        if isinstance(n, ast.ImportFrom)
    }
    assert "render_structure_chart" not in imports and "structure" in imports

    def must_not_recompute(*_args, **_kwargs):
        raise AssertionError("PNG 渲染器不应重算结构层")

    monkeypatch.setattr(render_structure_chart, "compute_structure_layer", must_not_recompute)
    report = run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path,
    )
    assert report["rule_scenarios"] == report["legacy_summary"]["projections"]
    assert report["mathematical_indicators"]["structure_levels"]["key_levels"] == report["legacy_summary"]["key_levels"]


def test_g02_v41_render_still_matches_shared_layer() -> None:
    bars = load_recorded("sina_daily/sh000300.json")[-450:]
    layer = compute_structure_layer(bars, None, 120)
    direct = render_png(bars, None, "t", Path("/dev/null"), show=120)
    shared = render_png(bars, None, "t", Path("/dev/null"), show=120, structure=layer)
    assert direct["projections"] == shared["projections"] == layer["projections"]
    assert "_upper" in layer["wedge"]


def test_g03_to_g07_rendering_invariants_and_low_price_etf(replay_recorded, tmp_path: Path) -> None:
    # G-05：低价 ETF (510300.SH，价格约 4 元) 推演标签保留小数，不退化为整数
    assert fmt_price(4.126) == "4.126"
    assert fmt_price(24.55) == "24.55"
    assert fmt_price(1312.97) == "1313"

    rep_60 = run_pipeline(
        {"symbol": "510300.SH", "market": "CN", "asset_type": "etf", "display_bars": 60, "horizon_bars": 15},
        output_dir=tmp_path / "etf60",
    )
    # G-05 & R-10：ETF 3 位小数全链路对齐（series_export 末根收盘 == q50[0]，且风控文案保留 3 位小数）
    bull_labels = [txt for _, txt in rep_60["rule_scenarios"]["bull"]["step_labels"]]
    assert all("." in txt for txt in bull_labels)
    assert rep_60["series_export"]["bars"][-1]["close"] == rep_60["future_projection_15d"]["base_median_q50"][0]
    sl_val = rep_60["triple_barrier_plan"]["stop_loss_barrier"]
    assert f"{sl_val:.3f}" in rep_60["triple_barrier_plan"]["left_side_constraints"]["exit_rule"]

    # CLI 参数边界拦截：--days 超出 60~500 范围必须报错（参数错误为 ValueError，CLI 输出 [INVALID_ARGUMENT]）
    with pytest.raises(ValueError, match="--days must be between 60 and 500"):
        run_pipeline({"symbol": "510300.SH", "market": "CN", "asset_type": "etf", "display_bars": 20}, output_dir=tmp_path / "bad_days")

    # G-04 & C-12：--days 60 下每个历史回放快照自带截至 as_of 的历史窗口且含 rule_score，无未来 K 线泄漏
    for snap in rep_60["replay_snapshots"]:
        assert "rule_score" in snap and 0.0 <= snap["rule_score"] <= 100.0
        assert len(snap["bars_window"]) == 60
        assert all(b["day"] <= snap["as_of"] for b in snap["bars_window"])

    # G-06：PNG 摘要记录数据质量状态与风控官否决状态
    assert rep_60["legacy_summary"]["quality_status"] == "OK"
    assert isinstance(rep_60["legacy_summary"]["risk_officer_veto"], bool)

    # G-03 & G-07：HTML 脚本使用 rail.start.idx + winOff 计算轨道真实起点，且订单块颜色跟随 isCnColor
    html_text = Path(rep_60["rendered_paths"]["dashboard_html"]).read_text(encoding="utf-8")
    assert "rail.start.idx + winOff" in html_text
    assert "const bullObFill = isCnColor ?" in html_text


def test_r07_point_in_time_adjustment_from_real_corporate_actions(replay_recorded) -> None:
    # 使用真实录制的比亚迪 (002594.SZ) 行情与 Yahoo 除权除息事件（含 2025-07-29 10送转20 及历年分红）
    now = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    req = {"symbol": "002594.SZ", "market": "CN", "asset_type": "equity"}
    bundle = load_bundle(req, now=now)
    status = bundle["provenance"]["adjustment_status"]
    assert status["applied"] == "total_return" and status["events_used"] >= 4 and "002594.SZ" in status["source"]

    bars = bundle["target"]["bars"]
    bar_pre = next(b for b in bars if b["day"] == "2025-07-28")
    bar_ex = next(b for b in bars if b["day"] == "2025-07-29")
    assert bar_pre["raw_close"] == 337.0
    # 前复权后 2025-07-28 与 2025-07-29 价格处于同一 100 元量级，消除了 337 -> 111 的未复权断崖缺口
    assert abs(bar_pre["close"] - bar_ex["open"]) < 5.0
    assert bars[-1]["close"] == bars[-1]["raw_close"]

    split_only = load_bundle({**req, "adjustment": "split"}, now=now)
    assert split_only["provenance"]["adjustment_status"]["applied"] == "split"
    bar_pre_split = next(b for b in split_only["target"]["bars"] if b["day"] == "2025-07-28")
    assert bar_pre_split["close"] == pytest.approx(337.0 / 3.0, abs=1e-3)


def test_r07_missing_events_are_disclosed_not_faked(monkeypatch: pytest.MonkeyPatch, replay_recorded) -> None:
    def events_blocked(*_args, **_kwargs):
        raise OSError("events endpoint blocked")

    monkeypatch.setattr(data_gateway, "fetch_yahoo_corporate_actions", events_blocked)
    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    bundle = load_bundle(req, now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
    status = bundle["provenance"]["adjustment_status"]
    assert status["requested"] == "total_return" and status["applied"] == "raw" and status["error"]
    report = analyze_bundle(bundle, req, include_replay=False)
    assert report["meta"]["adjustment"] == "raw"
    assert any("复权未生效" in n for n in report["quality"]["notes"])


def test_r07_yahoo_prices_skip_split_events() -> None:
    raw = load_recorded("yahoo_daily/NVDA.json")
    events = load_recorded("yahoo_events/NVDA.json")
    actions = data_gateway.build_corporate_actions(raw, events, include_splits=False)
    assert actions
    assert all(a["split_ratio"] == 1.0 and a["dividend_factor"] < 1.0 for a in actions)


def test_r08_hstech_uses_real_index_not_etf_proxy(replay_recorded) -> None:
    bundle = load_bundle(
        {"symbol": "HSTECH.HK", "market": "HK", "asset_type": "index"},
        now=datetime(2026, 10, 16, 9, 0, tzinfo=UTC),
    )
    assert bundle["provenance"]["source"] == "TENCENT_INDEX_DAILY(hkHSTECH)"
    assert classify_data_source(bundle["provenance"]["source"]) == "LIVE"
    assert bundle["benchmark"]["source"] == "YAHOO_DAILY(^HSI)"
    assert bundle["target"]["bars"][-1]["close"] > 3000.0  # 恒生科技指数真实点位量级（非 3033.HK ETF 的几港元）


def test_p2_schema_file_drives_report_validation(monkeypatch: pytest.MonkeyPatch, replay_recorded) -> None:
    report = analyze_bundle(
        load_bundle({"symbol": "600519.SH", "market": "CN", "asset_type": "equity"}),
        {"symbol": "600519.SH", "display_bars": 120, "horizon_bars": 15},
        include_replay=False,
    )
    validate_report(report)
    schema = contracts.load_report_schema()
    for key in schema["required"]:
        broken = copy.deepcopy(report)
        broken.pop(key)
        with pytest.raises(ValueError, match=f"Missing required report key: {key}"):
            validate_report(broken)
    bad_meta = copy.deepcopy(report)
    bad_meta["meta"]["color_scheme"] = "rainbow"
    with pytest.raises(ValueError, match="Invalid meta.color_scheme"):
        validate_report(bad_meta)
    stricter = copy.deepcopy(schema)
    stricter["required"] = [*schema["required"], "brand_new_section"]
    monkeypatch.setattr(contracts, "load_report_schema", lambda: stricter)
    with pytest.raises(ValueError, match="brand_new_section"):
        validate_report(report)
