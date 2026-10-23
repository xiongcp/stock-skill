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
from conftest import RECORDED_DIR, load_recorded, load_v6

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
from data_gateway import FETCH_BARS, load_bundle  # noqa: E402
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
    price_decimals,
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
    from contract_v6 import artifact_files, build_result

    bundle = load_bundle(
        {"symbol": "000001.SH", "market": "CN", "asset_type": "index", "display_bars": 120, "horizon_bars": 15}
    )
    report = analyze_bundle(bundle, {"symbol": "000001.SH", "display_bars": 120, "horizon_bars": 15}, include_replay=False)
    result = build_result(report, artifact_files("000001.SH", has_backtest=False))
    validate_report(result)

    bad_nan = json.loads(json.dumps(result))
    bad_nan["verdict"]["rule_score"] = float("nan")
    with pytest.raises(ValueError, match="Non-finite float"):
        validate_report(bad_nan)

    bad_q = json.loads(json.dumps(result))
    bad_q["volatility_range"]["q25"][0] = bad_q["volatility_range"]["q75"][0] + 10.0
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

    # 截至除权日构建动作时，未来事件只能用于还原 Yahoo 已折算的历史股息，
    # 不能作为未来 corporate action 输出或计入 events_used。
    actions_as_of_ex = data_gateway.build_corporate_actions(
        raw_bars[: idx_ex + 1], raw_events, include_splits=True
    )
    assert all(a["ex_date"] <= "2025-07-29" for a in actions_as_of_ex)
    assert len(actions_as_of_ex) <= len(actions)

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
    # 验证常数序列上量价秩相关返回 0.0（并列秩取均值）
    from indicators import price_volume_rank_corr

    assert price_volume_rank_corr([10.0] * 25, [100.0 + i for i in range(25)], window=20)[-1] == 0.0


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
    # 验证 A 股 T+1 入场首日 (idx=1, O=1236.0, H=1244.01, C=1243.88) 涨幅达标时可触发次日保本上移：
    # 设 sl=1228.0 (unit_risk=8.0, +0.9R=1243.2, +0.3R=1238.4)，idx=1 收盘后止损上移至 1236.64，idx=2 最低 1230.88 触发保本止盈
    ratchet_res = label_triple_barrier_event(
        bars, entry_idx=1, tp_price=1300.0, sl_price=1228.0, horizon_bars=3, market="CN", asset_type="equity"
    )
    assert ratchet_res["outcome"] == "TRAILING_PROFIT_STOP"
    assert ratchet_res["exit_idx"] == 2 and ratchet_res["exit_price"] > 1236.0
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

    # 验证到期日 (idx=3) 遭遇一字跌停封死时不能在 idx=3 卖出，必须顺延至开板日 (idx=4) 成交
    locked_bars = copy.deepcopy(bars)
    prev_c = float(locked_bars[2]["close"])
    limit_dn = round(prev_c * 0.90, 2)
    locked_bars[3] = {**locked_bars[3], "open": limit_dn, "high": limit_dn, "low": limit_dn, "close": limit_dn}
    res_lock = label_triple_barrier_event(
        locked_bars, entry_idx=1, tp_price=2000.0, sl_price=800.0, horizon_bars=3, market="CN", asset_type="equity", symbol="600519.SH"
    )
    assert res_lock["outcome"] == "TIME_BARRIER"
    assert res_lock["exit_idx"] == 4


@pytest.mark.slow
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
    assert report["schema_version"] == "6.0.0"
    assert report["quality"]["status"] == "OK"
    assert report["quality"]["data_source_class"] == "LIVE"
    assert report["meta"]["color_scheme"] == expected_scheme
    assert report["meta"]["as_of"] == "2026-10-15"
    assert report["mathematical_indicators"]["trend"]["ema200"] is not None
    assert 0.0 <= report["multi_timeframe_score"]["rule_score"] <= 100.0
    fc = report["future_projection_15d"]
    assert fc["calibration_audit"]["calibrated"] is True
    assert fc["calibration_audit"]["q50_mae_pct"] == fc["calibration_audit"]["random_walk_mae_pct"]
    sizing = report["triple_barrier_plan"]["position_sizing"]
    long_allowed = not report["debate_and_veto"]["risk_officer_veto"] and report["meta_labeling_decision"]["action"] in (
        "LONG_CONFIRMED",
        "WATCH_LONG_SETUP",
    )
    if asset_type == "index":
        assert sizing["recommended_shares"] is None
    elif long_allowed:
        assert sizing["recommended_shares"] is not None
    else:
        # 风控否决或评级不是做多时不给股数（股数本身就是买入建议），并写明原因
        assert sizing["recommended_shares"] is None and sizing["estimated_notional"] is None
        assert "不给出建议股数" in sizing["reason"]

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
    bars = load_recorded("sina_daily/sh600519.json")[-FETCH_BARS:]
    meta = {"market": "CN", "asset_type": "equity", "symbol": "600519.SH"}
    fc = compute_conformal_forecast(bars, meta=meta, horizon_bars=15)
    audit = fc["calibration_audit"]
    # 审计期覆盖全部 760 根历史：N_eval = n - 2H - 99 = 631，约 42 个独立样本，覆盖率达标即晋级
    assert audit["calibrated"] is True and audit["evaluation_samples"] == FETCH_BARS - 2 * 15 - 99 == 631
    assert abs(audit["nominal_50pct_empirical_coverage"] - 0.50) <= 0.10
    assert abs(audit["nominal_80pct_empirical_coverage"] - 0.80) <= 0.10
    assert audit["effective_independent_samples"] == 631 // 15 >= audit["min_effective_independent_samples"]
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


def test_c11_promotion_requires_effective_independent_samples_floor() -> None:
    """已达标需有效独立样本 >= 20：同一只 600519、H=15，取最近 429 根（300 个样本，20 个）达标，428 根（299 个，19 个）覆盖率合格也不晋级。"""
    bars = load_recorded("sina_daily/sh600519.json")
    meta = {"market": "CN", "asset_type": "equity", "symbol": "600519.SH"}
    for n, eff, status in ((429, 20, "PROMOTED"), (428, 19, "RESEARCH_CALIBRATION_WARMUP")):
        fc = compute_conformal_forecast(bars[-n:], meta=meta, horizon_bars=15)
        audit = fc["calibration_audit"]
        assert audit["min_effective_independent_samples"] == 20
        assert audit["evaluation_samples"] == n - 2 * 15 - 99
        assert audit["effective_independent_samples"] == eff == audit["evaluation_samples"] // 15
        assert abs(audit["nominal_50pct_empirical_coverage"] - 0.50) <= 0.10
        assert abs(audit["nominal_80pct_empirical_coverage"] - 0.80) <= 0.10
        assert not audit["uncalibrated_overlays"]
        assert fc["promotion_status"] == status


@pytest.mark.parametrize(
    "rel,meta",
    [
        ("sina_daily/sh600519.json", {"market": "CN", "asset_type": "equity", "symbol": "600519.SH"}),
        ("yahoo_daily/0700.HK.json", {"market": "HK", "asset_type": "equity", "symbol": "00700.HK"}),
        ("yahoo_daily/NVDA.json", {"market": "US", "asset_type": "equity", "symbol": "NVDA.US"}),
    ],
)
def test_c12_audit_period_decoupled_from_display_calibration_window(rel: str, meta: dict) -> None:
    """审计期随历史变长而扩大，展示区间只由最近 CALIBRATION_WINDOW 个截面决定，宽度与审计期长短无关。"""
    bars = load_recorded(rel)
    long_fc = compute_conformal_forecast(bars[-FETCH_BARS:], meta=meta, horizon_bars=15)
    # 展示区间只依赖最近 1 + H + 160 个截面及其前 60 根 σ 回看
    short_fc = compute_conformal_forecast(bars[-(1 + 15 + 160 + 60 + 1) :], meta=meta, horizon_bars=15)
    for key in ("q10", "q25", "base_median_q50", "optimistic_q75", "q90", "conformal_50pct_band", "conformal_80pct_band"):
        assert long_fc[key] == short_fc[key]
    la, sa = long_fc["calibration_audit"], short_fc["calibration_audit"]
    assert la["q80_residual_multiplier"] == sa["q80_residual_multiplier"]
    assert la["evaluation_samples"] == FETCH_BARS - 2 * 15 - 99
    assert sa["evaluation_samples"] == 237 - 2 * 15 - 99
    assert la["effective_independent_samples"] >= 20 > sa["effective_independent_samples"]


@pytest.mark.slow
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


@pytest.mark.slow
def test_c06_pipeline_is_days_invariant(replay_recorded, tmp_path: Path) -> None:
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
    v6_sigs = {}
    for days in (60, 120, 300, 500):
        rep = run_pipeline(
            {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": days, "structure_bars": 120, "horizon_bars": 15},
            output_dir=tmp_path / str(days),
        )
        sigs[days] = {k: rep[k] for k in keys}
        sigs[days]["report_hash"] = rep["reproducibility"]["report_hash"]
        assert len(rep["series_export"]["bars"]) == days
        result, _ = load_v6(rep)
        result["meta"]["request"].pop("display_bars", None)
        result["quality"].pop("display_bars", None)
        v6_sigs[days] = result
    assert sigs[60] == sigs[120] == sigs[300] == sigs[500]
    assert v6_sigs[60] == v6_sigs[120] == v6_sigs[300] == v6_sigs[500]


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


def test_c06_historical_asof_uses_deeper_fetch_window(monkeypatch: pytest.MonkeyPatch, replay_recorded) -> None:
    """历史截面必须取到 as_of 之前足够的预热K线，不能先截最新窗口再过滤。"""
    seen: list[int] = []
    orig_sina = data_gateway.fetch_sina_daily_bars

    def tracked_sina(code: str, datalen: int = 450) -> list[dict]:
        seen.append(datalen)
        return orig_sina(code, datalen)

    monkeypatch.setattr(data_gateway, "fetch_sina_daily_bars", tracked_sina)
    with pytest.raises(RuntimeError, match="历史长度不足"):
        load_bundle(
            {
                "symbol": "600519.SH",
                "market": "CN",
                "asset_type": "equity",
                "display_bars": 120,
                "as_of": "2021-12-31",
            },
            now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC),
        )
    assert seen == [data_gateway.HISTORICAL_FETCH_BARS]


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


