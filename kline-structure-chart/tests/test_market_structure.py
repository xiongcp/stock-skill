"""大盘结构与周期层：周期阶段、对齐关系、编排、契约与呈现（只用录制的真实行情）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

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
