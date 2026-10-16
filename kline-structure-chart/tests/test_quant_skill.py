from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from analysis import analyze_bundle
from analyze_and_forecast import run_pipeline
from contracts import sanitize_filename, validate_bars, validate_report
from data_gateway import generate_deterministic_series, load_bundle
from evaluation import (
    label_triple_barrier_event,
    run_causal_replay_audit,
)
from forecast import compute_conformal_forecast, match_historical_analogues
from indicators import compute_all_indicators
from market_rules import (
    apply_point_in_time_adjustment,
    get_daily_price_limit_pct,
    next_trading_dates,
    resolve_instrument,
    round_to_tick,
)
from patterns import (
    causal_nadaraya_watson,
    causal_swing_pivots,
    compute_volume_profile,
    detect_candlestick_patterns,
    detect_smc_structures,
)
from render_html import render_html


def test_contracts_reject_nan_and_quantile_crossing() -> None:
    bundle = load_bundle(
        {"symbol": "000001.SH", "market": "CN", "asset_type": "index", "display_bars": 120, "horizon_bars": 15},
        allow_network=False,
        allow_synthetic_fallback=True,
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
    # CN Index has no price limit
    assert get_daily_price_limit_pct("CN", "index", "000001.SH") is None
    # CN Main board ±10%, ChiNext/STAR ±20%, BSE ±30%；主板 ST 见 test_p1_st_price_limits_by_board_and_date
    assert get_daily_price_limit_pct("CN", "equity", "600519.SH") == 0.10
    assert get_daily_price_limit_pct("CN", "equity", "300750.SZ", day_str="2026-10-15") == 0.20
    assert get_daily_price_limit_pct("CN", "equity", "300750.SZ", day_str="2020-01-15") == 0.10
    assert get_daily_price_limit_pct("CN", "equity", "688981.SH") == 0.20
    assert get_daily_price_limit_pct("CN", "equity", "830799.BJ") == 0.30
    assert get_daily_price_limit_pct("CN", "equity", "600000.SH", is_st=True, day_str="2026-07-03") == 0.05
    # HK & US have no daily price limit
    assert get_daily_price_limit_pct("HK", "equity", "00700.HK") is None
    assert get_daily_price_limit_pct("US", "equity", "NVDA.US") is None

    # Holiday skipping around CN National Day (2026-10-01..2026-10-07)
    dates = next_trading_dates("2026-09-30", 3, market="CN")
    assert "2026-10-01" not in dates
    assert dates[0] == "2026-10-08"

    # HK tick size table
    assert round_to_tick(412.23, market="HK", asset_type="equity") == 412.20


def test_point_in_time_adjustment_no_future_leakage() -> None:
    raw_bars = [
        {"day": "2026-05-08", "open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0, "volume": 1000.0, "complete": True},
        {"day": "2026-05-11", "open": 50.0, "high": 52.0, "low": 49.0, "close": 51.0, "volume": 2000.0, "complete": True},
    ]
    actions = [{"ex_date": "2026-05-11", "split_ratio": 2.0, "dividend_factor": 1.0}]
    # As of 2026-05-08 (before split ex_date), price must remain 100.0
    before = apply_point_in_time_adjustment(raw_bars[:1], actions, mode="split", as_of_day="2026-05-08")
    assert before[0]["close"] == 100.0
    # As of 2026-05-11 (on/after split ex_date), prior bar is adjusted to 50.0 while raw_close stays 100.0
    after = apply_point_in_time_adjustment(raw_bars, actions, mode="split", as_of_day="2026-05-11")
    assert after[0]["close"] == 50.0
    assert after[0]["raw_close"] == 100.0


def test_indicators_and_algebraic_patterns_and_smc() -> None:
    bars = generate_deterministic_series("600519.SH", market="CN", num_bars=260)
    ind = compute_all_indicators(bars)
    snap = ind["snapshot"]
    assert snap["trend"]["ema20"] is not None
    assert snap["trend"]["supertrend_dir"] in (-1, 1)
    assert 0.0 <= float(snap["momentum"]["rsi14"]) <= 100.0
    assert -1.0 <= float(snap["qlib_factors"]["corr_close_logvol_20d"]) <= 1.0

    # Inject a textbook Bullish Hammer on the final bar and verify algebraic detection
    hammer_bars = [dict(b) for b in bars[:225]]
    low_20 = min(b["low"] for b in hammer_bars[-20:])
    c_hammer = low_20 * 0.995
    o_hammer = c_hammer * 0.996
    h_hammer = c_hammer * 1.001
    l_hammer = c_hammer * 0.965
    hammer_bars[-1] = {
        "day": hammer_bars[-1]["day"],
        "open": round(o_hammer, 2),
        "high": round(h_hammer, 2),
        "low": round(l_hammer, 2),
        "close": round(c_hammer, 2),
        "volume": hammer_bars[-2]["volume"] * 2.0,
        "complete": True,
    }
    ind_h = compute_all_indicators(hammer_bars)
    cdl = detect_candlestick_patterns(hammer_bars, ind_h["series"]["atr14"], ind_h["series"]["rvol20"], lookback=3)
    assert any(p["pattern"] == "CDL_BULLISH_HAMMER" for p in cdl)

    pivs = causal_swing_pivots(bars, 0.035)
    smc = detect_smc_structures(bars, pivs, ind["series"]["atr14"])
    vp = compute_volume_profile(bars, lookback=120)
    assert vp["val_price"] <= vp["poc_price"] <= vp["vah_price"]
    assert "fvgs" in smc and "order_blocks" in smc


def test_causal_replay_invariance_no_lookahead() -> None:
    bars = generate_deterministic_series("000300.SH", market="CN", num_bars=260)
    # Causal Nadaraya-Watson at index 150 must be identical whether computed on bars[:151] or bars[:]
    nw_short = causal_nadaraya_watson([b["close"] for b in bars[:151]], bandwidth=3.0)
    nw_full = causal_nadaraya_watson([b["close"] for b in bars], bandwidth=3.0)
    assert math.isclose(nw_short[150], nw_full[150], rel_tol=1e-12)

    audit = run_causal_replay_audit(bars, check_steps=6)
    assert audit["passed"] is True
    assert audit["mismatches_count"] == 0

    # Historical analogue matcher must never reference candidate windows whose horizon overlaps current bar
    ana = match_historical_analogues(bars, horizon_bars=15, patch_len=20, top_k=5)
    assert ana["available"] is True
    day_to_idx = {b["day"]: idx for idx, b in enumerate(bars)}
    for m in ana["matches"]:
        cand_idx = day_to_idx[m["end_day"]]
        assert cand_idx + 15 <= len(bars) - 1 - 10


def test_triple_barrier_t_plus_1_and_same_bar_conservative_sl() -> None:
    bars = [
        {"day": "2026-06-01", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0, "complete": True},
        # Entry day (idx=1): even if high hits 108 (above TP=106) and low hits 92 (below SL=95), CN T+1 forbids exit on entry day!
        {"day": "2026-06-02", "open": 100.0, "high": 109.0, "low": 91.0, "close": 101.0, "volume": 1200.0, "complete": True},
        # Day idx=2: hits both TP(106) and SL(95) on the same bar -> must resolve conservatively to SL_BARRIER_SAME_BAR_CONSERVATIVE
        {"day": "2026-06-03", "open": 100.0, "high": 108.0, "low": 94.0, "close": 102.0, "volume": 1100.0, "complete": True},
    ]
    res_cn = label_triple_barrier_event(
        bars,
        entry_idx=1,
        tp_price=106.0,
        sl_price=95.0,
        horizon_bars=5,
        market="CN",
        asset_type="equity",
        symbol="600519.SH",
    )
    assert res_cn["exit_idx"] == 2
    assert res_cn["outcome"] == "SL_BARRIER_SAME_BAR_CONSERVATIVE"
    assert res_cn["barrier_label"] == -1


@pytest.mark.parametrize(
    ("market", "symbol", "asset_type"),
    [
        ("CN", "000001.SH", "index"),
        ("CN", "600519.SH", "equity"),
        ("HK", "HSI.HK", "index"),
        ("HK", "00700.HK", "equity"),
        ("US", "SPY.US", "etf"),
        ("US", "NVDA.US", "equity"),
    ],
)
def test_multi_market_pipeline_and_backtest(
    tmp_path: Path,
    market: str,
    symbol: str,
    asset_type: str,
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
    report = run_pipeline(
        req, output_dir=tmp_path / market, allow_network=False, run_backtest=True, allow_synthetic=True, backtest_step=5
    )
    paths = report["rendered_paths"]
    assert Path(paths["result_json"]).exists()
    assert Path(paths["preview_png"]).exists()
    assert Path(paths["dashboard_html"]).exists()
    assert Path(paths["backtest_json"]).exists()

    html_text = Path(paths["dashboard_html"]).read_text(encoding="utf-8")
    assert "https://cdn" not in html_text
    assert "Time-Travel Replay" in html_text


def test_html_xss_escaping(tmp_path: Path) -> None:
    bundle = load_bundle(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        allow_network=False,
        allow_synthetic_fallback=True,
    )
    bundle["target"]["instrument"]["name"] = "</script><script>window.__XSS=1;</script>"
    report = analyze_bundle(bundle, {"symbol": "600519.SH", "display_bars": 120, "horizon_bars": 15}, include_replay=False)
    out_html = render_html(report, tmp_path / "xss_test.html")
    content = out_html.read_text(encoding="utf-8")
    assert "</script><script>window.__XSS=1;" not in content
    assert "\\u003c/script\\u003e" in content


# ---------------------------------------------------------------------------
# P0 数据真实性回归测试（A-01 ~ A-06）
# ---------------------------------------------------------------------------
import subprocess
from datetime import datetime, timezone

from PIL import Image

import data_gateway
from contracts import assess_data_quality
from market_rules import is_daily_bar_complete, yahoo_symbol_for
from render_structure_chart import render as render_png

UTC = timezone.utc


def _fake_yahoo(end_day: str, market: str, vix_close: float | None = None):
    def _fetch(yahoo_symbol: str, range_str: str = "2y") -> list[dict]:
        if yahoo_symbol == "^VIX":
            if vix_close is None:
                raise OSError("vix down")
            return [{"day": "2026-10-14", "open": 22.0, "high": 24.0, "low": 21.5, "close": vix_close, "volume": 0.0, "complete": True}]
        return generate_deterministic_series(yahoo_symbol, market=market, num_bars=300, end_day=end_day)

    return _fetch


def test_a01_unregistered_symbols_map_to_real_vendor_codes() -> None:
    assert resolve_instrument("601318.SH")["sina"] == "sh601318"
    assert resolve_instrument("300059.SZ")["sina"] == "sz300059"
    assert resolve_instrument("830799.BJ")["sina"] == "bj830799"
    assert yahoo_symbol_for("01810.HK") == "1810.HK"
    assert yahoo_symbol_for("00700.HK") == "0700.HK"
    assert yahoo_symbol_for("HSI.HK") == "^HSI"
    assert yahoo_symbol_for("NVDA.US") == "NVDA"


def test_a01_unregistered_a_share_is_fetched_live(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_sina(code: str, datalen: int = 450) -> list[dict]:
        calls.append(code)
        return generate_deterministic_series(code, market="CN", num_bars=300, end_day="2026-10-14")

    monkeypatch.setattr(data_gateway, "fetch_sina_daily_bars", fake_sina)
    bundle = load_bundle(
        {"symbol": "601318.SH", "market": "CN", "asset_type": "equity"},
        now=datetime(2026, 10, 15, 12, 0, tzinfo=UTC),
    )
    assert calls == ["sh601318", "sh000300"]
    assert bundle["provenance"]["source"] == "SINA_LIVE_DAILY"
    assert bundle["benchmark"]["source"] == "SINA_LIVE_DAILY"


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
        [sys.executable, str(SCRIPTS_DIR / "analyze_and_forecast.py"), "--market", "CN", "--symbol", "600519.SH",
         "--asset-type", "equity", "--offline", "--output-dir", str(tmp_path / "cli")],
        capture_output=True, text=True, timeout=120,
    )
    assert cli.returncode != 0
    assert "DATA_UNAVAILABLE" in cli.stderr


def test_a03_png_and_html_flag_non_live_data(tmp_path: Path) -> None:
    report = run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path,
        allow_network=False,
        allow_synthetic=True,
    )
    assert report["legacy_summary"]["data_source"] == "DETERMINISTIC_SYNTHETIC_FIXTURE"
    assert report["legacy_summary"]["data_warning"]
    html_text = Path(report["rendered_paths"]["dashboard_html"]).read_text(encoding="utf-8")
    static_part = html_text.split('<script id="report-data"')[0]
    assert 'data-quality-status="UNAVAILABLE"' in static_part
    assert "合成数据" in static_part

    # PNG：有告警时主图顶部为红色横幅，无告警时同一位置为深色背景
    bars = generate_deterministic_series("600519.SH", market="CN", num_bars=450)
    clean = render_png(bars, None, "t", tmp_path / "clean.png", show=120)
    warned = render_png(bars, None, "t", tmp_path / "warned.png", show=120, data_source="X", data_warning="合成数据")
    assert clean["data_warning"] is None and warned["data_warning"] == "合成数据"
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


def test_a05_vix_is_fetched_or_null_never_hardcoded(monkeypatch: pytest.MonkeyPatch) -> None:
    req = {"symbol": "NVDA.US", "market": "US", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    offline = load_bundle(req, allow_network=False, allow_synthetic_fallback=True)
    assert offline["extra_context"]["vix"] is None

    after_close_ny = datetime(2026, 10, 15, 23, 0, tzinfo=UTC)
    monkeypatch.setattr(data_gateway, "fetch_yahoo_daily_bars", _fake_yahoo("2026-10-14", "US", vix_close=None))
    no_vix = load_bundle(req, now=after_close_ny)
    assert no_vix["extra_context"]["vix"] is None
    assert no_vix["extra_context"]["vix_source"].startswith("UNAVAILABLE")

    monkeypatch.setattr(data_gateway, "fetch_yahoo_daily_bars", _fake_yahoo("2026-10-14", "US", vix_close=23.4))
    live = load_bundle(req, now=after_close_ny)
    assert live["extra_context"]["vix"] == 23.4
    report = analyze_bundle(live, req, include_replay=False)
    scaling = report["future_projection_15d"]["volatility_scaling"]
    assert scaling["vix"] == 23.4 and "^VIX" in scaling["vix_source"]
    assert scaling["vol_scale_multiplier"] > 1.0


def test_a06_incomplete_last_bar_is_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    hk_morning = datetime(2026, 10, 16, 3, 12, tzinfo=UTC)  # 11:12 HKT
    assert not is_daily_bar_complete("2026-10-16", "HK", hk_morning)
    assert is_daily_bar_complete("2026-10-15", "HK", hk_morning)
    assert is_daily_bar_complete("2026-10-16", "HK", datetime(2026, 10, 16, 8, 30, tzinfo=UTC))
    assert not is_daily_bar_complete("2026-10-15", "US", datetime(2026, 10, 15, 18, 0, tzinfo=UTC))
    assert not is_daily_bar_complete("2026-10-16", "CN", datetime(2026, 10, 16, 3, 0, tzinfo=UTC))

    monkeypatch.setattr(data_gateway, "fetch_yahoo_daily_bars", _fake_yahoo("2026-10-16", "HK"))
    req = {"symbol": "00700.HK", "market": "HK", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    bundle = load_bundle(req, now=hk_morning)
    assert bundle["target"]["bars"][-1]["day"] == "2026-10-15"
    assert bundle["target"]["intraday"]["day"] == "2026-10-16"
    assert bundle["benchmark"]["bars"][-1]["day"] == "2026-10-15"
    report = analyze_bundle(bundle, req, include_replay=False)
    assert report["meta"]["as_of"] == "2026-10-15"
    assert report["series_export"]["intraday"]["day"] == "2026-10-16"
    assert report["quality"]["status"] == "OK"
    assert any("2026-10-16" in note for note in report["quality"]["notes"])


# ---------------------------------------------------------------------------
# P1 制度规则回归测试（R-01 ~ R-06）
# ---------------------------------------------------------------------------
from market_rules import (
    CALENDAR_COVERAGE_END,
    calendar_covers,
    get_market_microstructure_rules,
    is_trading_day,
    round_trip_cost_rate,
)


def test_p1_st_price_limits_by_board_and_date() -> None:
    # 主板 ST：2026-07-06 起 ±5% -> ±10%
    assert get_daily_price_limit_pct("CN", "equity", "600000.SH", is_st=True, day_str="2026-07-03") == 0.05
    assert get_daily_price_limit_pct("CN", "equity", "600000.SH", is_st=True, day_str="2026-07-06") == 0.10
    assert get_daily_price_limit_pct("CN", "equity", "000002.SZ", is_st=True, day_str="2026-10-15") == 0.10
    # 双创 / 北交所 ST 与同板块普通股一致
    assert get_daily_price_limit_pct("CN", "equity", "300001.SZ", is_st=True, day_str="2026-10-15") == 0.20
    assert get_daily_price_limit_pct("CN", "equity", "300001.SZ", is_st=True, day_str="2020-01-15") == 0.05
    assert get_daily_price_limit_pct("CN", "equity", "688001.SH", is_st=True, day_str="2026-10-15") == 0.20
    assert get_daily_price_limit_pct("CN", "equity", "830799.BJ", is_st=True, day_str="2026-10-15") == 0.30


def test_p1_hk_spread_table_full_bands_and_reductions() -> None:
    # 500 以上各档（原实现一律 0.20）
    assert round_to_tick(612.30, "HK", "equity", "2026-10-15") == 612.5
    assert round_to_tick(1234.3, "HK", "equity", "2026-10-15") == 1234.0
    assert round_to_tick(3001.0, "HK", "equity", "2026-10-15") == 3002.0
    assert round_to_tick(6003.0, "HK", "equity", "2026-10-15") == 6005.0
    # Phase 1（2025-08-04）：10-20 档 0.02->0.01，20-50 档 0.05->0.02；50-100 档不变
    assert round_to_tick(15.01, "HK", "equity", "2025-08-01") == 15.02
    assert round_to_tick(15.01, "HK", "equity", "2025-08-04") == 15.01
    assert round_to_tick(27.03, "HK", "equity", "2025-08-01") == 27.05
    assert round_to_tick(27.03, "HK", "equity", "2025-08-04") == 27.04
    assert round_to_tick(77.03, "HK", "equity", "2026-10-15") == 77.05
    # Phase 2（2026-08-03）：0.5-10 档 0.01->0.005
    assert round_to_tick(5.003, "HK", "equity", "2026-07-31") == 5.0
    assert round_to_tick(5.003, "HK", "equity", "2026-08-03") == 5.005
    # ETF 不适用下调
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
    from datetime import date

    assert not is_trading_day(date(2025, 6, 2), "CN")  # 端午
    assert not is_trading_day(date(2026, 2, 23), "CN")  # 春节休市至 2-23
    assert not is_trading_day(date(2025, 1, 9), "US")  # 卡特国葬日
    assert not is_trading_day(date(2027, 1, 1), "US")
    assert not is_trading_day(date(2027, 12, 24), "US")
    assert not is_trading_day(date(2027, 2, 9), "HK")
    assert not is_trading_day(date(2027, 12, 27), "HK")
    assert next_trading_dates("2026-02-13", 1, market="CN") == ["2026-02-24"]
    assert CALENDAR_COVERAGE_END["CN"] == "2026-12-31"
    assert calendar_covers("2026-12-31", "CN") and not calendar_covers("2027-01-04", "CN")
    assert calendar_covers("2027-06-01", "US")

    bars = generate_deterministic_series("600519.SH", market="CN", num_bars=300, end_day="2026-12-22")
    meta = {"market": "CN", "asset_type": "equity", "symbol": "600519.SH"}
    fc = compute_conformal_forecast(bars, meta=meta)
    assert fc["calendar_coverage"]["covered"] is False
    assert fc["calendar_coverage"]["estimated_dates"][0] >= "2027-01-01"


def test_p1_us_pdt_rule_updated() -> None:
    us = get_market_microstructure_rules("US", "equity", "NVDA.US")
    assert us["settlement"] == "T+1_SEC_SETTLEMENT"
    assert "PDT" in us["day_trading_rule"] and "2026-06-04" in us["day_trading_rule"]


def test_p1_asset_type_inference_by_exchange_and_code() -> None:
    assert resolve_instrument("000002.SZ")["asset_type"] == "equity"  # 万科A
    assert resolve_instrument("000858.SZ")["asset_type"] == "equity"  # 五粮液
    assert resolve_instrument("002594.SZ")["asset_type"] == "equity"
    assert resolve_instrument("000905.SH")["asset_type"] == "index"
    assert resolve_instrument("399300.SZ")["asset_type"] == "index"
    etf = resolve_instrument("510300.SH")
    assert etf["asset_type"] == "etf" and etf["lot_size"] == 100
    assert resolve_instrument("159915.SZ")["asset_type"] == "etf"
    assert resolve_instrument("000002.SZ")["lot_size"] == 100
    etf_rules = get_market_microstructure_rules("CN", "etf", "510300.SH")
    assert etf_rules["settlement"] == "T+1" and etf_rules["lot_size"] == 100 and etf_rules["stamp_duty_sell_bps"] == 0.0


# ---------------------------------------------------------------------------
# P1 计算类回归测试（C-01 ~ C-06）
# ---------------------------------------------------------------------------
import random

from evaluation import run_plan_backtest


def _synthetic_bundle(symbol: str = "600519.SH", market: str = "CN", asset_type: str = "equity") -> dict:
    return load_bundle(
        {"symbol": symbol, "market": market, "asset_type": asset_type, "display_bars": 120, "horizon_bars": 15},
        allow_network=False,
        allow_synthetic_fallback=True,
    )


def test_c01_ema200_is_warmed_up_and_never_substituted() -> None:
    req = {"symbol": "600519.SH", "display_bars": 120, "horizon_bars": 15}
    report = analyze_bundle(_synthetic_bundle(), req, include_replay=False)
    trend = report["mathematical_indicators"]["trend"]
    assert trend["ema200"] is not None and trend["sma250"] is not None
    formula = next(s["formula_verified"] for s in report["strategy_signals"] if s["strategy_id"] == "supertrend_ema_trend")
    assert f"EMA200({trend['ema200']:.2f})" in formula

    short = _synthetic_bundle()
    short["target"]["bars"] = short["target"]["bars"][-150:]
    short["benchmark"]["bars"] = short["benchmark"]["bars"][-150:]
    rep_short = analyze_bundle(short, req, include_replay=False)
    assert rep_short["mathematical_indicators"]["trend"]["ema200"] is None
    formula_short = next(s["formula_verified"] for s in rep_short["strategy_signals"] if s["strategy_id"] == "supertrend_ema_trend")
    assert "EMA200(N/A" in formula_short
    assert rep_short["market_regime"]["evidence"]["close_vs_ema200_pct"] is None


def _random_walk_bars(n: int = 760, sigma: float = 0.012, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    days = generate_deterministic_series("RW.US", market="US", num_bars=n)
    price, bars = 100.0, []
    for d in days:
        prev = price
        price = prev * math.exp(rng.gauss(0.0, sigma))
        hi, lo = max(prev, price) * 1.004, min(prev, price) * 0.996
        bars.append({"day": d["day"], "open": round(prev, 4), "high": round(hi, 4), "low": round(lo, 4), "close": round(price, 4), "volume": 1e6, "complete": True})
    return bars


def test_c02_c03_conformal_bands_are_calibrated_and_q50_has_no_drift() -> None:
    bars = _random_walk_bars()
    meta = {"market": "US", "asset_type": "equity", "symbol": "RW.US"}
    fc = compute_conformal_forecast(bars, meta=meta, horizon_bars=15)
    audit = fc["calibration_audit"]
    assert audit["calibrated"] is True and audit["evaluation_samples"] >= 60
    assert abs(audit["nominal_80pct_empirical_coverage"] - 0.80) <= 0.15
    assert abs(audit["nominal_50pct_empirical_coverage"] - 0.50) <= 0.15
    assert len(audit["per_horizon_coverage"]) == 15
    # 展示的区间就是审计的那套构造：用报告里的乘数与 σ 可以复原终点区间
    last_c = bars[-1]["close"]
    sigma = fc["volatility_scaling"]["sigma_1d"]
    m50, m80 = audit["q50_residual_multiplier"], audit["q80_residual_multiplier"]
    assert fc["optimistic_q75"][-1] == pytest.approx(last_c * (1 + m50 * sigma * math.sqrt(15)), abs=0.02)
    assert fc["q10"][-1] == pytest.approx(last_c * (1 - m80 * sigma * math.sqrt(15)), abs=0.02)
    # C-03：中位数恒为最新收盘价，不再受规则分漂移影响
    assert set(fc["base_median_q50"]) == {round(last_c, 2)}
    assert fc["q50_method"] == "MARTINGALE_LAST_CLOSE"


def test_c04_c05_backtest_replays_live_plan_without_learned_params() -> None:
    bundle = _synthetic_bundle()
    req = {"symbol": "600519.SH", "display_bars": 120, "horizon_bars": 15}
    bt = run_plan_backtest(bundle, req, horizon_bars=15, step=3)
    assert bt["method"] == "LIVE_PLAN_REPLAY_V6"
    assert "meta_weights_learned" not in bt and "primary_strategy_oos" not in bt
    assert sum(bt["actions_seen"].values()) == bt["decision_window"]["decisions"]
    trades = bt["sample_trades"]
    assert trades, "合成样本上应至少产生一笔可核对的计划成交"
    for tr in trades:
        # 每笔成交都能在信号日用同一流水线复现出同一份计划
        t = next(i for i, b in enumerate(bundle["target"]["bars"]) if b["day"] == tr["signal_day"])
        sub = {**bundle, "target": {**bundle["target"], "bars": bundle["target"]["bars"][: t + 1]},
               "benchmark": {**bundle["benchmark"], "bars": [b for b in bundle["benchmark"]["bars"] if b["day"] <= tr["signal_day"]]},
               "extra_context": {"vix": None, "earnings_date": None}}
        plan = analyze_bundle(sub, {**req, "account_equity": None}, include_replay=False)["triple_barrier_plan"]
        assert plan["stop_loss_barrier"] == tr["plan_stop_loss"]
        assert plan["take_profit_1"] == tr["plan_take_profit_1"]
        assert tr["plan_entry_zone"][0] <= tr["entry_price"] <= tr["plan_entry_zone"][1]
    for prev, nxt in zip(trades, trades[1:]):
        assert nxt["entry_idx"] > prev["exit_idx"]
    if bt["all_long_signals"]["trades"] < 30:
        assert bt["sample_size_warning"]


def test_c06_v5_pipeline_is_days_invariant(tmp_path: Path) -> None:
    keys = (
        "meta", "market_benchmark", "mathematical_indicators", "algebraic_patterns_detected", "smc_structures",
        "volume_profile", "market_regime", "strategy_signals", "multi_timeframe_score", "meta_labeling_decision",
        "triple_barrier_plan", "rule_scenarios", "future_projection_15d", "debate_and_veto", "replay_snapshots",
    )
    sigs = {}
    for days in (60, 120, 300, 500):
        rep = run_pipeline(
            {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": days, "structure_bars": 120, "horizon_bars": 15},
            output_dir=tmp_path / str(days),
            allow_network=False,
            allow_synthetic=True,
        )
        sigs[days] = {k: rep[k] for k in keys}
        sigs[days]["report_hash"] = rep["reproducibility"]["report_hash"]
        assert len(rep["series_export"]["bars"]) == days
    assert sigs[60] == sigs[120] == sigs[300] == sigs[500]


def test_c06_fetch_length_does_not_depend_on_days(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    def fake_sina(code: str, datalen: int = 450) -> list[dict]:
        seen.append(datalen)
        return generate_deterministic_series(code, market="CN", num_bars=800, end_day="2026-10-14")

    monkeypatch.setattr(data_gateway, "fetch_sina_daily_bars", fake_sina)
    for days in (60, 500):
        load_bundle({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": days},
                    now=datetime(2026, 10, 15, 12, 0, tzinfo=UTC))
    assert len(set(seen)) == 1 and seen[0] == data_gateway.FETCH_BARS


# ---------------------------------------------------------------------------
# P1 渲染与数据源回归测试（G-01、G-02、R-07、R-08）
# ---------------------------------------------------------------------------
import ast

import render_structure_chart
from contracts import classify_data_source
from structure import compute_structure_layer


def _count_color(path: Path, rgb: tuple[int, int, int]) -> int:
    import numpy as np

    arr = np.asarray(Image.open(path).convert("RGB"))
    return int((arr == np.array(rgb, dtype=arr.dtype)).all(axis=-1).sum())


def test_g01_png_follows_market_color_scheme(tmp_path: Path) -> None:
    red, green = render_structure_chart.UP, render_structure_chart.DOWN
    bars = generate_deterministic_series("SPY.US", market="US", num_bars=450)
    cn = render_png(bars, None, "t", tmp_path / "cn.png", show=120, color_scheme="cn_red_up")
    us = render_png(bars, None, "t", tmp_path / "us.png", show=120, color_scheme="us_green_up")
    assert cn["color_scheme"] == "cn_red_up" and us["color_scheme"] == "us_green_up"
    cn_red, cn_green = _count_color(tmp_path / "cn.png", red), _count_color(tmp_path / "cn.png", green)
    us_red, us_green = _count_color(tmp_path / "us.png", red), _count_color(tmp_path / "us.png", green)
    assert cn_red > 0 and cn_green > 0
    # 同一组K线：A股配色下的红色（阳线等）在美股配色下变为绿色，反之亦然
    # （允许少量非K线元素带来的差异）
    assert abs(cn_red - us_green) <= cn_red * 0.10
    assert abs(cn_green - us_red) <= cn_green * 0.10

    report = run_pipeline(
        {"symbol": "NVDA.US", "market": "US", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path / "us_run",
        allow_network=False,
        allow_synthetic=True,
    )
    assert report["meta"]["color_scheme"] == "us_green_up"
    assert report["legacy_summary"]["color_scheme"] == "us_green_up"


def test_g02_structure_is_computed_once_outside_the_renderer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
        allow_network=False,
        allow_synthetic=True,
    )
    # JSON 与 PNG 来自同一份结构结果
    assert report["rule_scenarios"] == report["legacy_summary"]["projections"]
    assert report["mathematical_indicators"]["structure_levels"]["key_levels"] == report["legacy_summary"]["key_levels"]


def test_g02_v41_render_still_matches_shared_layer(tmp_path: Path) -> None:
    bars = generate_deterministic_series("000300.SH", market="CN", num_bars=450)
    layer = compute_structure_layer(bars, None, 120)
    direct = render_png(bars, None, "t", Path("/dev/null"), show=120)
    shared = render_png(bars, None, "t", Path("/dev/null"), show=120, structure=layer)
    assert direct["projections"] == shared["projections"] == layer["projections"]
    assert "_upper" in layer["wedge"]  # 渲染器不得修改共享的结构结果


def _cn_raw_bars() -> list[dict]:
    return generate_deterministic_series("RAW.SH", market="CN", num_bars=300, end_day="2026-10-14")


def test_r07_point_in_time_adjustment_from_corporate_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _cn_raw_bars()
    split_day, div_day = raw[200]["day"], raw[250]["day"]
    monkeypatch.setattr(data_gateway, "fetch_sina_daily_bars", lambda code, datalen=450: [dict(b) for b in raw])
    monkeypatch.setattr(
        data_gateway,
        "fetch_yahoo_corporate_actions",
        lambda sym, range_str="5y": [
            {"ex_date": split_day, "split_ratio": 2.0, "dividend": 0.0},
            {"ex_date": div_day, "split_ratio": 1.0, "dividend": 1.0},
        ],
    )
    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity"}
    bundle = load_bundle(req, now=now)
    status = bundle["provenance"]["adjustment_status"]
    assert status["applied"] == "total_return" and status["events_used"] == 2 and "600519.SS" in status["source"]
    bars = bundle["target"]["bars"]
    dfac = (raw[249]["close"] - 1.0) / raw[249]["close"]
    assert bars[100]["close"] == pytest.approx(raw[100]["close"] / 2.0 * dfac, rel=1e-4)
    assert bars[220]["close"] == pytest.approx(raw[220]["close"] * dfac, rel=1e-4)
    assert bars[280]["close"] == pytest.approx(raw[280]["close"], rel=1e-6)
    assert bars[100]["raw_close"] == raw[100]["close"]
    report = analyze_bundle(bundle, {**req, "display_bars": 120, "horizon_bars": 15}, include_replay=False)
    assert report["meta"]["adjustment"] == "total_return"

    split_only = load_bundle({**req, "adjustment": "split"}, now=now)
    assert split_only["target"]["bars"][220]["close"] == pytest.approx(raw[220]["close"], rel=1e-6)
    assert split_only["target"]["bars"][100]["close"] == pytest.approx(raw[100]["close"] / 2.0, rel=1e-4)


def test_r07_missing_events_are_disclosed_not_faked(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _cn_raw_bars()
    monkeypatch.setattr(data_gateway, "fetch_sina_daily_bars", lambda code, datalen=450: [dict(b) for b in raw])
    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    bundle = load_bundle(req, now=datetime(2026, 10, 15, 12, 0, tzinfo=UTC))  # 事件接口被测试夹具断网
    status = bundle["provenance"]["adjustment_status"]
    assert status["requested"] == "total_return" and status["applied"] == "raw" and status["error"]
    report = analyze_bundle(bundle, req, include_replay=False)
    assert report["meta"]["adjustment"] == "raw"
    assert any("复权未生效" in n for n in report["quality"]["notes"])


def test_r07_yahoo_prices_skip_split_events() -> None:
    raw = generate_deterministic_series("NVDA.US", market="US", num_bars=300, end_day="2026-10-14")
    events = [{"ex_date": raw[200]["day"], "split_ratio": 10.0, "dividend": 0.01}]
    actions = data_gateway.build_corporate_actions(raw, events, include_splits=False)
    assert actions[0]["split_ratio"] == 1.0 and actions[0]["dividend_factor"] < 1.0


def test_r08_hstech_uses_real_index_not_etf_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_tencent(code: str, datalen: int = 760) -> list[dict]:
        calls.append(code)
        return generate_deterministic_series(code, market="HK", num_bars=300, end_day="2026-10-15", base_price=4100.0)

    monkeypatch.setattr(data_gateway, "fetch_tencent_index_daily_bars", fake_tencent)
    monkeypatch.setattr(data_gateway, "fetch_yahoo_daily_bars", _fake_yahoo("2026-10-15", "HK"))
    bundle = load_bundle(
        {"symbol": "HSTECH.HK", "market": "HK", "asset_type": "index"},
        now=datetime(2026, 10, 16, 9, 0, tzinfo=UTC),
    )
    assert calls == ["hkHSTECH"]
    assert bundle["provenance"]["source"] == "TENCENT_INDEX_DAILY(hkHSTECH)"
    assert classify_data_source(bundle["provenance"]["source"]) == "LIVE"
    assert bundle["benchmark"]["source"] == "YAHOO_DAILY(^HSI)"


# ---------------------------------------------------------------------------
# P2：analysis.schema.json 是报告契约的唯一来源
# ---------------------------------------------------------------------------
import copy

import contracts


def test_p2_schema_file_drives_report_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    report = analyze_bundle(
        load_bundle({"symbol": "600519.SH", "market": "CN", "asset_type": "equity"}, allow_network=False, allow_synthetic_fallback=True),
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
    # 修改 schema 文件内容即改变校验行为：证明代码里没有第二份硬编码清单
    stricter = copy.deepcopy(schema)
    stricter["required"] = [*schema["required"], "brand_new_section"]
    monkeypatch.setattr(contracts, "load_report_schema", lambda: stricter)
    with pytest.raises(ValueError, match="brand_new_section"):
        validate_report(report)