def _closed_conclusions(layer: dict) -> dict:
    """结构层里只由已收盘K线决定的结论：ABC 情景与 C 浪进度、轨道、阶梯、三路径点位（不含盘中标注与折线起点）。"""
    scenario = layer["scenario"]
    if scenario is not None:
        scenario = {k: v for k, v in scenario.items() if k != "intraday_note"}
        scenario["levels"] = [{k: v for k, v in lvl.items() if k != "intraday"} for lvl in scenario["levels"]]
    wedge = {k: layer["wedge"][k] for k in ("pattern", "fitted", "status", "status_short", "upper_today", "lower_today", "expired", "fit_check")}
    counts = [
        {
            **{k: v for k, v in wc.items() if k != "intraday_note"},
            "levels": [{k: v for k, v in lvl.items() if k != "intraday"} for lvl in wc["levels"]],
        }
        for wc in layer["wave_counts"]
    ]
    proj = layer["projections"]
    paths = {
        "breakout": proj["breakout_level"],
        "breakdown": proj["breakdown_level"],
        "contested": proj["contested_levels"],
        "eta": {name: proj[name]["eta_bars"] for name in ("bull", "bear")},
        # 折点时间只由收盘价与已收盘K线的波动决定，盘中不改变
        "offsets": {name: [o for o, _ in proj[name]["waypoints_offset"]] for name in ("bull", "neutral", "bear")},
        "bull": [proj["bull"][k] for k in ("retest_hold", "target_price", "target_source")],
        "neutral": [proj["neutral"]["range"], proj["neutral"]["target_price"]],
        "bear": [proj["bear"][k] for k in ("rebound_cap", "target_price", "target_source")],
        # 折线第 0 点是当前位置（盘中价），其余点位必须与收盘态一致
        "waypoints": {name: [p for _, p in proj[name]["waypoints_offset"][1:]] for name in ("bull", "neutral", "bear")},
    }
    return {
        "scenario": scenario,
        "wave_counts": counts,
        "wedge": wedge,
        "fibs": layer["fibs"],
        "background": layer["background"],
        "gaps": layer["gaps"],
        "ladder": layer["ladder"],
        "paths": paths,
    }


@pytest.mark.parametrize("symbol", ["sh000300", "sh600519", "sz399006"])
def test_g08_live_bar_does_not_change_closed_structure(symbol: str) -> None:
    # 铁律 0：盘中K线（录制数据中下一根真实K线标记为未收盘）只做标注，不改变 ABC 情景、C 浪进度与三路径点位
    bars = load_recorded(f"sina_daily/{symbol}.json")
    checked = with_scenario = 0
    for k in range(450, len(bars), 23):
        cut = [dict(b) for b in bars[k - 450 : k]]
        live = {**bars[k], "prev": cut[-1]["close"], "stamp": bars[k]["day"] + " 10:30:00", "complete": False}
        closed = compute_structure_layer(cut, None, 120)
        intra = compute_structure_layer(cut, live, 120)
        assert intra["active_intra"] is not None and intra["completed"] == closed["completed"]
        assert _closed_conclusions(intra) == _closed_conclusions(closed), f"{symbol} @ {cut[-1]['day']}"
        checked += 1
        with_scenario += closed["scenario"] is not None
    assert checked >= 20 and with_scenario > 0


def test_g03_to_g07_rendering_invariants_and_low_price_etf(replay_recorded, tmp_path: Path) -> None:
    # G-05：低价 ETF (510300.SH，价格约 4 元) 推演标签保留小数，不退化为整数。
    # 第二轮复查 5.3-2：原先 >=100 取整（"1313"）是为了短标签，但与 *_report.md 的价位表精度不一致
    # （BRK.B.US 图上"突破 534"而报告为 533.55），现改为调用方按 market_rules.price_decimals 传入小数位，
    # 图、step_labels 与报告同一精度；缺省 2 位为 A 股股票口径（v4.1 CLI 无市场信息时使用）。
    assert fmt_price(1312.97) == "1312.97"
    assert fmt_price(5.123) == "5.12"
    assert fmt_price(4.126, price_decimals(4.126, "CN", "etf")) == "4.126"
    assert fmt_price(0.5, price_decimals(0.5, "US", "equity")) == "0.5000"

    rep_60 = run_pipeline(
        {"symbol": "510300.SH", "market": "CN", "asset_type": "etf", "display_bars": 60, "horizon_bars": 15},
        output_dir=tmp_path / "etf60",
    )
    # G-05 & R-10：ETF 3 位小数全链路对齐（series_export 末根收盘 == q50[0]，且风控文案保留 3 位小数）
    bull_labels = [txt for _, txt in rep_60["rule_scenarios"]["bull"]["step_labels"]]
    assert all(len(txt.rsplit(".", 1)[-1]) == 3 for txt in bull_labels)  # ETF 0.001 价位：3 位小数
    r1 = rep_60["rule_scenarios"]["breakout_level"]["price"]
    assert bull_labels[0] == f"突破 {r1:.3f}" and f"{r1:.3f}" in rep_60["rule_scenarios"]["bull"]["trigger"]
    report_md = Path(rep_60["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    assert f"{rep_60['rule_scenarios']['bull']['target_price']:.3f}" in report_md
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


def test_replay_snapshots_use_point_in_time_benchmark(replay_recorded, tmp_path: Path) -> None:
    report = run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path,
    )
    snapshot = next(s for s in report["replay_snapshots"] if s["as_of"] == "2026-06-16")
    # 该截面当时沪深300为宽幅震荡 75.5 分、相对强弱 0.9421；不能用最新截面的 16.0 分/1.1164。
    assert snapshot["benchmark_regime_score"] == pytest.approx(75.5, abs=0.1)
    assert snapshot["benchmark_relative_strength"] == pytest.approx(0.9421, abs=0.001)


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
    from contract_v6 import artifact_files, build_result

    report = analyze_bundle(
        load_bundle({"symbol": "600519.SH", "market": "CN", "asset_type": "equity"}),
        {"symbol": "600519.SH", "display_bars": 120, "horizon_bars": 15},
        include_replay=False,
    )
    result = build_result(report, artifact_files("600519.SH", has_backtest=False))
    validate_report(result)
    schema = contracts.load_report_schema()
    for key in schema["required"]:
        broken = copy.deepcopy(result)
        broken.pop(key)
        with pytest.raises(ValueError, match=f"缺少必填字段 {key}"):
            validate_report(broken)
    bad_meta = copy.deepcopy(result)
    bad_meta["meta"]["color_scheme"] = "rainbow"
    with pytest.raises(ValueError, match="meta.color_scheme"):
        validate_report(bad_meta)
    stricter = copy.deepcopy(schema)
    stricter["required"] = [*schema["required"], "brand_new_section"]
    monkeypatch.setattr(contracts, "load_report_schema", lambda: stricter)
    with pytest.raises(ValueError, match="brand_new_section"):
        validate_report(result)


# ---------------------------------------------------------------------------
# 第三轮复查 O3：推演失真修正（真实录制行情）
# ---------------------------------------------------------------------------

O3_SERIES = (
    *(f"sina_daily/{p.stem}.json" for p in sorted((RECORDED_DIR / "sina_daily").glob("*.json"))),
    "yahoo_daily/0700.HK.json",
    "yahoo_daily/1810.HK.json",
    "yahoo_daily/HSI.json",
    "yahoo_daily/NVDA.json",
    "yahoo_daily/SPY.json",
)
CONVERGING = ("对称三角形", "上升楔形", "下降楔形", "上升三角形", "下降三角形", "收敛整理")


def _o3_layers(step: int = 20, horizon_bars: int = 15):
    for rel in O3_SERIES:
        bars = load_recorded(rel)
        for cut in range(450, len(bars), step):
            yield rel, bars, cut, compute_structure_layer(bars[: cut + 1], None, 120, horizon_bars=horizon_bars)


def _ols_r2_slope(values: list[float]) -> tuple[float, float]:
    xs = range(len(values))
    mx, my = (len(values) - 1) / 2, sum(values) / len(values)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, values))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in values)
    return (sxy * sxy / (sxx * syy) if sxx and syy else 0.0), sxy / sxx


@pytest.mark.slow
def test_o3_01_rail_fit_has_no_convergence_bias() -> None:
    # 旧算法取凸包最后一条边，1034 个截面中收敛类占 85.5%、对称三角形 42.5%，强趋势截面被判成对称三角形/反向形态 35%
    patterns: list[str] = []
    strong = misread = 0
    for _, _, _, layer in _o3_layers():
        wedge = layer["wedge"]
        name = wedge["pattern"].split("（")[0]
        patterns.append(name)
        assert {"upper", "lower", "flat_pct_per_bar"} <= set(wedge["fit_check"])
        if wedge["pattern"].startswith("形态不明确"):
            assert not (wedge["fit_check"]["upper"]["consistent"] and wedge["fit_check"]["lower"]["consistent"])
        r2, slope = _ols_r2_slope(causal_nadaraya_watson([b["close"] for b in layer["sview"]], bandwidth=3.0))
        if r2 >= 0.85:
            strong += 1
            wrong = ("对称三角形", "下降楔形", "下降三角形", "下降通道") if slope > 0 else ("对称三角形", "上升楔形", "上升三角形", "上升通道")
            misread += name in wrong
    n = len(patterns)
    assert n >= 400 and strong >= 30
    assert sum(p in CONVERGING for p in patterns) / n <= 0.65
    assert patterns.count("对称三角形") / n <= 0.20
    assert misread / strong <= 0.20, f"强趋势截面误判 {misread}/{strong}"


