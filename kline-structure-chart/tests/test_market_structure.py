"""大盘结构与周期层：周期阶段、对齐关系、编排、契约与呈现（只用录制的真实行情）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from analyze_and_forecast import run_pipeline  # noqa: E402
from conftest import load_recorded  # noqa: E402

CSI300 = "sina_daily/sh000300.json"
MOUTAI = "sina_daily/sh600519.json"


def _completed_weeks(daily: list[dict]) -> list[dict]:
    from strategies import aggregate_bars_to_weekly

    return [w for w in aggregate_bars_to_weekly(daily[-420:]) if w["complete"]][1:]


def _phase_of_weeks(weeks: list[dict]) -> dict | None:
    """不调用被测模块，用 numpy 按规格第 3.3 节对一串已完成周线重算阶段。"""
    if len(weeks) < 35:
        return None
    c = np.array([w["close"] for w in weeks], dtype=float)
    ma_now, ma_prev = c[-30:].mean(), c[-34:-4].mean()
    slope = ma_now / ma_prev - 1.0
    win = weeks[-60:]
    hi, lo = max(w["high"] for w in win), min(w["low"] for w in win)
    pos = (c[-1] - lo) / (hi - lo)
    if slope > 0.005 and c[-1] > ma_now:
        phase = "ADVANCE"
    elif slope < -0.005 and c[-1] < ma_now:
        phase = "DECLINE"
    else:
        phase = "TOPPING" if pos >= 0.5 else "BASING"
    return {"phase": phase, "slope": slope, "pos": pos, "ma": ma_now, "n": len(win)}


def _independent_phase(daily: list[dict]) -> dict | None:
    return _phase_of_weeks(_completed_weeks(daily))


def test_cycle_phase_matches_independent_computation() -> None:
    from market_structure import classify_cycle_phase

    bars = load_recorded(CSI300)
    seen: set[str] = set()
    for cut in range(430, len(bars) + 1, 20):
        got = classify_cycle_phase(bars[:cut])
        exp = _independent_phase(bars[:cut])
        assert exp is not None
        assert got["phase"] == exp["phase"], bars[cut - 1]["day"]
        assert got["slope_4w_pct"] == pytest.approx(exp["slope"] * 100.0, abs=0.0051)
        assert got["pos_60w_pct"] == pytest.approx(exp["pos"] * 100.0, abs=0.051)
        assert got["ma30w_price"] == pytest.approx(exp["ma"], abs=0.006)
        assert got["range_week_count"] == exp["n"]
        assert got["phase_weeks"] >= 1 and got["reason"] is None
        seen.add(got["phase"])
    # 录制区间内没有出现的阶段在这里如实列出，不为凑覆盖而构造行情
    print("phases covered:", sorted(seen))
    assert len(seen) >= 2


def test_cycle_phase_weeks_counts_consecutive_weeks() -> None:
    from market_structure import classify_cycle_phase

    bars = load_recorded(CSI300)
    got = classify_cycle_phase(bars)
    weeks = _completed_weeks(bars)
    # 从最新一周往回数：阶段相同的连续周数；数到不足 35 周仍相同，则 capped
    run, k = 0, len(weeks)
    while k >= 35 and _phase_of_weeks(weeks[:k])["phase"] == got["phase"]:
        run, k = run + 1, k - 1
    assert got["phase_weeks"] == run
    assert got["phase_weeks_capped"] == (k < 35)


def test_cycle_phase_insufficient_history_is_null_with_reason() -> None:
    from market_structure import classify_cycle_phase

    bars = load_recorded(CSI300)
    got = classify_cycle_phase(bars[:150])  # 约 30 周，少于 35
    assert got["phase"] is None and got["ma30w_price"] is None and got["phase_weeks"] is None
    assert "35" in got["reason"]


def test_monthly_aggregation_only_finished_months() -> None:
    from market_structure import aggregate_bars_to_monthly

    bars = load_recorded(CSI300)[-300:]
    months = aggregate_bars_to_monthly(bars)
    keys = [m["day"][:7] for m in months]
    assert keys == sorted(set(keys))
    assert bars[-1]["day"][:7] not in keys  # 最后一个自然月无法确认已结束，不计入
    assert bars[0]["day"][:7] not in keys   # 第一个月可能不完整，丢弃
    last_key = keys[-1]
    in_month = [b for b in bars if b["day"][:7] == last_key]
    assert months[-1]["close"] == float(in_month[-1]["close"])
    assert months[-1]["high"] == max(float(b["high"]) for b in in_month)


def _layers(cut: int | None = None):
    from structure import STRUCT_DAYS, compute_structure_layer

    t = load_recorded(MOUTAI)
    b = load_recorded(CSI300)
    if cut is not None:
        t = t[:cut]
    b = [x for x in b if x["day"] <= t[-1]["day"]]
    return t, b, compute_structure_layer(t, None, STRUCT_DAYS), compute_structure_layer(b, None, STRUCT_DAYS)


def test_alignment_beta_corr_match_numpy() -> None:
    from market_structure import compute_alignment

    t, b, tl, bl = _layers()
    got = compute_alignment(tl, bl, t, b)
    bmap = {x["day"]: float(x["close"]) for x in b}
    pairs = [(float(x["close"]), bmap[x["day"]]) for x in t if x["day"] in bmap][-120:]
    rt = np.diff(np.log([p[0] for p in pairs]))
    rb = np.diff(np.log([p[1] for p in pairs]))
    assert got["common_day_count"] == 120
    assert got["beta_120"] == pytest.approx(np.cov(rt, rb, ddof=0)[0, 1] / rb.var(), abs=5e-4)
    assert got["corr_120"] == pytest.approx(np.corrcoef(rt, rb)[0, 1], abs=5e-4)
    rs = np.array([p[0] / p[1] for p in pairs])
    exp20 = "UP" if rs[-1] / rs[-21] >= 1.02 else ("DOWN" if rs[-1] / rs[-21] <= 0.98 else "FLAT")
    assert got["rs_trend_20"] == exp20
    assert got["rs_at_60d_high"] == bool(rs[-1] >= rs[-60:].max())
    assert got["target_phase"] in ("ADVANCE", "DECLINE", "TOPPING", "BASING")
    assert got["benchmark_phase"] in ("ADVANCE", "DECLINE", "TOPPING", "BASING")


def _expected_low_compare(t: list[dict], b: list[dict], bl: dict) -> dict | None:
    days = [x["day"] for x in t if x["day"] in {y["day"] for y in b}]
    pos = {d: i for i, d in enumerate(days)}
    tlow = {x["day"]: float(x["low"]) for x in t}
    lows = [p for p in bl["pivots"] if p["confirmed"] and p["kind"] == "L" and p["day"] in pos]
    if len(lows) < 2:
        return None
    l1, l2 = lows[-2], lows[-1]

    def wmin(day: str) -> float:
        i = pos[day]
        return min(tlow[d] for d in days[max(0, i - 5) : i + 6])

    m1, m2 = wmin(l1["day"]), wmin(l2["day"])
    ext = float(l2["price"]) < float(l1["price"])
    if ext:
        label = "HIGHER_LOW_WHILE_INDEX_LOWER_LOW" if m2 > m1 else "LOWER_LOW_WITH_INDEX"
    else:
        label = "LOWER_LOW_WHILE_INDEX_HIGHER_LOW" if m2 < m1 else "HIGHER_LOW_WITH_INDEX"
    return {"label": label, "days": [l1["day"], l2["day"]], "complete": pos[l2["day"]] + 5 <= len(days) - 1,
            "chg": round((m2 / m1 - 1.0) * 100.0, 2)}


def test_alignment_low_compare_matches_independent_on_many_cuts() -> None:
    from market_structure import compute_alignment

    n = len(load_recorded(MOUTAI))
    checked = incomplete = 0
    for cut in range(n - 400, n + 1, 10):
        t, b, tl, bl = _layers(cut)
        got = compute_alignment(tl, bl, t, b)["low_compare"]
        exp = _expected_low_compare(t, b, bl)
        if exp is None:
            assert got is None
            continue
        checked += 1
        assert got["label"] == exp["label"] and got["benchmark_days"] == exp["days"]
        assert got["window_complete"] == exp["complete"]
        assert got["target_change_pct"] == exp["chg"]
        incomplete += 0 if exp["complete"] else 1
    assert checked >= 20
    print("low_compare cuts:", checked, "window_incomplete:", incomplete)


def test_alignment_window_incomplete() -> None:
    """标的最近几天没有K线（例如停牌）：基准最近一个低点之后的共同交易日不足 5 个，标签必须标为未稳定。"""
    from market_structure import compute_alignment

    t, b, tl, bl = _layers()
    lows = [p for p in bl["pivots"] if p["confirmed"] and p["kind"] == "L"]
    assert len(lows) >= 2
    cut_day = next(x["day"] for i, x in enumerate(b) if i >= 2 and b[i - 2]["day"] == lows[-1]["day"])  # 低点后第 2 个交易日
    got = compute_alignment(tl, bl, [x for x in t if x["day"] <= cut_day], b)["low_compare"]
    assert got["benchmark_days"][1] == lows[-1]["day"] and got["window_complete"] is False
    full = compute_alignment(tl, bl, t, b)["low_compare"]
    assert full["window_complete"] is (sum(1 for x in t if x["day"] > lows[-1]["day"]) >= 5)


def test_alignment_lead_lag_sign_and_window() -> None:
    from market_structure import compute_alignment

    t, b, tl, bl = _layers()
    got = compute_alignment(tl, bl, t, b)
    for key in ("low_lead_lag_bars", "high_lead_lag_bars"):
        assert got[key] is None or (isinstance(got[key], int) and abs(got[key]) <= 10)


def test_alignment_short_overlap_gives_nulls() -> None:
    from market_structure import compute_alignment

    t, b, tl, bl = _layers()
    got = compute_alignment(tl, bl, t, b[-40:])  # 基准只有 40 根：共同交易日不足 60
    assert got["common_day_count"] == 40
    assert got["beta_120"] is None and got["corr_120"] is None
    assert got["rs_trend_60"] is None and got["rs_at_60d_high"] is None
    assert got["rs_trend_20"] in ("UP", "DOWN", "FLAT")


REQ = {"display_bars": 120, "structure_bars": 120, "horizon_bars": 15}
FIVE = [("CN", "600519.SH", "equity"), ("CN", "000001.SH", "index"), ("HK", "00700.HK", "equity"),
        ("US", "NVDA.US", "equity"), ("US", "SPY.US", "etf")]


def test_market_layer_present_for_stock(replay_recorded, tmp_path: Path) -> None:
    rep = run_pipeline({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ}, output_dir=tmp_path)
    ml = rep["market_layer"]
    assert ml["cycle"]["basis"] == "BENCHMARK" and ml["cycle"]["phase"] in ("ADVANCE", "DECLINE", "TOPPING", "BASING")
    s = ml["structure"]
    assert set(s) == {"last_close", "wedge", "ladder", "scenario", "wave_counts", "patterns", "projections"}
    assert not any(k.startswith("_") for k in s["wedge"])
    assert all(p["category"] == "geometric" for p in s["patterns"])
    assert ml["alignment"]["common_day_count"] == 120
    assert ml["benchmark_price_decimals"] == 2


def test_market_layer_self_benchmark(replay_recorded, tmp_path: Path) -> None:
    rep = run_pipeline({"symbol": "SPY.US", "market": "US", "asset_type": "etf", **REQ}, output_dir=tmp_path)
    ml = rep["market_layer"]
    assert ml["structure"] is None and ml["alignment"] is None
    assert ml["cycle"]["basis"] == "SELF" and ml["benchmark_price_decimals"] is None


def test_market_layer_none_without_benchmark() -> None:
    from market_structure import compute_market_layer
    from structure import STRUCT_DAYS, compute_structure_layer

    bars = load_recorded(MOUTAI)
    bundle = {"market": "CN", "benchmark": None,
              "target": {"instrument": {"symbol": "600519.SH", "market": "CN", "asset_type": "equity"}, "bars": bars}}
    assert compute_market_layer(bundle, compute_structure_layer(bars, None, STRUCT_DAYS), 15) is None


@pytest.mark.slow
@pytest.mark.parametrize("market,symbol,asset_type", FIVE)
def test_decisions_do_not_depend_on_market_layer(replay_recorded, market, symbol, asset_type) -> None:
    """每 5 个交易日取一个截面：开与关大盘层，除 market_layer 外内部报告逐项相同。"""
    import data_gateway
    from analysis import analyze_bundle

    req = {"symbol": symbol, "market": market, "asset_type": asset_type, **REQ}
    full = data_gateway.load_bundle(req)
    bars, bench = full["target"]["bars"], full.get("benchmark")
    checked = 0
    # 截面的构造方式与 evaluation.run_plan_backtest 相同：标的截到第 t 根，基准截到同一天
    for t in range(len(bars) - 250, len(bars), 5):
        cut_day = bars[t]["day"]
        bundle = {
            **full,
            "target": {**full["target"], "bars": bars[: t + 1], "intraday": None},
            "benchmark": {**bench, "bars": [b for b in bench["bars"] if b["day"] <= cut_day]} if bench else None,
        }
        on = analyze_bundle(bundle, req, include_replay=False, include_forecast=False, include_market_layer=True)
        off = analyze_bundle(bundle, req, include_replay=False, include_forecast=False, include_market_layer=False)
        assert off["market_layer"] is None
        on.pop("market_layer"), off.pop("market_layer")
        assert on == off, f"{symbol} {cut_day}"
        checked += 1
    assert checked == 50


def test_market_layer_ignores_benchmark_bars_after_cut(replay_recorded) -> None:
    import data_gateway
    from evaluation import audit_market_layer_causality
    from market_structure import classify_cycle_phase

    bundle = data_gateway.load_bundle({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ})
    assert audit_market_layer_causality(bundle) == []
    # 对照：同一段K线被乘以 3 后，周期阶段的输出一定不同；所以上面的相等不是空转
    b = bundle["benchmark"]["bars"]
    dirty = b[:-30] + [{**x, **{k: float(x[k]) * 3.0 for k in ("open", "high", "low", "close")}} for x in b[-30:]]
    assert classify_cycle_phase(dirty)["weekly_close"] != classify_cycle_phase(b)["weekly_close"]


@pytest.mark.slow
def test_causal_audit_reports_market_layer(replay_recorded) -> None:
    import data_gateway
    from evaluation import run_causal_replay_audit

    bundle = data_gateway.load_bundle({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ})
    res = run_causal_replay_audit(bundle["target"]["bars"], bundle=bundle)
    assert "market_layer" in res["modules_audited"] and res["modules_skipped"] == [] and res["passed"]
    res2 = run_causal_replay_audit(bundle["target"]["bars"])
    assert res2["modules_skipped"] == ["market_layer"] and "market_layer" not in res2["modules_audited"]


def _result(rep: dict) -> dict:
    return json.loads(Path(rep["rendered_paths"]["result_json"]).read_text(encoding="utf-8"))


def test_contract_projects_market_layer(replay_recorded, tmp_path: Path) -> None:
    from contracts import validate_report

    rep = run_pipeline({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ}, output_dir=tmp_path)
    res = _result(rep)
    validate_report(res)
    mc = res["market_context"]
    assert set(mc["structure"]) == {"last_close", "channel", "key_levels", "abc_scenario", "wave_counts",
                                    "patterns", "scenarios"}
    assert set(mc["structure"]["scenarios"]) == {"breakout_level", "breakdown_level", "active", "impulse_wave"}
    for item in mc["structure"]["scenarios"]["active"]:
        assert not {"waypoints_offset", "step_labels", "eta_bars", "target_within_horizon"} & set(item)
    assert mc["cycle"]["basis"] == "BENCHMARK" and mc["alignment"]["common_day_count"] == 120
    qb = res["quotable_prices"]["benchmark"]
    assert qb["price_decimals"] == 2 and mc["cycle"]["ma30w_price"] in qb["prices"]
    assert mc["structure"]["last_close"] in qb["prices"]
    assert mc["cycle"]["range_week_count"] not in qb["prices"]  # 周数不是价格
    size = Path(rep["rendered_paths"]["result_json"]).stat().st_size
    assert size <= 80 * 1024, size


def test_quotable_self_basis(replay_recorded, tmp_path: Path) -> None:
    rep = run_pipeline({"symbol": "SPY.US", "market": "US", "asset_type": "etf", **REQ}, output_dir=tmp_path)
    res = _result(rep)
    mc = res["market_context"]
    assert mc["structure"] is None and mc["alignment"] is None and mc["cycle"]["basis"] == "SELF"
    assert res["quotable_prices"]["benchmark"] is None
    assert mc["cycle"]["ma30w_price"] in res["quotable_prices"]["prices"]


def test_market_context_nulls_without_benchmark() -> None:
    import contract_v6

    assert contract_v6._market_layer_fields(None) == {"structure": None, "cycle": None, "alignment": None}


def test_lint_benchmark_price_needs_index_context(replay_recorded, tmp_path: Path) -> None:
    from lint_response import lint_agent_response

    rep = run_pipeline({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ}, output_dir=tmp_path)
    res = _result(rep)
    px = res["market_context"]["cycle"]["ma30w_price"]
    assert px not in res["quotable_prices"]["prices"]
    tail = "\n\n以上内容不构成投资建议。"
    ok = lint_agent_response(f"大盘周线均线附近的支撑位 {px}。{tail}", res)
    bad = lint_agent_response(f"该股下方支撑位 {px}。{tail}", res)
    assert not [e for e in ok if "白名单" in e]
    assert [e for e in bad if "白名单" in e]


def test_report_md_has_market_layer_and_passes_lint(replay_recorded, tmp_path: Path) -> None:
    from lint_response import lint_agent_response

    rep = run_pipeline({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ}, output_dir=tmp_path)
    res = _result(rep)
    md = Path(rep["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    cyc, ali = res["market_context"]["cycle"], res["market_context"]["alignment"]
    assert "### 大盘结构" in md and "### 周期阶段" in md and "### 个股与大盘" in md
    assert cyc["phase_cn"] in md and f"{cyc['phase_weeks']}" in md
    if ali["low_compare"]:
        assert ali["low_compare"]["label_cn"] in md
        assert ("未稳定" in md) == (not ali["low_compare"]["window_complete"] or
                                    bool(ali["high_compare"] and not ali["high_compare"]["window_complete"]))
    assert "不构成交易信号，也不改变风控结论" in md
    assert lint_agent_response(md, res) == []


def test_report_md_market_layer_absent_cases(replay_recorded, tmp_path: Path) -> None:
    from render_report_md import _market_layer_lines

    assert _market_layer_lines({"structure": None, "cycle": None, "alignment": None}, 2) == []
    rep = run_pipeline({"symbol": "SPY.US", "market": "US", "asset_type": "etf", **REQ}, output_dir=tmp_path)
    md = Path(rep["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    assert "### 周期阶段" in md and "### 大盘结构" not in md and "### 个股与大盘" not in md


def test_dashboard_embeds_market_layer_rows(replay_recorded, tmp_path: Path) -> None:
    rep = run_pipeline({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ}, output_dir=tmp_path)
    html = Path(rep["rendered_paths"]["dashboard_html"]).read_text(encoding="utf-8")
    for label in ("大盘周线阶段", "大盘日线通道", "大盘低点对照"):
        assert label in html
