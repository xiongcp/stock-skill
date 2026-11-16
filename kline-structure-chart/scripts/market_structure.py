"""大盘结构与周期层：基准的结构摘要、周线月线阶段、个股与大盘的对齐关系。

全部为描述性纯计算，只用已收盘K线与已确认拐点。结果写入内部报告的 market_layer，
不参与规则分、评级、否决与计划价位（规格 2026-11-15 第 3 节）。公式见 references/math-patterns.md。
"""
from __future__ import annotations

from typing import Any

from strategies import aggregate_bars_to_weekly
from structure import HISTORY_BARS, TREND_FLAT_PCT

MA_WEEKS = 30
SLOPE_WEEKS = 4
RANGE_WEEKS = 60
MIN_WEEKS = 35
MA_MONTHS = 10
MIN_MONTHS = 11
PHASE_CN = {"ADVANCE": "上升阶段", "DECLINE": "下降阶段", "TOPPING": "高位震荡", "BASING": "低位震荡"}


def aggregate_bars_to_monthly(daily_bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """已收盘日线聚合成月线。丢弃第一个月（可能不完整）和最后一个月（无法确认已结束）。"""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for b in daily_bars:
        buckets.setdefault(str(b["day"])[:7], []).append(b)
    keys = sorted(buckets)[1:-1]
    return [
        {
            "day": buckets[k][-1]["day"],
            "open": float(buckets[k][0]["open"]),
            "high": max(float(x["high"]) for x in buckets[k]),
            "low": min(float(x["low"]) for x in buckets[k]),
            "close": float(buckets[k][-1]["close"]),
        }
        for k in keys
    ]


def _phase_at(weeks: list[dict[str, Any]], k: int) -> dict[str, Any] | None:
    """只用 weeks[: k + 1] 判定阶段（无状态）；样本不足返回 None。"""
    if k + 1 < MIN_WEEKS:
        return None
    closes = [float(w["close"]) for w in weeks[: k + 1]]
    ma_now = sum(closes[-MA_WEEKS:]) / MA_WEEKS
    ma_prev = sum(closes[-MA_WEEKS - SLOPE_WEEKS : -SLOPE_WEEKS]) / MA_WEEKS
    slope = ma_now / ma_prev - 1.0
    win = weeks[max(0, k + 1 - RANGE_WEEKS) : k + 1]
    hi = max(float(w["high"]) for w in win)
    lo = min(float(w["low"]) for w in win)
    pos = (closes[-1] - lo) / (hi - lo) if hi > lo else 0.5
    flat = TREND_FLAT_PCT / 100.0
    if slope > flat and closes[-1] > ma_now:
        phase = "ADVANCE"
    elif slope < -flat and closes[-1] < ma_now:
        phase = "DECLINE"
    else:
        phase = "TOPPING" if pos >= 0.5 else "BASING"
    return {"phase": phase, "close": closes[-1], "ma": ma_now, "slope": slope, "pos": pos, "hi": hi, "lo": lo, "n": len(win)}


def classify_cycle_phase(daily_bars: list[dict[str, Any]], decimals: int = 2) -> dict[str, Any]:
    """周线阶段（四类）、已持续周数与月线相对 10 月均线的位置。输入为已收盘日线，只取最近 HISTORY_BARS 根。"""
    hist = daily_bars[-HISTORY_BARS:]
    out: dict[str, Any] = {
        "phase": None, "phase_cn": None, "phase_weeks": None, "phase_weeks_capped": None,
        "weekly_close": None, "ma30w_price": None, "slope_4w_pct": None, "pos_60w_pct": None,
        "range_week_count": None, "high_60w_price": None, "low_60w_price": None,
        "monthly_vs_ma10": None, "ma10m_price": None, "reason": None,
    }
    weeks = [w for w in aggregate_bars_to_weekly(hist) if w.get("complete", True)][1:]
    last = len(weeks) - 1
    cur = _phase_at(weeks, last)
    if cur is None:
        out["reason"] = f"已完成周线 {len(weeks)} 个，少于 {MIN_WEEKS} 个，无法判定周线阶段"
    else:
        run, capped, j = 1, False, last - 1
        while True:
            prev = _phase_at(weeks, j)
            if prev is None:
                capped = True
                break
            if prev["phase"] != cur["phase"]:
                break
            run += 1
            j -= 1
        out.update(
            phase=cur["phase"], phase_cn=PHASE_CN[cur["phase"]], phase_weeks=run, phase_weeks_capped=capped,
            weekly_close=round(cur["close"], decimals), ma30w_price=round(cur["ma"], decimals),
            slope_4w_pct=round(cur["slope"] * 100.0, 2), pos_60w_pct=round(cur["pos"] * 100.0, 1),
            range_week_count=cur["n"], high_60w_price=round(cur["hi"], decimals), low_60w_price=round(cur["lo"], decimals),
        )
    months = aggregate_bars_to_monthly(hist)
    if len(months) >= MIN_MONTHS:
        closes = [m["close"] for m in months]
        ma10 = sum(closes[-MA_MONTHS:]) / MA_MONTHS
        out["monthly_vs_ma10"] = "ABOVE" if closes[-1] > ma10 else "BELOW"
        out["ma10m_price"] = round(ma10, decimals)
    return out