@pytest.mark.slow
@pytest.mark.parametrize("horizon_bars", [15, 5])
def test_o3_02_projection_timeline_within_horizon_and_triggers_off_noise(horizon_bars: int) -> None:
    signatures = set()
    checked = 0
    for _, _, _, layer in _o3_layers(step=40, horizon_bars=horizon_bars):
        proj = layer["projections"]
        ref = float(layer["last_c"])
        min_dist = proj["min_trigger_distance_atr"] * proj["atr_pct"] / 100
        assert proj["horizon_bars"] == horizon_bars
        for key in ("bull", "neutral", "bear"):
            offsets = [o for o, _ in proj[key]["waypoints_offset"]]
            assert offsets[0] == 0 and max(offsets) <= horizon_bars
            assert offsets == sorted(offsets) and len(set(offsets)) == len(offsets), (key, offsets)
        for key, level in (("bull", proj["breakout_level"]["price"]), ("bear", proj["breakdown_level"]["price"])):
            assert abs(level / ref - 1) >= min_dist - 0.0001, (key, level, ref)
            eta = proj[key]["eta_bars"]
            assert proj[key]["target_within_horizon"] == (eta["target"] <= horizon_bars)
        for c in proj["contested_levels"]:
            assert abs(c["price"] / ref - 1) < min_dist + 0.0001
        assert layer["future_slots"] >= horizon_bars + 6
        signatures.add(tuple(o for o, _ in proj["bull"]["waypoints_offset"] + proj["bear"]["waypoints_offset"]))
        checked += 1
    assert checked >= 100
    # 折点时间随距离与波动率变化，不再是固定模板（旧版只有 1 种）
    assert len(signatures) >= (20 if horizon_bars == 15 else 5)


@pytest.mark.slow
def test_o3_03_wave_counts_primary_plus_distinct_alternates() -> None:
    with_alt = 0
    for _, _, _, layer in _o3_layers(step=40):
        counts = layer["wave_counts"]
        primary = layer["scenario"]
        assert len(counts) <= 3
        if primary:
            assert counts[0]["role"] == "主计数" and counts[0]["a_start"] == primary["a_start"]
        for wc in counts:
            assert wc["count_invalidation"]["price"] == wc["b_end"]["price"]
            assert wc["a_range_atr"] > 0
        alts = [wc for wc in counts if wc["role"] == "备选计数"]
        with_alt += bool(alts)
        for i, a in enumerate(counts):
            for b in counts[i + 1 :]:
                same_ends = a["direction"] == b["direction"] and a["a_end"]["idx"] == b["a_end"]["idx"] and a["b_end"]["idx"] == b["b_end"]["idx"]
                assert not same_ends, "同一 A/B 终点只是起点不同的计数应去重"
    assert with_alt >= 50


def test_o3_04_defense_and_short_plans_by_market(replay_recorded) -> None:
    cases = (
        ("600519.SH", "CN", "equity", False),
        ("00700.HK", "HK", "equity", True),
        ("NVDA.US", "US", "equity", True),
        ("000001.SH", "CN", "index", False),
    )
    for symbol, market, asset_type, shortable in cases:
        req = {"symbol": symbol, "market": market, "asset_type": asset_type, "display_bars": 120, "horizon_bars": 15}
        report = analyze_bundle(load_bundle(req), req, include_replay=False)
        plan = report["triple_barrier_plan"]
        decision = report["meta_labeling_decision"]
        assert len(decision["veto_codes"]) == len(decision["veto_reasons"])
        assert report["debate_and_veto"]["veto_codes"] == decision["veto_codes"]
        defense, short = plan["holder_defense_plan"], plan["short_plan"]
        s1 = report["rule_scenarios"]["breakdown_level"]["price"]
        if asset_type == "index":
            assert defense["available"] is False and short["available"] is False
            continue
        assert defense["available"] and defense["exit_level"] <= defense["reduce_level"]
        assert defense["reduce_level"] == pytest.approx(s1, abs=0.01)
        assert short["available"] is shortable
        if shortable:
            lo, hi = short["entry_zone"]
            assert short["stop_loss_barrier"] > hi >= lo > short["take_profit_1"] >= short["take_profit_2"] > 0
            assert short["time_barrier_bars"] == 15 and short["constraints"]
        else:
            assert "融券" in short["reason"]


