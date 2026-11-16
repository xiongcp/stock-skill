"""大盘结构与周期层：基准的结构摘要、周线月线阶段、个股与大盘的对齐关系。

全部为描述性纯计算，只用已收盘K线与已确认拐点。结果写入内部报告的 market_layer，
不参与规则分、评级、否决与计划价位（规格 2026-11-15 第 3 节）。公式见 references/math-patterns.md。
"""
from __future__ import annotations

import math
from typing import Any

from strategies import aggregate_bars_to_weekly
from structure import HISTORY_BARS, MIN_RAIL_SPAN, TREND_FLAT_PCT

MA_WEEKS = 30
SLOPE_WEEKS = 4
RANGE_WEEKS = 60
MIN_WEEKS = 35
MA_MONTHS = 10
MIN_MONTHS = 11
PHASE_CN = {"ADVANCE": "上升阶段", "DECLINE": "下降阶段", "TOPPING": "高位震荡", "BASING": "低位震荡"}
ALIGN_DAYS = 120
ALIGN_MIN_DAYS = 60
RS_BAND = 0.02
ALIGN_WINDOW_BARS = 5
_COMPARE_CN = {
    "HIGHER_LOW_WHILE_INDEX_LOWER_LOW": "大盘新低、个股未新低",
    "LOWER_LOW_WITH_INDEX": "与大盘同步新低",
    "LOWER_LOW_WHILE_INDEX_HIGHER_LOW": "大盘未新低、个股新低",
    "HIGHER_LOW_WITH_INDEX": "与大盘同步抬高低点",
    "LOWER_HIGH_WHILE_INDEX_HIGHER_HIGH": "大盘新高、个股未新高",
    "HIGHER_HIGH_WITH_INDEX": "与大盘同步新高",
    "HIGHER_HIGH_WHILE_INDEX_LOWER_HIGH": "大盘未新高、个股新高",
    "LOWER_HIGH_WITH_INDEX": "与大盘同步压低高点",
}


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


def _trend(rs: list[float], n: int) -> str | None:
    if len(rs) <= n:
        return None
    ratio = rs[-1] / rs[-1 - n]
    return "UP" if ratio >= 1.0 + RS_BAND else ("DOWN" if ratio <= 1.0 - RS_BAND else "FLAT")


def _confirmed(layer: dict[str, Any], kind: str, pos: dict[str, int]) -> list[dict[str, Any]]:
    return [p for p in layer["pivots"] if p.get("confirmed") and p["kind"] == kind and p["day"] in pos]


def _lead_lag(t_layer: dict[str, Any], b_layer: dict[str, Any], kind: str, pos: dict[str, int]) -> int | None:
    b_piv = _confirmed(b_layer, kind, pos)
    if not b_piv:
        return None
    b_pos = pos[b_piv[-1]["day"]]
    near = [pos[p["day"]] - b_pos for p in _confirmed(t_layer, kind, pos) if abs(pos[p["day"]] - b_pos) <= MIN_RAIL_SPAN]
    return min(near, key=lambda d: (abs(d), d)) if near else None


