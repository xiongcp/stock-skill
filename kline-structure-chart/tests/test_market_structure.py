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