@pytest.mark.slow
def test_o3_05_backtest_reports_rule_score_ic_and_action_forward_returns(replay_recorded) -> None:
    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    bundle = load_bundle(req, now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
    bt = run_plan_backtest(bundle, req, horizon_bars=15, step=10)
    decisions = bt["decision_window"]["decisions"]
    ic = bt["rule_score_ic"]
    assert ic["decisions"] == decisions and ic["effective_independent_samples"] == decisions * 10 // 15
    assert ic["spearman"] is None or -1.0 <= ic["spearman"] <= 1.0
    assert ic["informative"] == (ic["spearman"] is not None and ic["effective_independent_samples"] >= 20 and abs(ic["spearman"]) >= 0.05)
    assert sum(b["decisions"] for b in bt["rule_score_buckets"]) == decisions
    assert sum(r["decisions"] for r in bt["action_forward_returns"].values()) == sum(
        v for k, v in bt["actions_seen"].items() if k in bt["action_forward_returns"]
    )
    codes = {"BENCHMARK_MISSING", "BENCHMARK_WEAK", "RR_INSUFFICIENT", "BELOW_LOWER_RAIL", "BREAKOUT_LOW_VOLUME"}
    assert set(bt["veto_reason_counts"]) <= codes
    for prev, nxt in zip(bt["sample_trades"], bt["sample_trades"][1:]):
        assert nxt["entry_idx"] > prev["exit_idx"]


def test_o3_report_md_shows_wave_counts_defense_and_short_plans(replay_recorded, tmp_path: Path) -> None:
    rep = run_pipeline(
        {"symbol": "00700.HK", "market": "HK", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path,
    )
    md = Path(rep["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    plan = rep["triple_barrier_plan"]
    assert "持仓防守计划" in md and "破位做空计划" in md
    assert f"{plan['short_plan']['stop_loss_barrier']:.2f}" in md
    if rep["mathematical_indicators"]["structure_levels"]["wave_counts"]:
        assert "| 浪型计数 |" in md and "此计数失效" in md
    assert "只是时间量级，不是方向预测" in md
    # 风险预算折算不足一手时明确说明，不只显示"0 股"
    from render_report_md import render_report_markdown

    zero = copy.deepcopy(rep)
    zero["triple_barrier_plan"]["position_sizing"].update(
        {"tradable": True, "account_equity": 500000.0, "max_risk_pct": 1.0, "risk_budget_amount": 5000.0,
         "lot_size": 100, "min_order_shares": 100, "lot_step": 100, "recommended_shares": 0, "estimated_notional": 0.0}
    )
    zero_md = render_report_markdown(zero)
    assert "没有可执行的股数" in zero_md and "**0 股**" not in zero_md


# ---------------------------------------------------------------------------
# 阶段 2（O3-20 ~ O3-24）：多情景引擎、无前视历史条件频率、Brier 检验与条件扇形
# ---------------------------------------------------------------------------


def test_o3_20_multi_scenario_engine_diversity_active_trigger_and_impulse_rules(replay_recorded) -> None:
    from scenarios import PLAYBOOK_BY_ID, SCENARIO_PLAYBOOK, detect_impulse_wave
    from structure import compute_structure_layer

    assert len(SCENARIO_PLAYBOOK) == 8
    valid_ids = set(PLAYBOOK_BY_ID)

    signatures: set[tuple[str, ...]] = set()
    scenarios_seen: set[str] = set()
    impulse_found = False
    active_found = False

    for sym, market, asset_type in (
        ("600519.SH", "CN", "equity"),
        ("000300.SH", "CN", "index"),
        ("00700.HK", "HK", "equity"),
        ("NVDA.US", "US", "equity"),
    ):
        req = {"symbol": sym, "market": market, "asset_type": asset_type, "display_bars": 120, "horizon_bars": 15}
        bundle = load_bundle(req, now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
        bars = bundle["target"]["bars"]
        for cut in range(220, len(bars), 20):
            sub = bars[:cut]
            layer = compute_structure_layer(sub, None, 120, horizon_bars=15)
            proj = layer["projections"]
            scs = proj["scenarios"]
            assert 2 <= len(scs) <= 5
            sig = tuple(s["id"] for s in scs)
            assert set(sig) <= valid_ids
            signatures.add(sig)
            scenarios_seen.update(sig)
            for s in scs:
                assert s["precondition_met"] == (s["selection_role"] == "PRECONDITION_MET")
                assert s["precondition_met"] == (s["applicability_score"] >= 80.0)
                if not s["precondition_met"]:
                    assert proj["precondition_met_count"] < 2
                    assert s["status"] == "PENDING_TRIGGER"
                if s["precondition_met"] and s["trigger_distance_atr"] < 0.5:
                    assert s["status"] == "ACTIVE" and s["status_cn"] == "已在进行中"
                    assert s["trigger_price"] is None
                    active_found = True
                ref_c = float(layer["last_c"])
                if s["direction"] == "BULLISH":
                    assert s["target_price"] > ref_c, f"{sym}@{cut} {s['id']} 看多目标 {s['target_price']} <= 现价 {ref_c}"
                    if s["invalidation_price"] is not None:
                        assert s["invalidation_price"] < ref_c, f"{sym}@{cut} {s['id']} 看多失效位 {s['invalidation_price']} >= 现价 {ref_c}"
                elif s["direction"] == "BEARISH":
                    assert s["target_price"] < ref_c, f"{sym}@{cut} {s['id']} 看空目标 {s['target_price']} >= 现价 {ref_c}"
                    if s["invalidation_price"] is not None:
                        assert s["invalidation_price"] > ref_c, f"{sym}@{cut} {s['id']} 看空失效位 {s['invalidation_price']} <= 现价 {ref_c}"
                if s["id"] == "APEX_BREAKOUT":
                    assert not layer["wedge"].get("expired")
                    assert layer["wedge"].get("apex_bars_ahead") is not None and float(layer["wedge"]["apex_bars_ahead"]) >= 0.0
                if s["id"] == "BREAKDOWN_DECLINE" and s["status"] == "ACTIVE":
                    assert s["reference_trigger_price"] >= ref_c
                if s["id"] == "BREAKOUT_RETEST" and s["status"] == "ACTIVE":
                    assert s["reference_trigger_price"] <= ref_c
            imp = proj.get("impulse_wave")
            if imp is not None:
                impulse_found = True
                rules = imp["hard_rules"]
                assert rules["wave2_above_wave1_start"] is True
                assert rules["wave3_not_shortest"] is True
                assert rules["wave4_outside_wave1"] is True
                ref_c = float(layer["last_c"])
                if imp["direction"] == "up":
                    assert imp["wave3_1618_target"] > ref_c
                else:
                    assert imp["wave3_1618_target"] < ref_c

    # 不同标的和截面不能总是同一套三剧本，必须产生多种情景组合
    assert len(signatures) >= 10, f"情景组合过于单一: {signatures}"
    assert len(scenarios_seen) >= 6, f"覆盖的标准情景种类过少: {scenarios_seen}"
    assert impulse_found is True
    assert active_found is True

    # 验证推动 5 浪硬规则违反时自动拒绝（不返回违规推动浪）
    req_cn = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    b_cn = load_bundle(req_cn, now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC))["target"]["bars"][:300]
    layer_cn = compute_structure_layer(b_cn, None, 120, horizon_bars=15)
    pivs = copy.deepcopy(layer_cn["pivots"])
    if len(pivs) >= 6:
        # 构造浪 2 跌破浪 1 起点的违规拐点，验证 detect_impulse_wave 返回 None
        bad_pivs = pivs[-6:]
        bad_pivs[0]["kind"], bad_pivs[0]["price"] = "L", 1500.0
        bad_pivs[1]["kind"], bad_pivs[1]["price"] = "H", 1600.0
        bad_pivs[2]["kind"], bad_pivs[2]["price"] = "L", 1480.0  # 跌破浪1起点 1500
        bad_pivs[3]["kind"], bad_pivs[3]["price"] = "H", 1750.0
        bad_pivs[4]["kind"], bad_pivs[4]["price"] = "L", 1620.0
        bad_pivs[5]["kind"], bad_pivs[5]["price"] = "H", 1800.0
        assert detect_impulse_wave(layer_cn["sview"], bad_pivs, 0.015) is None


def test_o3_23_renderers_draw_scenarios(replay_recorded, tmp_path: Path) -> None:
    rep = run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path,
        run_backtest=False,
    )
    # 默认保形区间的中位数恒等于最新收盘价，不被情景路径篡改
    fp = rep["future_projection_15d"]
    last_close = rep["series_export"]["bars"][-1]["close"]
    assert all(abs(v - last_close) < 1e-6 for v in fp["base_median_q50"])
    # O3-23：HTML 与 Markdown 包含多情景推演图层与说明
    html_text = Path(rep["rendered_paths"]["dashboard_html"]).read_text(encoding="utf-8")
    md_text = Path(rep["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    png_path = Path(rep["rendered_paths"]["preview_png"])
    assert png_path.exists() and png_path.stat().st_size > 20_000
    assert "多情景推演" in html_text
    assert "多情景推演" in md_text
    for sc_item in rep["rule_scenarios"]["scenarios"]:
        assert sc_item["name"] in md_text


def test_hk_etp_auto_detection_and_stamp_duty_exemption() -> None:
    from market_rules import get_market_microstructure_rules, is_cn_t0_etf, is_daily_bar_complete, is_hk_etp_code
    from resolve_symbol import parse_search_response, to_candidate

    assert is_hk_etp_code("02800.HK") is True
    assert is_hk_etp_code("03033.HK") is True
    assert is_hk_etp_code("07788.HK") is True
    assert is_hk_etp_code("09067.HK") is True
    assert is_hk_etp_code("00700.HK") is False

    # 验证港股半日市（12:10 HKT 收市）与美股提前收市日（13:00 ET 收市）判定
    assert is_daily_bar_complete("2026-12-24", "HK", now=datetime(2026, 12, 24, 4, 15, tzinfo=UTC)) is True
    assert is_daily_bar_complete("2026-12-24", "HK", now=datetime(2026, 12, 24, 3, 50, tzinfo=UTC)) is False
    assert is_daily_bar_complete("2026-11-27", "US", now=datetime(2026, 11, 27, 18, 5, tzinfo=UTC)) is True
    assert is_daily_bar_complete("2026-11-27", "US", now=datetime(2026, 11, 27, 17, 30, tzinfo=UTC)) is False

    # 验证 A 股跨境/债券/黄金/货币 ETF 自动识别为 T+0 回转交易
    assert is_cn_t0_etf("513050.SH", "中概互联网ETF") is True
    assert is_cn_t0_etf("511880.SH", "银华日利ETF") is True
    assert is_cn_t0_etf("518880.SH", "黄金ETF") is True
    assert is_cn_t0_etf("510300.SH", "沪深300ETF") is False
    assert get_market_microstructure_rules("CN", "etf", "513050.SH", name="中概互联网ETF")["can_intraday_roundtrip"] is True
    assert get_market_microstructure_rules("CN", "etf", "510300.SH", name="沪深300ETF")["can_intraday_roundtrip"] is False

    # 即使调用方传入 asset_type="equity"，港交所 ETF 代码段也自动享受印花税豁免 (0 bps)
    rules_2800 = get_market_microstructure_rules("HK", "equity", "02800.HK")
    assert rules_2800["stamp_duty_buy_bps"] == 0.0 and rules_2800["stamp_duty_sell_bps"] == 0.0
    rules_700 = get_market_microstructure_rules("HK", "equity", "00700.HK")
    assert rules_700["stamp_duty_buy_bps"] == 10.0 and rules_700["stamp_duty_sell_bps"] == 10.0

    # resolve_symbol 解析腾讯搜索结果时，02800 自动识别为 etf
    rows = parse_search_response('v_hint="hk~02800~盈富基金~yffj~GP";')
    assert len(rows) == 1
    rec = to_candidate(rows[0])
    assert rec is not None
    assert rec["symbol"] == "02800.HK"
    assert rec["asset_type"] == "etf"


def test_lint_response_catches_contract_violations_and_passes_clean_report(replay_recorded, tmp_path: Path) -> None:
    from lint_response import lint_agent_response

    rep = run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path,
        run_backtest=False,
    )
    result, _ = load_v6(rep)
    clean_md = Path(rep["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    errs_clean = lint_agent_response(clean_md, result)
    assert errs_clean == [], errs_clean

    # 违规：把规则分说成「胜率 75%」，且捏造白名单外价格 9123.45，且删去免责声明
    bad_text = "本报告规则分对应的上涨概率为 75%，建议在目标位 9123.45 元附近离场。"
    errs_bad = lint_agent_response(bad_text, result)
    assert len(errs_bad) >= 2
    assert any("上涨概率" in e or "规则分" in e for e in errs_bad)
    assert any("9123.45" in e for e in errs_bad)

    # UNAVAILABLE 时任何带交易动作的价格（如 "1300.00 买入"）均被拦截
    unavail_rep = copy.deepcopy(result)
    unavail_rep["quality"]["status"] = "UNAVAILABLE"
    errs_unavail = lint_agent_response("建议在 1300.00 买入，不构成投资建议。", unavail_rep)
    assert any("UNAVAILABLE" in e for e in errs_unavail)
    assert any("免责声明" in e for e in errs_bad)


@pytest.mark.slow
def test_triple_barrier_entry_zone_anchored_and_rr_insufficient_veto(replay_recorded) -> None:
    from analysis import analyze_bundle

    req = {"symbol": "00700.HK", "market": "HK", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    bundle = load_bundle(req, now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
    rep = analyze_bundle(bundle, req, include_replay=False)
    last_c = float(rep["series_export"]["bars"][-1]["close"])
    atr14 = float(rep["mathematical_indicators"]["volatility"]["atr14"])
    tb = rep["triple_barrier_plan"]
    ez_lo, ez_hi = tb["entry_zone"]
    # 入场区围绕最新收盘价，不再横跨整个 120 日通道（宽度不超过 1.2 * ATR14）
    assert (ez_hi - ez_lo) <= 1.2 * atr14
    # TP1 距离最新收盘价不超过 2.5 * ATR14（15日可达范围）
    assert (tb["take_profit_1"] - last_c) <= 2.5 * atr14

    # 回测中 RR_INSUFFICIENT 不再是死代码，能真实拦截上方空间不足或止损过宽的截面
    bt = run_plan_backtest(bundle, req, horizon_bars=15, step=10)
    assert bt["veto_reason_counts"].get("RR_INSUFFICIENT", 0) > 0
    assert bt["rule_score_ic"]["effective_independent_samples"] >= 20


@pytest.mark.slow
def test_regime_adaptive_scoring_and_breakeven_ratchet_boost_moutai_ic_and_winrate(replay_recorded) -> None:
    from analysis import analyze_bundle

    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    bundle = load_bundle(req, now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
    rep = analyze_bundle(bundle, req, include_replay=False)
    tf = rep["multi_timeframe_score"]["timeframes_aligned"]
    assert "causal_momentum_ic_15d" in tf
    assert tf["scoring_regime"] in ("MOMENTUM_TREND", "MEAN_REVERSION_ADAPTED")
    assert 0.0 <= tf["weekly_60w_position_pct"] <= 100.0

    bt = run_plan_backtest(bundle, req, horizon_bars=15, step=5)
    assert bt["rule_score_ic"]["spearman"] is not None and bt["rule_score_ic"]["spearman"] > 0.15
    assert bt["all_long_signals"]["win_rate_pct"] >= 50.0
    assert bt["all_long_signals"]["cumulative_return_pct"] > 0.0
    assert bt["long_confirmed"]["win_rate_pct"] >= 60.0
    assert bt["long_confirmed"]["cumulative_return_pct"] > 0.0


def test_p0_mean_reversion_rule_score_adjustment_is_disclosed(replay_recorded, tmp_path: Path) -> None:
    """均值回归体制下 rule_score 被旋转：必须保留四组分项合计、改用体制标签并在直出摘要中披露调整前后数值。"""
    rep = run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path,
        run_backtest=False,
    )
    mtf = rep["multi_timeframe_score"]
    assert mtf["components_total"] == round(sum(mtf["components"].values()), 1)
    adj = mtf["regime_adjustment"]
    # 录制的茅台行情（截至 2026-10-15）处于均值回归体制
    assert mtf["timeframes_aligned"]["scoring_regime"] == "MEAN_REVERSION_ADAPTED"
    assert adj["applied"] is True
    assert adj["components_total"] == mtf["components_total"] and adj["adjusted_rule_score"] == mtf["rule_score"]
    assert "多周期" not in mtf["rating_cn"] and mtf["rating_cn"].startswith("均值回归体制")
    md = Path(rep["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    digest = next(line for line in md.splitlines() if "直出摘要" in line)
    assert f"四组规则分项合计 {mtf['components_total']:.1f} 经反转调整为 {mtf['rule_score']:.1f}" in digest


def test_p0_lint_rejects_skill_md_counterexample_and_unvalidated_probabilities(replay_recorded, tmp_path: Path) -> None:
    """SKILL.md 自己列出的错误回复必须被 lint 拦截；合规的报告、否定式说明与带 95% 区间的回测胜率不得误报。"""
    from lint_response import lint_agent_response
    from price_text import collect_price_whitelist

    rep = run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path,
        run_backtest=False,
    )
    assert rep["debate_and_veto"]["risk_officer_veto"] is True
    result, _ = load_v6(rep)
    assert result["verdict"]["risk_veto"] is True
    md = Path(rep["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    assert lint_agent_response(md, result) == []

    disclaimer = "以上为规则化技术分析，仅供研究参考，不构成投资建议。"
    counter = f"风控否决：大盘偏弱。茅台已经向上突破，上涨概率约 70%，胜算七成，大概率突破，建议 1330 附近买入，目标 1440。\n{disclaimer}"
    errs = lint_agent_response(counter, result)
    for token in ("上涨概率", "概率约 70%", "胜算", "大概率", "七成", "1330", "1440"):
        assert any(token in e for e in errs), (token, errs)

    ok_text = (
        "风控否决：大盘环境评分偏弱。规则分不是胜率或上涨概率，回复里不得出现「大概率突破」之类的说法。"
        "回测全部做多信号胜率 50.0%（95% 区间 21.5% ~ 78.5%），成交仅 8 笔。" + disclaimer
    )
    assert lint_agent_response(ok_text, result) == []

    late_veto = "茅台的结构说明。" * 60 + "风控否决：大盘偏弱。" + disclaimer
    assert any("置顶" in e for e in lint_agent_response(late_veto, result))

    # 价格白名单只收价格字段：频率、根数、比值、百分比不能让捏造的"价格"蒙混过关
    wl = collect_price_whitelist(
        {k: rep[k] for k in ("mathematical_indicators", "smc_structures", "volume_profile",
                             "triple_barrier_plan", "rule_scenarios", "future_projection_15d") if k in rep},
        int(rep["meta"].get("price_decimals") or 2),
        (rep.get("series_export") or {}).get("bars") or [],
    )["prices"]
    assert any(abs(p - rep["rule_scenarios"]["bear"]["rebound_cap"]) <= 0.01 for p in wl)


def test_p1_all_eight_audit_items_regression(replay_recorded, tmp_path: Path) -> None:
    """针对 8 项 P1 审查问题的逐项回归测试。"""
    from zoneinfo import ZoneInfo
    from analysis import analyze_bundle
    from market_rules import get_daily_price_limit_pct, get_market_microstructure_rules, round_to_tick
    from risk import compute_triple_barrier_plan

    # 1. 回测与线上计划一致：result.json 含 breakeven_ratchet，回测 entry_rule 不写“无移动止损”，硬止损统一为盘中触及
    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    bundle = load_bundle(req, now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
    rep = analyze_bundle(bundle, req, include_replay=False)
    tb = rep["triple_barrier_plan"]
    ratchet = tb["breakeven_ratchet"]
    assert ratchet["enabled"] is True
    assert ratchet["trigger_r"] == 0.90 and ratchet["confirm_close_r"] == 0.30 and ratchet["lock_r"] == 0.08
    assert ratchet["trigger_price"] > ratchet["confirm_close_price"] > ratchet["breakeven_stop_price"] > tb["stop_loss_barrier"]
    assert "盘中" in tb["left_side_constraints"]["exit_rule"] and "收盘跌破" not in tb["left_side_constraints"]["exit_rule"]
    bt = run_plan_backtest(bundle, req, horizon_bars=15, step=15)
    assert "无移动止损" not in bt["entry_rule"] and "+0.90R" in bt["entry_rule"] and "+0.08R" in bt["entry_rule"]

    # 2. 情景前提诚实标注：前提未满足的补足情景绝不标为 precondition_met=True
    bars_sh = load_recorded("sina_daily/sh000001.json")
    from structure import compute_structure_layer
    for cut in (220, 240):
        layer_cut = compute_structure_layer(bars_sh[:cut], None, 120, horizon_bars=15)
        proj_cut = layer_cut["projections"]
        for sc_item in proj_cut["scenarios"]:
            if sc_item["applicability_score"] < 80.0:
                assert sc_item["precondition_met"] is False
                assert sc_item["selection_role"] == "CONDITIONAL_FALLBACK"
                assert sc_item["status"] == "PENDING_TRIGGER"

    # 4. --as-of 设为当天且尚未收盘时，必须拆出盘中未收盘 K 线，不得算进已完成序列
    sh_tz = ZoneInfo("Asia/Shanghai")
    intraday_now = datetime(2026, 10, 15, 10, 30, tzinfo=sh_tz)
    b_asof_today = load_bundle({**req, "as_of": "2026-10-15"}, now=intraday_now)
    assert b_asof_today["target"]["bars"][-1]["day"] == "2026-10-14"
    assert b_asof_today["target"]["intraday"] is not None and b_asof_today["target"]["intraday"]["day"] == "2026-10-15"
    local_file = tmp_path / "local_bars.json"
    local_file.write_text(json.dumps(load_recorded("sina_daily/sh600519.json")[-200:]), encoding="utf-8")
    b_local_today = load_bundle({**req, "as_of": "2026-10-15"}, input_path=local_file, allow_network=False, now=intraday_now)
    assert b_local_today["target"]["bars"][-1]["day"] == "2026-10-14"
    assert b_local_today["target"]["intraday"] is not None and b_local_today["target"]["intraday"]["day"] == "2026-10-15"

    # 5. 科创板、创业板及双创 ETF 涨跌幅限制为 ±20%（主板宽基 ETF 仍为 ±10%）
    assert get_daily_price_limit_pct("CN", "etf", "588000.SH") == 0.20
    assert get_daily_price_limit_pct("CN", "etf", "588200.SH") == 0.20
    assert get_daily_price_limit_pct("CN", "etf", "159915.SZ", day_str="2026-10-15") == 0.20
    assert get_daily_price_limit_pct("CN", "etf", "159915.SZ", day_str="2020-01-15") == 0.10
    assert get_daily_price_limit_pct("CN", "etf", "159949.SZ") == 0.20
    assert get_daily_price_limit_pct("CN", "etf", "159781.SZ") == 0.20
    assert get_daily_price_limit_pct("CN", "etf", "159999.SZ", name="创业板人工智能ETF") == 0.20
    assert get_daily_price_limit_pct("CN", "etf", "510300.SH") == 0.10
    assert get_market_microstructure_rules("CN", "etf", "588000.SH")["daily_price_limit_pct"] == 0.20
    assert get_market_microstructure_rules("CN", "etf", "159915.SZ")["daily_price_limit_pct"] == 0.20

    # 6/7 共用的伪造输入：把看多目标抬到现价 +15%，其余沿用真实报告
    fake_proj = copy.deepcopy(rep["rule_scenarios"])
    fake_bench = {**rep["market_benchmark"], "regime_score": 60.0, "benchmark_missing": False}
    fake_proj["bull"]["target_price"] = float(bundle["target"]["bars"][-1]["close"]) * 1.15

    # 7. 均值回归体制下 LEFT_SIDE_PULLBACK 入场区与止损按左侧回踩公式计算，且要求 RVOL20 <= 1.10 缩量
    mr_mtf = copy.deepcopy(rep["multi_timeframe_score"])
    mr_mtf["rule_score"] = 70.0
    mr_mtf["timeframes_aligned"]["scoring_regime"] = "MEAN_REVERSION_ADAPTED"
    mr_mtf["timeframes_aligned"]["weekly_60w_position_pct"] = 40.0
    mr_ind = copy.deepcopy(rep["mathematical_indicators"])
    last_c = float(bundle["target"]["bars"][-1]["close"])
    atr14 = float(mr_ind["volatility"]["atr14"])
    mr_ind["trend"]["ema20"] = last_c  # ext_atr == 0.0 <= 0.15
    mr_ind["momentum"]["rsi14"] = 48.0
    mr_ind["momentum"]["rvol20"] = 0.85  # 缩量 <= 1.10
    mr_wedge = {**rep["mathematical_indicators"]["structure_levels"]["wedge_structure"], "status": "inside"}
    s1 = float(fake_proj["breakdown_level"]["price"])
    r1 = float(fake_proj["breakout_level"]["price"])
    dec_mr_ok, tb_mr_ok, _ = compute_triple_barrier_plan(
        bundle["target"]["bars"], rep["meta"], mr_ind, mr_wedge, {}, fake_proj, mr_mtf, [], fake_bench
    )
    assert dec_mr_ok["setup_type"] == "LEFT_SIDE_PULLBACK"
    exp_lo = round_to_tick(max(s1 * 0.995, last_c - 0.45 * atr14), "CN", "equity", rep["meta"]["as_of"])
    exp_hi = round_to_tick(min(r1, last_c + 0.20 * atr14), "CN", "equity", rep["meta"]["as_of"])
    exp_sl = round_to_tick(min(s1 - 0.15 * atr14, max(s1 - 0.50 * atr14, last_c - 1.45 * atr14)), "CN", "equity", rep["meta"]["as_of"])
    assert tb_mr_ok["entry_zone"] == [exp_lo, exp_hi]
    assert tb_mr_ok["stop_loss_barrier"] == exp_sl
    # 放量下跌 (rvol20 = 1.60 > 1.10) 且无反转形态时，不得确认为 LEFT_SIDE_PULLBACK
    mr_ind_high_vol = copy.deepcopy(mr_ind)
    mr_ind_high_vol["momentum"]["rvol20"] = 1.60
    dec_mr_hv, _, _ = compute_triple_barrier_plan(
        bundle["target"]["bars"], rep["meta"], mr_ind_high_vol, mr_wedge, {}, fake_proj, mr_mtf, [], fake_bench
    )
    assert dec_mr_hv["setup_type"] != "LEFT_SIDE_PULLBACK"
    assert dec_mr_hv["action"] == "NEUTRAL_HOLD_WAIT"

    # 8. SKILL.md 与 references/ 不得残留无脚本/无数据支撑的“1,500 股盲测”引用
    skill_root = Path(__file__).resolve().parent.parent
    for doc_path in [skill_root / "SKILL.md", *sorted((skill_root / "references").glob("*.md"))]:
        txt = doc_path.read_text(encoding="utf-8")
        assert "1,500" not in txt and "1500" not in txt and "盲测" not in txt, f"Unverified claim in {doc_path}"


# ---------------------------------------------------------------------------
# 资金与K线计算深度审查回归（仓位 / 资金参数 / 涨跌停撮合 / 低价品种指标精度 / 计划几何）
# ---------------------------------------------------------------------------


def test_position_sizing_uses_worst_fill_and_is_withheld_under_veto(replay_recorded, tmp_path: Path) -> None:
    from analysis import analyze_bundle
    from render_report_md import render_report_markdown

    now = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    base = {"symbol": "510300.SH", "market": "CN", "asset_type": "etf", "display_bars": 120, "horizon_bars": 15,
            "account_equity": 300000.0, "max_risk_pct": 0.01}
    # 2025-09-12：未被否决的做多确认。按入场区上沿（最差成交价）计每股风险，区内任意价位成交触及止损都不超预算
    hist_req = {**base, "as_of": "2025-09-12"}
    hist = analyze_bundle(load_bundle(hist_req, now=now), hist_req, include_replay=False)
    assert hist["meta_labeling_decision"]["action"] == "LONG_CONFIRMED" and not hist["debate_and_veto"]["risk_officer_veto"]
    tb, sz = hist["triple_barrier_plan"], hist["triple_barrier_plan"]["position_sizing"]
    fee = round_trip_cost_rate(tb["market_microstructure"])
    worst = (tb["entry_zone"][1] - tb["stop_loss_barrier"]) + tb["entry_zone"][1] * fee
    assert sz["sizing_basis"] == "ENTRY_HIGH_WORST_FILL" and abs(sz["unit_risk_per_share"] - worst) < 1e-3
    assert sz["recommended_shares"] > 0 and sz["recommended_shares"] % 100 == 0
    assert sz["recommended_shares"] * worst <= sz["risk_budget_amount"] + 1e-6
    assert sz["max_loss_at_stop"] <= sz["risk_budget_amount"] and "跌停" in sz["gap_risk_note"]
    md = render_report_markdown(hist)
    assert f"**{sz['recommended_shares']} 股**" in md and "触及止损约亏" in md

    # 2026-10-15：风控否决（大盘偏弱等），股数本身就是买入建议，必须不给出，并写明原因
    rep = analyze_bundle(load_bundle(base, now=now), base, include_replay=False)
    assert rep["debate_and_veto"]["risk_officer_veto"]
    sz_now = rep["triple_barrier_plan"]["position_sizing"]
    assert sz_now["recommended_shares"] is None and sz_now["estimated_notional"] is None and sz_now["max_loss_at_stop"] is None
    assert "风控已否决" in sz_now["reason"]
    md_now = render_report_markdown(rep)
    assert "不给出建议股数" in md_now and "按入场区上沿" not in md_now


@pytest.mark.parametrize(
    ("field", "value"),
    [("max_risk_pct", 1.0), ("max_risk_pct", 0.0), ("max_risk_pct", -0.01), ("max_risk_pct", float("nan")),
     ("account_equity", -100.0), ("account_equity", float("inf"))],
)
def test_money_parameters_are_validated_before_fetching(tmp_path: Path, field: str, value: float) -> None:
    # 1% 误写成 1 会让单笔风险变成 100% 净值：必须在取数前直接拒绝（测试断网，校验先于取数才不会报 DATA_UNAVAILABLE）
    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15,
           "account_equity": 300000.0, "max_risk_pct": 0.01, field: value}
    with pytest.raises(ValueError, match="max-risk-pct|account-equity"):
        run_pipeline(req, output_dir=tmp_path)


def test_p1_backtest_limit_prices_use_exchange_rounding_and_one_price_boards() -> None:
    from market_rules import daily_limit_prices

    # 交易所规则：前收 × (1 ± 涨跌幅) 按价位四舍五入；十进制计算避免 1.30 × 0.95 = 1.2349999… 被算成 1.23
    assert daily_limit_prices(1.30, 0.05) == (1.37, 1.24)
    assert daily_limit_prices(1.46, 0.05) == (1.53, 1.39)
    assert daily_limit_prices(1235.58, 0.10) == (1359.14, 1112.02)
    assert daily_limit_prices(4.242, 0.10, "CN", "etf") == (4.666, 3.818)

    # ST信通（600289.SH，主板 ST 当时 ±5%）真实日线：低价股的涨跌停价相对 1±5% 偏离可达 0.3%，旧的 0.1% 比例容差会漏判
    bars = load_recorded("sina_daily/sh600289.json")
    idx = {b["day"]: i for i, b in enumerate(bars)}
    kw = {"market": "CN", "asset_type": "equity", "symbol": "600289.SH", "is_st": True}
    for day in ("2024-02-23", "2024-05-08", "2025-01-15"):  # 开盘即一字涨停（如 2024-02-23 前收 1.47、开盘 1.54）
        res = label_triple_barrier_event(bars, entry_idx=idx[day], tp_price=99.0, sl_price=0.5, horizon_bars=5, **kw)
        assert res == {"tradable": False, "reason": "LIMIT_UP_NO_FILL", "entry_day": day}
    # 2024-04-12 开盘 1.22 买入、止损 1.20：04-15（1.24）与 04-16（1.18）均为一字跌停封死无法卖出，
    # 只能在 04-17 开盘 1.12 离场；旧逻辑漏判 04-16 封板，会按 1.18 止损成交而低估约 5 个百分点的亏损
    res = label_triple_barrier_event(
        bars, entry_idx=idx["2024-04-12"], tp_price=99.0, sl_price=1.20, horizon_bars=5, trailing_stop=False, **kw
    )
    assert res["outcome"] == "SL_BARRIER" and res["exit_day"] == "2024-04-17" and res["exit_price"] == 1.12


def test_indicator_snapshot_keeps_precision_for_low_priced_instruments() -> None:
    # 快照参与 (收盘-EMA20)/ATR、EMA20 与 EMA50、收盘与布林带的比较；低价 ETF 舍入到 2 位小数会翻转判断
    etf = load_recorded("sina_daily/sh510300.json")[-420:]
    ind = compute_all_indicators(etf)
    snap, series = ind["snapshot"], ind["series"]
    assert abs(snap["trend"]["ema20"] - series["ema"][20][-1]) <= 0.5e-4
    assert abs(snap["trend"]["ema50"] - series["ema"][50][-1]) <= 0.5e-4
    assert abs(snap["volatility"]["bb_upper"] - series["bollinger"]["upper"][-1]) <= 0.5e-4
    assert abs(snap["volatility"]["atr14"] - series["atr14"][-1]) <= 0.5e-5
    # 价格 ≥ 100 的品种保持 2 位小数，输出与既有报告一致
    moutai = compute_all_indicators(load_recorded("sina_daily/sh600519.json")[-420:])["snapshot"]
    assert moutai["trend"]["ema20"] == round(moutai["trend"]["ema20"], 2)


def test_plan_geometry_guard_vetoes_stop_inside_entry_zone(replay_recorded) -> None:
    from analysis import analyze_bundle
    from risk import compute_triple_barrier_plan

    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    bundle = load_bundle(req, now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
    rep = analyze_bundle(bundle, req, include_replay=False)
    last_c = float(bundle["target"]["bars"][-1]["close"])
    # 触发位与破位位重合且都在现价下方时，突破计划的止损会落在入场区内：必须否决而不是输出可执行计划
    proj = copy.deepcopy(rep["rule_scenarios"])
    proj["breakout_level"]["price"] = proj["breakdown_level"]["price"] = round(last_c * 0.95, 2)
    wedge = {**rep["mathematical_indicators"]["structure_levels"]["wedge_structure"], "status": "above"}
    bench = {**rep["market_benchmark"], "regime_score": 60.0, "benchmark_missing": False}
    dec, tb, debate = compute_triple_barrier_plan(
        bundle["target"]["bars"], rep["meta"], rep["mathematical_indicators"], wedge, {}, proj,
        rep["multi_timeframe_score"], [], bench,
    )
    assert tb["stop_loss_barrier"] >= tb["entry_zone"][0]
    assert "PLAN_GEOMETRY_INVALID" in dec["veto_codes"] and debate["risk_officer_veto"]


def test_lint_rejects_share_count_when_report_withholds_sizing(replay_recorded) -> None:
    from analysis import analyze_bundle
    from contract_v6 import artifact_files, build_result
    from lint_response import lint_agent_response

    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15,
           "account_equity": 500000.0, "max_risk_pct": 0.01}
    rep = analyze_bundle(load_bundle(req, now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC)), req, include_replay=False)
    assert rep["triple_barrier_plan"]["position_sizing"]["recommended_shares"] is None
    result = build_result(rep, artifact_files("600519.SH", False))
    head = "【风控否决】大盘偏弱，以下点位只用于观察。交易制度：每手 100 股（最小申报 100 股）。以上不构成投资建议。"
    assert not any("买入股数" in e for e in lint_agent_response(head, result))
    errors = lint_agent_response(head + "按账户 50 万可买入 300 股。", result)
    assert any("买入股数" in e for e in errors)


def test_expert_audit_fixes_regression(replay_recorded, tmp_path: Path) -> None:
    """量化与 K 线专家全链路审计修复回归测试（跨市场 T+0/T+1 文案、左/右侧计划自洽、港股价位表、HTML 回放除权无前视、L3 校验与 Lint 角色拦截）。"""
    from lint_response import lint_agent_response
    from market_rules import round_to_tick

    # 1. 港股 00700.HK：止损文案不出现 A 股 T+1；左侧计划 bull_case[0] 不出现「突破 r1 后指向更低 TP1」倒挂；情景价格符合港股价位表
    rep_hk = run_pipeline(
        {"symbol": "00700.HK", "market": "HK", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path / "hk",
    )
    hk_md = Path(rep_hk["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    hk_html = Path(rep_hk["rendered_paths"]["dashboard_html"]).read_text(encoding="utf-8")
    assert "A 股受 T+1" not in hk_md
    assert "支持 T+0 当日止损" in hk_md
    assert "支持 T+0 当日止损" in rep_hk["triple_barrier_plan"]["left_side_constraints"]["exit_rule"]
    r1_hk = rep_hk["rule_scenarios"]["breakout_level"]["price"]
    tp1_hk = rep_hk["triple_barrier_plan"]["take_profit_1"]
    if tp1_hk <= r1_hk:
        assert f"站稳 {r1_hk:.2f}" not in rep_hk["debate_and_veto"]["bull_case"][0]
    for sc_item in rep_hk["rule_scenarios"]["scenarios"]:
        for pk in ("target_price", "invalidation_price", "reference_trigger_price"):
            pval = sc_item.get(pk)
            if pval is not None:
                assert round_to_tick(float(pval), "HK", "equity", rep_hk["meta"]["as_of"]) == float(pval)
    assert "观察区（风控已否决）" in hk_html

    # 2. A 股 600519.SH：HTML 历史回放快照使用当时点位复权价（2026-05-19 截面不泄漏 2026-06-26 分红），且 Lint 拦截浪型测算位冒充目标价、否决下做多建议与伪造回测胜率
    rep_cn = run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path / "cn",
    )
    snap0 = rep_cn["replay_snapshots"][0]
    # 2026-06-26 除息前的历史快照收盘价必须等于当日原始未除息价，而非扣减未来分红后的 1280+
    assert snap0["as_of"] < "2026-06-26"
    assert snap0["base_close"] > 1300.0
    assert snap0["bars_window"][-1]["close"] == snap0["base_close"]

    result_cn, _ = load_v6(rep_cn)
    disclaimer = "以上为规则化技术分析，仅供研究参考，不构成投资建议。"
    errs_buy_under_veto = lint_agent_response(f"风控否决：大盘偏弱，但仍建议逢低买入。{disclaimer}", result_cn)
    assert any("严禁给出做多/买入建议" in e for e in errs_buy_under_veto)
    errs_wave_as_target = lint_agent_response(f"风控否决：大盘偏弱，上方目标位 1440.44。{disclaimer}", result_cn)
    assert any("浪型测算位" in e for e in errs_wave_as_target)
    fake_bt = {"backtest": {"all_long_signals": {"win_rate_pct": 46.15}, "long_confirmed": {"win_rate_pct": 83.33}, "watch_long_setup": {"win_rate_pct": 38.46}}}
    errs_fake_wr = lint_agent_response(f"风控否决：大盘偏弱。回测胜率 95.0%（95% 区间 80%~99%）。{disclaimer}", result_cn, backtest=fake_bt)
    assert any("回测胜率" in e for e in errs_fake_wr)


def test_lint_audit_regressions_wave_numbers_small_sample_and_veto_bypass(replay_recorded, tmp_path: Path) -> None:
    """审计回归：浪号不误判为价格；n<30 胜率不得包装成 70% 证据；否决下带价格的买入建议必须拦截。"""
    from lint_response import lint_agent_response

    rep = run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path / "lint_audit",
    )
    result, _ = load_v6(rep)
    price = float(result["plan"]["long"]["stop_loss_barrier"])
    disclaimer = "以上为规则化技术分析，仅供研究参考，不构成投资建议。"

    wave_text = f"风控否决：大盘偏弱。若收盘跌破浪 1 起点 {price:.2f} 则上升推动浪计数失效。{disclaimer}"
    errs_wave = lint_agent_response(wave_text, result)
    assert not any("白名单中的价格" in e for e in errs_wave), errs_wave

    fake_bt = {"backtest": {
        "all_long_signals": {"win_rate_pct": 83.33, "trades": 6},
        "long_confirmed": {"win_rate_pct": 71.43, "trades": 14},
        "watch_long_setup": {"win_rate_pct": 50.0, "trades": 4},
    }}
    small_text = f"风控否决：大盘偏弱。回测胜率 83.3%（n=6），证明该系统达到 70% 目标。{disclaimer}"
    errs_small = lint_agent_response(small_text, result, backtest=fake_bt)
    assert any("小样本回测" in e for e in errs_small), errs_small

    bypass_text = (
        f"风控否决：大盘偏弱。建议在 {price:.2f} 附近买入做多；投资者亦可于 {price:.2f} 建仓，逢低吸纳。{disclaimer}"
    )
    errs_bypass = lint_agent_response(bypass_text, result)
    assert sum(1 for e in errs_bypass if "严禁给出做多/买入建议" in e) >= 2, errs_bypass


def test_historical_as_of_vix_fetches_ten_year_window(monkeypatch) -> None:
    """历史截面 VIX 必须取 10 年窗口；3mo 窗口过滤后会变成 null。"""
    seen: dict[str, str] = {}
    bars = load_recorded("yahoo_daily/VIX.json")

    def fake_vix(symbol: str, range_str: str = "5y", datalen: int | None = data_gateway.FETCH_BARS):
        seen["symbol"], seen["range"] = symbol, range_str
        return [dict(b) for b in bars]

    monkeypatch.setattr(data_gateway, "fetch_yahoo_daily_bars", fake_vix)
    value, source = data_gateway._fetch_vix("2024-06-14", True)
    assert seen == {"symbol": "^VIX", "range": "10y"}
    assert value is not None
    assert source.startswith("YAHOO_DAILY(^VIX")


def test_historical_as_of_never_uses_live_st_name(monkeypatch) -> None:
    """历史截面的实时快照简称含未来 ST 信息，必须禁用。"""
    inst = resolve_instrument("600519.SH", market="CN", asset_type="equity")
    monkeypatch.setattr(
        data_gateway, "fetch_tencent_quote_meta",
        lambda code: {"code": code, "name": "ST测试", "lot_size": None},
    )
    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity"}
    hist_out, hist_meta = data_gateway._enrich_instrument(inst, req, True, as_of="2021-06-15")
    assert hist_out["is_st"] is False
    assert hist_meta["error"] == "HISTORICAL_AS_OF_LIVE_METADATA_DISABLED"
    live_out, live_meta = data_gateway._enrich_instrument(dict(inst), req, True, as_of=None)
    assert live_out["is_st"] is True
    assert live_meta["vendor_name"] == "ST测试"


def test_tencent_index_fetch_is_capped_at_service_limit(monkeypatch) -> None:
    """腾讯指数请求 2600 根会返回空；必须按服务上限截断并披露。"""
    bars = load_recorded("tencent_daily/hkHSTECH.json")
    seen: dict[str, int] = {}

    def fake_tencent(code: str, datalen: int = 760):
        seen["datalen"] = datalen
        return [dict(b) for b in bars][-datalen:]

    monkeypatch.setattr(data_gateway, "fetch_tencent_index_daily_bars", fake_tencent)
    inst = resolve_instrument("HSTECH.HK", market="HK", asset_type="index")
    got, source, _errors, _placeholders = data_gateway._load_single_series(
        inst, datalen=2600, as_of="2024-06-14", allow_network=True
    )
    assert seen["datalen"] == data_gateway.TENCENT_INDEX_MAX_BARS
    assert "max=" in source
    assert got


def test_label_event_rejects_missing_next_bar_and_inverted_geometry() -> None:
    """T+1 市场末根入场不得 T+0 平仓；倒挂屏障不得产出胜场标签。"""
    bars = [dict(b) for b in load_recorded("sina_daily/sh600519.json")]
    last = len(bars) - 1
    entry = float(bars[last]["open"])
    res = label_triple_barrier_event(
        bars, last, entry * 1.05, entry * 0.95, horizon_bars=15,
        market="CN", asset_type="equity", symbol="600519.SH",
    )
    assert res["tradable"] is False
    assert res["reason"] == "INSUFFICIENT_FUTURE_BARS"
    prev = last - 1
    entry_prev = float(bars[prev]["open"])
    res_bad = label_triple_barrier_event(
        bars[:-1], prev, entry_prev * 0.95, entry_prev * 1.05, horizon_bars=15,
        market="CN", asset_type="equity", symbol="600519.SH",
    )
    assert res_bad["tradable"] is False
    assert res_bad["reason"] == "INVALID_PLAN_GEOMETRY"


def test_stamp_duty_uses_historical_effective_rates() -> None:
    from market_rules import get_market_microstructure_rules, round_trip_cost_rate

    cn_2021 = get_market_microstructure_rules("CN", "equity", "600519.SH", day_str="2021-06-15")
    cn_2024 = get_market_microstructure_rules("CN", "equity", "600519.SH", day_str="2024-06-15")
    assert cn_2021["stamp_duty_sell_bps"] == 10.0
    assert cn_2024["stamp_duty_sell_bps"] == 5.0
    hk_2022 = get_market_microstructure_rules("HK", "equity", "00700.HK", day_str="2022-06-15")
    hk_2024 = get_market_microstructure_rules("HK", "equity", "00700.HK", day_str="2024-06-15")
    assert hk_2022["stamp_duty_buy_bps"] == 13.0
    assert hk_2024["stamp_duty_buy_bps"] == 10.0
    assert round_trip_cost_rate(cn_2021) > round_trip_cost_rate(cn_2024)


def test_overextended_warning_not_masked_by_momentum_crowding(replay_recorded) -> None:
    """过热（会阻止做多确认）与动量拥挤（仅提示）同时成立时，必须披露过热降级，而不是"不改变评级"。"""
    from risk import compute_triple_barrier_plan

    bundle = load_bundle(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC),
    )
    rep = analyze_bundle(bundle, {"symbol": "600519.SH", "display_bars": 120, "horizon_bars": 15}, include_replay=False)
    bars = bundle["target"]["bars"]
    last_c = float(bars[-1]["close"])
    ind = copy.deepcopy(rep["mathematical_indicators"])
    atr = float(ind["volatility"]["atr14"])
    ind["trend"]["ema20"] = last_c - 2.5 * atr  # ext_atr = +2.5：同时满足过热(>2.0)与拥挤(>1.65)
    bench = {**rep["market_benchmark"], "regime_score": 60.0, "benchmark_missing": False}
    dec, _, _ = compute_triple_barrier_plan(
        bars,
        rep["meta"],
        ind,
        rep["mathematical_indicators"]["structure_levels"]["wedge_structure"],
        {},
        rep["rule_scenarios"],
        rep["multi_timeframe_score"],
        [],
        bench,
    )
    warnings = dec["anti_quant_traps"]["warnings_cn"]
    assert dec["overextended"] is True and dec["anti_quant_traps"]["momentum_crowding"] is True
    assert any(w.startswith("【过热降级】") for w in warnings)
    assert not any("不改变评级" in w and "短期涨幅偏大" in w for w in warnings)
    assert dec["action"] != "LONG_CONFIRMED"


def test_round_number_stop_avoidance_only_on_fine_tick_grids() -> None:
    """整数关口避让只在 tick 远小于 0.5 时生效；移动以关口为锚，移完不再贴着关口。"""
    from risk import _avoid_round_number_stop

    # A 股 tick 0.01：落在关口 ±1 tick 内都移到关口下方 2 tick
    assert _avoid_round_number_stop(10.00, 11.0, "CN", "equity", "2026-10-16") == pytest.approx(9.98)
    assert _avoid_round_number_stop(10.01, 11.0, "CN", "equity", "2026-10-16") == pytest.approx(9.98)
    assert _avoid_round_number_stop(10.51, 11.0, "CN", "equity", "2026-10-16") == pytest.approx(10.48)
    assert _avoid_round_number_stop(10.23, 11.0, "CN", "equity", "2026-10-16") == pytest.approx(10.23)
    # 港股 200~500 档 tick 0.2：每个价位都离 0.5 关口不超过 1 tick，不能一律下移
    for px in (400.0, 400.2, 400.4, 400.6, 400.8):
        assert _avoid_round_number_stop(px, 450.0, "HK", "equity", "2026-10-16") == pytest.approx(px)



def test_snapshot_exposes_previous_rsi(replay_recorded) -> None:
    bars = load_recorded("sina_daily/sh600519.json")
    ind = compute_all_indicators(bars)
    series = ind["series"]["rsi14"]
    assert ind["snapshot"]["momentum"]["rsi14_prev"] == pytest.approx(round(float(series[-2]), 2))


# ---------------------------------------------------------------------------
# 反量化收割 / 全栈审计修复回归（真实录制行情，不使用任何合成K线）
# ---------------------------------------------------------------------------


def test_liquidity_sweep_bullish_detected_on_real_bars_and_is_causal() -> None:
    """破底翻扫损收回：真实茅台 2025-07-02 出现形态；截断到该日之前时不得提前出现（因果性）。"""
    bars = load_recorded("sina_daily/sh600519.json")
    ind = compute_all_indicators(bars)
    pats = detect_candlestick_patterns(bars, ind["series"]["atr14"], ind["series"]["rvol20"], lookback=len(bars), max_items=None)
    sweeps = {p["observed_at"]: p for p in pats if p["pattern"] == "LIQUIDITY_SWEEP_BULLISH"}
    assert "2025-07-02" in sweeps
    p = sweeps["2025-07-02"]
    assert p["direction"] == "BULLISH" and "PriorLow20" in p["formula_verified"]
    idx = next(i for i, b in enumerate(bars) if b["day"] == "2025-07-02")
    cut = bars[:idx]
    ind_cut = compute_all_indicators(cut)
    pats_cut = detect_candlestick_patterns(cut, ind_cut["series"]["atr14"], ind_cut["series"]["rvol20"], lookback=len(cut), max_items=None)
    assert "2025-07-02" not in {x["observed_at"] for x in pats_cut}


def _ignition_case(replay_recorded, market_override: str | None):
    """在真实茅台最后一根K线上只改日内形态（高开低走 + 长上影），其余结构原样；可切换 meta.market 验证门禁范围。"""
    from risk import compute_triple_barrier_plan

    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    bundle = load_bundle(req, now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
    rep = analyze_bundle(bundle, req, include_replay=False)
    bars = copy.deepcopy(bundle["target"]["bars"])
    last = bars[-1]
    c0 = float(last["close"])
    last["open"], last["high"], last["low"], last["close"] = c0 * 1.01, c0 * 1.03, c0 * 0.995, c0
    meta = {**rep["meta"], **({"market": market_override} if market_override else {})}
    wedge = {**rep["mathematical_indicators"]["structure_levels"]["wedge_structure"], "status": "above"}
    bench = {**rep["market_benchmark"], "regime_score": 60.0, "benchmark_missing": False}
    ind = copy.deepcopy(rep["mathematical_indicators"])
    ind["momentum"]["rvol20"] = 1.60
    return compute_triple_barrier_plan(bars, meta, ind, wedge, {}, rep["rule_scenarios"], rep["multi_timeframe_score"], [], bench)[0]


def test_ignition_trap_downgrades_only_where_evidence_supports(replay_recorded) -> None:
    """Dev/Val 归因：US 上点火假突破降级有效；CN 上被拦截组反而更好，只提示不降级。"""
    us = _ignition_case(replay_recorded, "US")
    assert us["setup_type"] == "WAIT_CONFIRMATION"
    assert us["anti_quant_traps"]["ignition_bull_trap_downgraded"] is True
    assert us["anti_quant_traps"]["gates_active"]["ignition_trap_downgrade"] is True
    assert any("防冲高回落假突破" in w for w in us["anti_quant_traps"]["warnings_cn"])

    cn = _ignition_case(replay_recorded, None)
    assert cn["setup_type"] == "RIGHT_SIDE_BREAKOUT"
    assert cn["anti_quant_traps"]["ignition_bull_trap_downgraded"] is False
    assert cn["anti_quant_traps"]["gates_active"]["ignition_trap_downgrade"] is False
    assert any("仅提示" in w for w in cn["anti_quant_traps"]["warnings_cn"])


def test_hint_only_rules_never_veto_or_downgrade(replay_recorded) -> None:
    """高位对倒与动量拥挤不再产生否决码，也不改变 overextended。"""
    dec = _ignition_case(replay_recorded, None)
    assert "QUANT_BULL_TRAP_CHURN" not in dec["veto_codes"]
    gates = dec["anti_quant_traps"]["gates_active"]
    assert gates["bull_trap_churn"] is False and gates["momentum_crowding"] is False


def test_st_limit_pct_uses_name_when_flag_missing() -> None:
    """名称含 ST 时即使调用方没传 is_st，主板涨跌停也必须按 ±5% 处理（2026-07-06 前）。"""
    assert get_daily_price_limit_pct("CN", "equity", "600079.SH", day_str="2024-03-01", name="ST人福") == 0.05
    assert get_daily_price_limit_pct("CN", "equity", "600079.SH", day_str="2024-03-01", name="人福医药") == 0.10


def test_cached_and_recorded_sources_are_not_unknown() -> None:
    for src in ("CACHE", "CACHED_CN_BENCHMARK", "CACHED_US_DAILY", "RECORDED_FIXTURE"):
        assert classify_data_source(src) == "LIVE"
        assert assess_data_quality(src, src, needs_benchmark=True)["status"] == "OK"
    assert classify_data_source("DETERMINISTIC_SYNTHETIC_FIXTURE") == "SYNTHETIC"


def test_pit_rescale_precision_after_large_split() -> None:
    """adj_factor 保留 12 位小数：大比例拆股后 rescale 与直接按截面复权的差异 < 1e-9（收盘价 4 位小数内一致）。"""
    bars = [{"day": f"2024-01-{d:02d}", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0 + d * 0.01, "volume": 1000} for d in range(1, 21)]
    actions = [{"ex_date": "2024-01-15", "split_ratio": 40.0, "dividend_factor": 1.0}]
    full = apply_point_in_time_adjustment(bars, actions, mode="split", as_of_day="2024-01-20")
    cut_idx = 9  # 2024-01-10，早于拆股日
    direct = apply_point_in_time_adjustment(bars[: cut_idx + 1], actions, mode="split", as_of_day="2024-01-10")
    rescaled = rescale_bars_as_of(full, cut_idx)[: cut_idx + 1]
    for a, b in zip(direct, rescaled):
        assert abs(a["close"] - b["close"]) < 1e-9


def _real_report_for_lint(replay_recorded) -> dict:
    from contract_v6 import artifact_files, build_result

    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    internal = analyze_bundle(load_bundle(req, now=datetime(2026, 10, 16, 12, 0, tzinfo=UTC)), req, include_replay=False)
    return build_result(internal, artifact_files("600519.SH", False))


def test_lint_blocks_probability_bragging_variants_but_allows_negations(replay_recorded) -> None:
    from lint_response import lint_agent_response

    report = copy.deepcopy(_real_report_for_lint(replay_recorded))
    report["verdict"]["risk_veto"] = False
    bt = {"backtest": {"all_long_signals": {"trades": 6, "win_rate_pct": 83.3}}}
    base = "不构成投资建议。"
    for bad in (
        "该信号命中概率达到 72%。",
        "形态准确率超 75%。",
        "多周期规则评分的确定性很高。",
        "主力真实筹码正在吸筹。",
    ):
        assert lint_agent_response(bad + base, report), bad
    ok = "Volume Profile 是成交分布近似，不是真实持仓筹码，也不代表获利盘比例。" + base
    assert not [e for e in lint_agent_response(ok, report) if "违规表述" in e]
    small = "回测胜率为 83.3%（n=6），足以说明已达到 70% 目标。" + base
    assert any("小样本" in e for e in lint_agent_response(small, report, bt))


def test_lint_catches_every_buy_shares_mention_under_veto(replay_recorded) -> None:
    from lint_response import lint_agent_response

    rep = copy.deepcopy(_real_report_for_lint(replay_recorded))
    rep["verdict"]["risk_veto"] = True
    rep["plan"]["long"]["position_sizing"]["recommended_shares"] = None
    text = "【风控否决】观察。港股每手 100 股，仓位按 100 股/手假设计算。直接买入 500 股。不构成投资建议。"
    assert any("买入股数" in e for e in lint_agent_response(text, rep))
    clean = "【风控否决】观察。仓位按 100 股/手假设计算。不构成投资建议。"
    assert not any("买入股数" in e for e in lint_agent_response(clean, rep))


def test_png_draws_volume_profile_poc(replay_recorded, tmp_path: Path) -> None:
    """PNG 必须同源绘制 POC（图例与右侧标签）；报告里 HTML 观察区全角一致。"""
    rep = run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path,
    )
    src = Path(render_structure_chart.__file__).read_text(encoding="utf-8")
    assert "volume_profile" in src and "POC 成交分布近似" in src
    assert rep["volume_profile"]["poc_price"] > 0
    assert "获利盘" not in src and "主力筹码" not in src


def test_structure_fib_ratios_complete(replay_recorded) -> None:
    from structure import RETRACE_RATIOS

    assert RETRACE_RATIOS == (0.236, 0.382, 0.5, 0.618, 0.786)
    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    layer = compute_structure_layer(load_recorded("sina_daily/sh600519.json"), None, 120)
    assert [lv["ratio"] for lv in layer["fibs"]["retracements"]] == list(RETRACE_RATIOS)