def _extreme_compare(
    b_layer: dict[str, Any], kind: str, days: list[str], pos: dict[str, int], t_ext: dict[str, float]
) -> dict[str, Any] | None:
    """基准最近两个已确认低点（或高点）处，标的在前后 ALIGN_WINDOW_BARS 根窗口内的极值对照。"""
    piv = _confirmed(b_layer, kind, pos)
    if len(piv) < 2:
        return None
    p1, p2 = piv[-2], piv[-1]
    low = kind == "L"
    pick = min if low else max

    def window_ext(day: str) -> float:
        i = pos[day]
        return pick(t_ext[d] for d in days[max(0, i - ALIGN_WINDOW_BARS) : i + ALIGN_WINDOW_BARS + 1])

    m1, m2 = window_ext(p1["day"]), window_ext(p2["day"])
    extended = float(p2["price"]) < float(p1["price"]) if low else float(p2["price"]) > float(p1["price"])
    if low:
        label = ("HIGHER_LOW_WHILE_INDEX_LOWER_LOW" if m2 > m1 else "LOWER_LOW_WITH_INDEX") if extended else (
            "LOWER_LOW_WHILE_INDEX_HIGHER_LOW" if m2 < m1 else "HIGHER_LOW_WITH_INDEX")
    else:
        label = ("LOWER_HIGH_WHILE_INDEX_HIGHER_HIGH" if m2 < m1 else "HIGHER_HIGH_WITH_INDEX") if extended else (
            "HIGHER_HIGH_WHILE_INDEX_LOWER_HIGH" if m2 > m1 else "LOWER_HIGH_WITH_INDEX")
    return {
        "benchmark_days": [p1["day"], p2["day"]],
        "benchmark_extended": extended,
        "target_change_pct": round((m2 / m1 - 1.0) * 100.0, 2),
        "label": label,
        "label_cn": _COMPARE_CN[label],
        "window_complete": pos[p2["day"]] + ALIGN_WINDOW_BARS <= len(days) - 1,
    }


def compute_alignment(
    target_layer: dict[str, Any],
    bench_layer: dict[str, Any],
    target_bars: list[dict[str, Any]],
    bench_bars: list[dict[str, Any]],
    target_decimals: int = 2,
    bench_decimals: int = 2,
) -> dict[str, Any]:
    """个股与大盘的对齐关系：联动程度、相对强弱线、拐点先后、新低新高对照、阶段对照。"""
    b_close = {b["day"]: float(b["close"]) for b in bench_bars if float(b["close"]) > 0}
    common = [b for b in target_bars if b["day"] in b_close and float(b["close"]) > 0]
    days = [b["day"] for b in common]
    pos = {d: i for i, d in enumerate(days)}
    tail = common[-ALIGN_DAYS:]
    rs = [float(b["close"]) / b_close[b["day"]] for b in tail]

    beta = corr = None
    if len(tail) >= ALIGN_MIN_DAYS:
        rt = [math.log(float(tail[i]["close"]) / float(tail[i - 1]["close"])) for i in range(1, len(tail))]
        rb = [math.log(b_close[tail[i]["day"]] / b_close[tail[i - 1]["day"]]) for i in range(1, len(tail))]
        mt, mb = sum(rt) / len(rt), sum(rb) / len(rb)
        cov = sum((x - mt) * (y - mb) for x, y in zip(rt, rb)) / len(rt)
        vt = sum((x - mt) ** 2 for x in rt) / len(rt)
        vb = sum((y - mb) ** 2 for y in rb) / len(rb)
        if vb > 0 and vt > 0:
            beta, corr = round(cov / vb, 3), round(cov / math.sqrt(vt * vb), 3)
    has60 = len(rs) >= ALIGN_MIN_DAYS
    return {
        "common_day_count": len(tail),
        "beta_120": beta,
        "corr_120": corr,
        "rs_trend_20": _trend(rs, 20),
        "rs_trend_60": _trend(rs, 60) if has60 else None,
        "rs_at_60d_high": bool(rs[-1] >= max(rs[-60:])) if has60 else None,
        "rs_at_60d_low": bool(rs[-1] <= min(rs[-60:])) if has60 else None,
        "low_lead_lag_bars": _lead_lag(target_layer, bench_layer, "L", pos),
        "high_lead_lag_bars": _lead_lag(target_layer, bench_layer, "H", pos),
        "low_compare": _extreme_compare(bench_layer, "L", days, pos, {b["day"]: float(b["low"]) for b in common}),
        "high_compare": _extreme_compare(bench_layer, "H", days, pos, {b["day"]: float(b["high"]) for b in common}),
        "target_phase": classify_cycle_phase(target_bars, target_decimals)["phase"],
        "benchmark_phase": classify_cycle_phase(bench_bars, bench_decimals)["phase"],
    }
