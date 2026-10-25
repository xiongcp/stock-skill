from __future__ import annotations

import math
from typing import Any


def causal_nadaraya_watson(values: list[float], bandwidth: float = 3.0, window: int = 25) -> list[float]:
    """单边因果 Nadaraya-Watson 核回归平滑（仅使用 t 时刻及之前的历史观测，严格消除前视偏差）。"""
    out: list[float] = []
    h2 = max(1e-6, 2.0 * (bandwidth ** 2))
    for t in range(len(values)):
        start = max(0, t - window + 1)
        num = 0.0
        den = 0.0
        for j in range(start, t + 1):
            dist = float(t - j)
            w = math.exp(-(dist * dist) / h2)
            num += w * float(values[j])
            den += w
        out.append(num / den if den > 0 else float(values[t]))
    return out


def causal_swing_pivots(bars: list[dict[str, Any]], reversal_thr: float, price_decimals: int = 2) -> list[dict[str, Any]]:
    """无前视因果 ZigZag 极值检测器：记录极值发生时刻 (idx, day) 与真正被反向走势确认的时刻 (confirmed_at_idx, confirmed_at_day)。"""
    if not bars:
        return []

    def _make_pivot(
        idx: int,
        kind: str,
        confirmed: bool,
        confirmed_idx: int | None,
    ) -> dict[str, Any]:
        p = float(bars[idx]["high"]) if kind == "H" else float(bars[idx]["low"])
        return {
            "idx": idx,
            "day": bars[idx]["day"],
            "price": round(p, price_decimals),
            "kind": kind,
            "confirmed": confirmed,
            "confirmed_at_idx": confirmed_idx,
            "confirmed_at_day": bars[confirmed_idx]["day"] if confirmed_idx is not None else None,
        }

    pivots: list[dict[str, Any]] = []
    hi_i = lo_i = ext = 0
    trend = 0
    for i in range(1, len(bars)):
        high = float(bars[i]["high"])
        low = float(bars[i]["low"])
        if trend == 0:
            if high > float(bars[hi_i]["high"]):
                hi_i = i
            if low < float(bars[lo_i]["low"]):
                lo_i = i
            if float(bars[hi_i]["high"]) >= float(bars[lo_i]["low"]) * (1.0 + reversal_thr):
                if lo_i < hi_i:
                    pivots.append(_make_pivot(lo_i, "L", True, i))
                    trend, ext = 1, hi_i
                else:
                    pivots.append(_make_pivot(hi_i, "H", True, i))
                    trend, ext = -1, lo_i
        elif trend == 1:
            if high > float(bars[ext]["high"]):
                ext = i
            elif low <= float(bars[ext]["high"]) * (1.0 - reversal_thr):
                pivots.append(_make_pivot(ext, "H", True, i))
                trend, ext = -1, i
        else:
            if low < float(bars[ext]["low"]):
                ext = i
            elif high >= float(bars[ext]["low"]) * (1.0 + reversal_thr):
                pivots.append(_make_pivot(ext, "L", True, i))
                trend, ext = 1, i
    if trend != 0:
        pivots.append(_make_pivot(ext, "H" if trend == 1 else "L", False, None))
    return pivots


def detect_candlestick_patterns(
    bars: list[dict[str, Any]],
    atr_vals: list[float | None],
    rvol_vals: list[float | None],
    lookback: int = 10,
    max_items: int | None = 6,
) -> list[dict[str, Any]]:
    """100% 代数不等式蜡烛形态检测（附 ATR 归一化与成交量/分位确认公式证据）。"""
    n = len(bars)
    if n < 5:
        return []
    detected: list[dict[str, Any]] = []
    start_idx = max(2, n - lookback)

    for t in range(start_idx, n):
        b = bars[t]
        p1 = bars[t - 1]
        p2 = bars[t - 2]
        o, h, l, c = float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])
        o1, h1, l1, c1 = float(p1["open"]), float(p1["high"]), float(p1["low"]), float(p1["close"])
        o2, h2, l2, c2 = float(p2["open"]), float(p2["high"]), float(p2["low"]), float(p2["close"])

        rng = max(1e-9, h - l)
        body = abs(c - o)
        lower_wick = min(o, c) - l
        upper_wick = h - max(o, c)
        ref_atr = atr_vals[t - 1] if (t - 1 >= 0 and t - 1 < len(atr_vals) and atr_vals[t - 1] is not None) else (atr_vals[t] if t < len(atr_vals) else None)
        atr = float(ref_atr) if ref_atr is not None else rng
        atr = max(1e-6, atr)
        rv = float(rvol_vals[t]) if (t < len(rvol_vals) and rvol_vals[t] is not None) else 1.0

        win20 = bars[max(0, t - 19) : t + 1]
        lo20 = min(float(x["low"]) for x in win20)
        hi20 = max(float(x["high"]) for x in win20)
        low_rank = 0.5 if hi20 <= lo20 else (min(o, c) - lo20) / (hi20 - lo20)
        high_rank = 0.5 if hi20 <= lo20 else (max(o, c) - lo20) / (hi20 - lo20)
        # 若下影线创出或逼近近20日新低，同样满足波段低位条件
        at_swing_low = (l <= min(float(x["low"]) for x in win20[:-1]) * 1.005) if len(win20) > 1 else True
        at_swing_high = (h >= max(float(x["high"]) for x in win20[:-1]) * 0.995) if len(win20) > 1 else True

        # 1. 看涨锤头线 (Bullish Hammer)
        if (
            lower_wick >= 2.0 * max(body, 0.05 * rng)
            and upper_wick <= 0.18 * rng
            and rng >= 0.75 * atr
            and (low_rank <= 0.35 or at_swing_low)
        ):
            detected.append(
                {
                    "pattern": "CDL_BULLISH_HAMMER",
                    "name_cn": "看涨锤头线",
                    "category": "candlestick",
                    "direction": "BULLISH",
                    "bar_idx": t,
                    "observed_at": b["day"],
                    "confirmed_at": b["day"],
                    "volume_confirmed": rv >= 1.15,
                    "formula_verified": (
                        f"LowerWick({lower_wick:.2f}) >= 2.0*Body({body:.2f}) "
                        f"AND UpperWick/Range({upper_wick/rng:.2f}) <= 0.18 "
                        f"AND Range({rng:.2f}) >= 0.75*ATR({atr:.2f}) "
                        f"AND LowRank20({low_rank:.2f})"
                    ),
                }
            )

        # 2. 看跌流星线 (Shooting Star)
        if (
            upper_wick >= 2.0 * max(body, 0.05 * rng)
            and lower_wick <= 0.18 * rng
            and rng >= 0.75 * atr
            and (high_rank >= 0.65 or at_swing_high)
        ):
            detected.append(
                {
                    "pattern": "CDL_BEARISH_SHOOTING_STAR",
                    "name_cn": "看跌流星线",
                    "category": "candlestick",
                    "direction": "BEARISH",
                    "bar_idx": t,
                    "observed_at": b["day"],
                    "confirmed_at": b["day"],
                    "volume_confirmed": rv >= 1.15,
                    "formula_verified": (
                        f"UpperWick({upper_wick:.2f}) >= 2.0*Body({body:.2f}) "
                        f"AND LowerWick/Range({lower_wick/rng:.2f}) <= 0.18 "
                        f"AND Range({rng:.2f}) >= 0.75*ATR({atr:.2f}) "
                        f"AND HighRank20({high_rank:.2f})"
                    ),
                }
            )

        # 3. 看涨吞没 (Bullish Engulfing)
        body1 = abs(c1 - o1)
        if (
            c1 < o1
            and c > o
            and c >= o1
            and o <= c1
            and body >= 1.08 * max(body1, 1e-6)
            and body >= 0.55 * atr
        ):
            detected.append(
                {
                    "pattern": "CDL_BULLISH_ENGULFING",
                    "name_cn": "看涨吞没形态",
                    "category": "candlestick",
                    "direction": "BULLISH",
                    "bar_idx": t,
                    "observed_at": b["day"],
                    "confirmed_at": b["day"],
                    "volume_confirmed": rv >= 1.2,
                    "formula_verified": (
                        f"PrevBear(C1={c1:.2f}<O1={o1:.2f}) AND BullBody({body:.2f}) >= 1.08*PrevBody({body1:.2f}) "
                        f"AND C({c:.2f}) >= O1({o1:.2f}) AND RVOL({rv:.2f})"
                    ),
                }
            )

        # 4. 看跌吞没 (Bearish Engulfing)
        if (
            c1 > o1
            and c < o
            and o >= c1
            and c <= o1
            and body >= 1.08 * max(body1, 1e-6)
            and body >= 0.55 * atr
        ):
            detected.append(
                {
                    "pattern": "CDL_BEARISH_ENGULFING",
                    "name_cn": "看跌吞没形态",
                    "category": "candlestick",
                    "direction": "BEARISH",
                    "bar_idx": t,
                    "observed_at": b["day"],
                    "confirmed_at": b["day"],
                    "volume_confirmed": rv >= 1.2,
                    "formula_verified": (
                        f"PrevBull(C1={c1:.2f}>O1={o1:.2f}) AND BearBody({body:.2f}) >= 1.08*PrevBody({body1:.2f}) "
                        f"AND C({c:.2f}) <= O1({o1:.2f}) AND RVOL({rv:.2f})"
                    ),
                }
            )

        # 5. 早晨之星 (Morning Star)
        body2 = abs(c2 - o2)
        if (
            c2 < o2
            and body2 >= 0.6 * atr
            and body1 <= 0.35 * body2
            and c > o
            and c >= (o2 + c2) / 2.0
        ):
            detected.append(
                {
                    "pattern": "CDL_MORNING_STAR",
                    "name_cn": "早晨之星",
                    "category": "candlestick",
                    "direction": "BULLISH",
                    "bar_idx": t,
                    "observed_at": b["day"],
                    "confirmed_at": b["day"],
                    "volume_confirmed": rv >= 1.15,
                    "formula_verified": (
                        f"Bar[-2]Bear({body2:.2f}>=0.6*ATR) AND StarBody({body1:.2f}<=0.35*Body[-2]) "
                        f"AND Close({c:.2f}) >= Mid[-2]({(o2+c2)/2:.2f})"
                    ),
                }
            )

        # 6. 黄昏之星 (Evening Star)
        if (
            c2 > o2
            and body2 >= 0.6 * atr
            and body1 <= 0.35 * body2
            and c < o
            and c <= (o2 + c2) / 2.0
        ):
            detected.append(
                {
                    "pattern": "CDL_EVENING_STAR",
                    "name_cn": "黄昏之星",
                    "category": "candlestick",
                    "direction": "BEARISH",
                    "bar_idx": t,
                    "observed_at": b["day"],
                    "confirmed_at": b["day"],
                    "volume_confirmed": rv >= 1.15,
                    "formula_verified": (
                        f"Bar[-2]Bull({body2:.2f}>=0.6*ATR) AND StarBody({body1:.2f}<=0.35*Body[-2]) "
                        f"AND Close({c:.2f}) <= Mid[-2]({(o2+c2)/2:.2f})"
                    ),
                }
            )

        # 7. 破底翻 / 流动性扫损收回 (Bullish Liquidity Sweep / Stop-Hunt Reclaim)
        prior_bars = win20[:-1]
        if prior_bars:
            ref_swing_low = min(float(x["low"]) for x in prior_bars)
            sweep_depth = ref_swing_low - l
            close_in_rng = (c - l) / rng
            lower_wick_r = lower_wick / rng
            if (
                0.0 < sweep_depth <= 0.65 * atr
                and c > ref_swing_low
                and c > o
                and lower_wick_r >= 0.42
                and close_in_rng >= 0.58
            ):
                detected.append(
                    {
                        "pattern": "LIQUIDITY_SWEEP_BULLISH",
                        "name_cn": "破底翻扫损收回",
                        "category": "candlestick",
                        "direction": "BULLISH",
                        "bar_idx": t,
                        "observed_at": b["day"],
                        "confirmed_at": b["day"],
                        "volume_confirmed": rv >= 1.10,
                        "formula_verified": (
                            f"0 < PriorLow20({ref_swing_low:.2f})-Low({l:.2f})={sweep_depth:.2f} <= 0.65*ATR({atr:.2f}) "
                            f"AND Close({c:.2f}) > PriorLow20 AND Close > Open({o:.2f}) "
                            f"AND LowerWickRatio({lower_wick_r:.2f}) >= 0.42 (RVOL={rv:.2f})"
                        ),
                    }
                )

    return detected[-max_items:] if max_items is not None else detected


def detect_geometric_patterns(
    bars: list[dict[str, Any]],
    pivots: list[dict[str, Any]],
    atr_vals: list[float | None],
    rvol_vals: list[float | None],
    price_decimals: int = 2,
) -> list[dict[str, Any]]:
    """基于因果 ZigZag 已确认波段拐点检测双底 / 双顶，并对核平滑收盘价做线性回归判断趋势通道（仅此两类波段形态）。"""
    n = len(bars)
    if n < 25:
        return []
    out: list[dict[str, Any]] = []
    atr = float(atr_vals[-1]) if (atr_vals and atr_vals[-1] is not None) else max(1e-6, float(bars[-1]["close"]) * 0.02)
    last_c = float(bars[-1]["close"])
    last_day = bars[-1]["day"]
    rv = float(rvol_vals[-1]) if (rvol_vals and rvol_vals[-1] is not None) else 1.0

    confirmed_pivots = [p for p in pivots if p.get("confirmed")]
    lows = [p for p in confirmed_pivots if p["kind"] == "L"]
    highs = [p for p in confirmed_pivots if p["kind"] == "H"]

    # 1. 波段双底 (Double Bottom / W底)：取最近两个已确认低点
    if len(lows) >= 2:
        l1, l2 = lows[-2], lows[-1]
        span = int(l2["idx"]) - int(l1["idx"])
        mid_highs = [h for h in highs if int(l1["idx"]) < int(h["idx"]) < int(l2["idx"])]
        if mid_highs and 6 <= span <= 85:
            neck = max(mid_highs, key=lambda x: float(x["price"]))
            diff_lows = abs(float(l1["price"]) - float(l2["price"]))
            neck_height = float(neck["price"]) - max(float(l1["price"]), float(l2["price"]))
            if diff_lows <= 0.65 * atr and neck_height >= 1.2 * atr:
                breakout = last_c > float(neck["price"])
                out.append(
                    {
                        "pattern": "SWING_DOUBLE_BOTTOM_BREAKOUT" if breakout else "SWING_DOUBLE_BOTTOM_FORMING",
                        "name_cn": "波段双底突破" if breakout else "波段双底结构",
                        "category": "geometric",
                        "direction": "BULLISH",
                        "observed_at": l2["day"],
                        "confirmed_at": last_day if breakout else (l2.get("confirmed_at_day") or l2["day"]),
                        "neckline_price": round(float(neck["price"]), price_decimals),
                        "formula_verified": (
                            f"|E2({l1['price']:.2f}) - E4({l2['price']:.2f})| = {diff_lows:.2f} <= 0.65*ATR({atr:.2f}) "
                            f"AND Neckline({neck['price']:.2f}) - Bottom >= 1.2*ATR "
                            f"AND Close({last_c:.2f}) {'>' if breakout else '<='} Neckline (RVOL={rv:.2f})"
                        ),
                    }
                )

    # 2. 波段双顶 (Double Top / M头)：取最近两个已确认高点
    if len(highs) >= 2:
        h1, h2 = highs[-2], highs[-1]
        span = int(h2["idx"]) - int(h1["idx"])
        mid_lows = [l for l in lows if int(h1["idx"]) < int(l["idx"]) < int(h2["idx"])]
        if mid_lows and 6 <= span <= 85:
            neck = min(mid_lows, key=lambda x: float(x["price"]))
            diff_highs = abs(float(h1["price"]) - float(h2["price"]))
            neck_depth = min(float(h1["price"]), float(h2["price"])) - float(neck["price"])
            if diff_highs <= 0.65 * atr and neck_depth >= 1.2 * atr:
                breakdown = last_c < float(neck["price"])
                out.append(
                    {
                        "pattern": "SWING_DOUBLE_TOP_BREAKDOWN" if breakdown else "SWING_DOUBLE_TOP_FORMING",
                        "name_cn": "波段双顶破位" if breakdown else "波段双顶结构",
                        "category": "geometric",
                        "direction": "BEARISH",
                        "observed_at": h2["day"],
                        "confirmed_at": last_day if breakdown else (h2.get("confirmed_at_day") or h2["day"]),
                        "neckline_price": round(float(neck["price"]), price_decimals),
                        "formula_verified": (
                            f"|E1({h1['price']:.2f}) - E3({h2['price']:.2f})| = {diff_highs:.2f} <= 0.65*ATR({atr:.2f}) "
                            f"AND Top - Neckline({neck['price']:.2f}) >= 1.2*ATR "
                            f"AND Close({last_c:.2f}) {'<' if breakdown else '>='} Neckline"
                        ),
                    }
                )

    # 3. 核平滑线性趋势：对因果 NW 平滑后的收盘价做普通最小二乘（平滑会抬高 R²，阈值取 0.72）
    win = min(60, n)
    sub = bars[-win:]
    xs = [float(i) for i in range(win)]
    ys = causal_nadaraya_watson([float(b["close"]) for b in sub], bandwidth=3.0)
    mx = sum(xs) / win
    my = sum(ys) / win
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    syy = sum((y - my) ** 2 for y in ys)
    if sxx > 0 and syy > 0:
        slope = sxy / sxx
        r2 = (sxy * sxy) / (sxx * syy)
        if r2 >= 0.72 and abs(slope) >= 0.04 * atr:
            up_chan = slope > 0
            out.append(
                {
                    "pattern": "KERNEL_SMOOTHED_UPTREND" if up_chan else "KERNEL_SMOOTHED_DOWNTREND",
                    "name_cn": "回归上升趋势通道" if up_chan else "回归下降趋势通道",
                    "category": "geometric",
                    "direction": "BULLISH" if up_chan else "BEARISH",
                    "observed_at": sub[0]["day"],
                    "confirmed_at": last_day,
                    "formula_verified": (
                        f"OLS_Slope_on_NW_Close({slope:+.3f}/bar) {' > 0' if up_chan else ' < 0'} "
                        f"AND R^2({r2:.2f}) >= 0.72 OVER {win} bars"
                    ),
                }
            )

    return out


def detect_divergences(
    bars: list[dict[str, Any]],
    pivots: list[dict[str, Any]],
    rsi_vals: list[float | None],
    atr_vals: list[float | None],
) -> list[dict[str, Any]]:
    """检测最近两个已确认波段极值与 RSI14 的方向背离（顶背离 / 底背离）；只比较 RSI14 一个指标。"""
    out: list[dict[str, Any]] = []
    confirmed = [p for p in pivots if p.get("confirmed")]
    lows = [p for p in confirmed if p["kind"] == "L"]
    highs = [p for p in confirmed if p["kind"] == "H"]
    atr = float(atr_vals[-1]) if (atr_vals and atr_vals[-1] is not None) else max(1e-6, float(bars[-1]["close"]) * 0.015)

    if len(lows) >= 2:
        l1, l2 = lows[-2], lows[-1]
        i1, i2 = int(l1["idx"]), int(l2["idx"])
        if i2 - i1 >= 5 and i2 < len(rsi_vals) and rsi_vals[i1] is not None and rsi_vals[i2] is not None:
            p1, p2 = float(l1["price"]), float(l2["price"])
            r1, r2 = float(rsi_vals[i1]), float(rsi_vals[i2])
            if p2 < p1 - 0.25 * atr and r2 > r1 + 2.5 and r1 < 42.0:
                out.append(
                    {
                        "pattern": "RSI_BULLISH_DIVERGENCE",
                        "name_cn": "RSI 底背离",
                        "category": "divergence",
                        "direction": "BULLISH",
                        "observed_at": l2["day"],
                        "confirmed_at": l2.get("confirmed_at_day") or l2["day"],
                        "points": [
                            {"day": l1["day"], "price": p1, "indicator": round(r1, 2)},
                            {"day": l2["day"], "price": p2, "indicator": round(r2, 2)},
                        ],
                        "formula_verified": (
                            f"Low2({p2:.2f}) < Low1({p1:.2f}) - 0.25*ATR({atr:.2f}) "
                            f"AND RSI2({r2:.1f}) > RSI1({r1:.1f}) + 2.5"
                        ),
                    }
                )

    if len(highs) >= 2:
        h1, h2 = highs[-2], highs[-1]
        i1, i2 = int(h1["idx"]), int(h2["idx"])
        if i2 - i1 >= 5 and i2 < len(rsi_vals) and rsi_vals[i1] is not None and rsi_vals[i2] is not None:
            p1, p2 = float(h1["price"]), float(h2["price"])
            r1, r2 = float(rsi_vals[i1]), float(rsi_vals[i2])
            if p2 > p1 + 0.25 * atr and r2 < r1 - 2.5 and r1 > 58.0:
                out.append(
                    {
                        "pattern": "RSI_BEARISH_DIVERGENCE",
                        "name_cn": "RSI 顶背离",
                        "category": "divergence",
                        "direction": "BEARISH",
                        "observed_at": h2["day"],
                        "confirmed_at": h2.get("confirmed_at_day") or h2["day"],
                        "points": [
                            {"day": h1["day"], "price": p1, "indicator": round(r1, 2)},
                            {"day": h2["day"], "price": p2, "indicator": round(r2, 2)},
                        ],
                        "formula_verified": (
                            f"High2({p2:.2f}) > High1({p1:.2f}) + 0.25*ATR({atr:.2f}) "
                            f"AND RSI2({r2:.1f}) < RSI1({r1:.1f}) - 2.5"
                        ),
                    }
                )

    return out


def detect_smc_structures(
    bars: list[dict[str, Any]],
    pivots: list[dict[str, Any]],
    atr_vals: list[float | None],
    lookback: int = 80,
    price_decimals: int = 2,
) -> dict[str, Any]:
    """算法化 SMC 结构检测：FVG 公允价值缺口、Order Block 订单块与 BOS 结构破位（收盘越过最近已确认波段高/低点；不区分趋势延续与反转）。"""
    n = len(bars)
    sub_start = max(2, n - lookback)
    fvgs: list[dict[str, Any]] = []
    order_blocks: list[dict[str, Any]] = []
    structure_breaks: list[dict[str, Any]] = []

    for t in range(sub_start, n):
        atr = float(atr_vals[t]) if (t < len(atr_vals) and atr_vals[t] is not None) else max(1e-6, float(bars[t]["close"]) * 0.015)
        b0 = bars[t]
        b1 = bars[t - 1]
        b2 = bars[t - 2]
        l0, h0 = float(b0["low"]), float(b0["high"])
        o1, c1 = float(b1["open"]), float(b1["close"])
        l2, h2 = float(b2["low"]), float(b2["high"])
        impulse_body = abs(c1 - o1)

        # Bullish FVG: Low[t] - High[t-2] > 0.2 * ATR and ImpulseBody[t-1] > 1.1 * ATR
        if l0 - h2 > 0.20 * atr and impulse_body >= 1.10 * atr and c1 > o1:
            future_lows = [float(b["low"]) for b in bars[t + 1 :]]
            min_after = min(future_lows) if future_lows else l0
            status = "FILLED" if min_after <= h2 else ("MITIGATED" if min_after < l0 else "OPEN")
            if status != "FILLED":
                fvgs.append(
                    {
                        "kind": "BULLISH_FVG",
                        "day": b1["day"],
                        "idx": t - 1,
                        "zone": [round(h2, price_decimals), round(l0, price_decimals)],
                        "status": status,
                        "formula_verified": f"Low[{b0['day']}]({l0:.2f}) - High[{b2['day']}]({h2:.2f}) > 0.2*ATR({atr:.2f})",
                    }
                )

        # Bearish FVG: Low[t-2] - High[t] > 0.2 * ATR
        if l2 - h0 > 0.20 * atr and impulse_body >= 1.10 * atr and c1 < o1:
            future_highs = [float(b["high"]) for b in bars[t + 1 :]]
            max_after = max(future_highs) if future_highs else h0
            status = "FILLED" if max_after >= l2 else ("MITIGATED" if max_after > h0 else "OPEN")
            if status != "FILLED":
                fvgs.append(
                    {
                        "kind": "BEARISH_FVG",
                        "day": b1["day"],
                        "idx": t - 1,
                        "zone": [round(h0, price_decimals), round(l2, price_decimals)],
                        "status": status,
                        "formula_verified": f"Low[{b2['day']}]({l2:.2f}) - High[{b0['day']}]({h0:.2f}) > 0.2*ATR({atr:.2f})",
                    }
                )

    # BOS and Order Blocks from confirmed pivots
    confirmed = [p for p in pivots if p.get("confirmed") and int(p["idx"]) >= sub_start]
    high_pivs = [p for p in confirmed if p["kind"] == "H"]
    low_pivs = [p for p in confirmed if p["kind"] == "L"]

    if high_pivs:
        last_h = high_pivs[-1]
        h_idx = int(last_h["idx"])
        h_price = float(last_h["price"])
        for k in range(h_idx + 1, n):
            if float(bars[k]["close"]) > h_price:
                structure_breaks.append(
                    {
                        "type": "BULLISH_BOS",
                        "day": bars[k]["day"],
                        "idx": k,
                        "broken_level": round(h_price, price_decimals),
                        "pivot_day": last_h["day"],
                        "formula_verified": f"Close[{bars[k]['day']}]({float(bars[k]['close']):.2f}) > SwingHigh[{last_h['day']}]({h_price:.2f})",
                    }
                )
                # Find last bearish candle before the breakout leg as Bullish Order Block
                bear_candidates = [
                    m for m in range(max(0, h_idx - 10), k) if float(bars[m]["close"]) < float(bars[m]["open"])
                ]
                if bear_candidates:
                    ob_idx = bear_candidates[-1]
                    ob_bar = bars[ob_idx]
                    ob_low, ob_high = float(ob_bar["low"]), float(ob_bar["high"])
                    invalidated = any(float(b["close"]) < ob_low for b in bars[k + 1 :])
                    if not invalidated:
                        order_blocks.append(
                            {
                                "kind": "BULLISH_ORDER_BLOCK",
                                "day": ob_bar["day"],
                                "idx": ob_idx,
                                "zone": [round(ob_low, price_decimals), round(ob_high, price_decimals)],
                                "status": "ACTIVE",
                                "formula_verified": f"LastBearBar[{ob_bar['day']}] before BOS({h_price:.2f}), Close stays >= {ob_low:.2f}",
                            }
                        )
                break

    if low_pivs:
        last_l = low_pivs[-1]
        l_idx = int(last_l["idx"])
        l_price = float(last_l["price"])
        for k in range(l_idx + 1, n):
            if float(bars[k]["close"]) < l_price:
                structure_breaks.append(
                    {
                        "type": "BEARISH_BOS",
                        "day": bars[k]["day"],
                        "idx": k,
                        "broken_level": round(l_price, price_decimals),
                        "pivot_day": last_l["day"],
                        "formula_verified": f"Close[{bars[k]['day']}]({float(bars[k]['close']):.2f}) < SwingLow[{last_l['day']}]({l_price:.2f})",
                    }
                )
                bull_candidates = [
                    m for m in range(max(0, l_idx - 10), k) if float(bars[m]["close"]) > float(bars[m]["open"])
                ]
                if bull_candidates:
                    ob_idx = bull_candidates[-1]
                    ob_bar = bars[ob_idx]
                    ob_low, ob_high = float(ob_bar["low"]), float(ob_bar["high"])
                    invalidated = any(float(b["close"]) > ob_high for b in bars[k + 1 :])
                    if not invalidated:
                        order_blocks.append(
                            {
                                "kind": "BEARISH_ORDER_BLOCK",
                                "day": ob_bar["day"],
                                "idx": ob_idx,
                                "zone": [round(ob_low, price_decimals), round(ob_high, price_decimals)],
                                "status": "ACTIVE",
                                "formula_verified": f"LastBullBar[{ob_bar['day']}] before Breakdown({l_price:.2f}), Close stays <= {ob_high:.2f}",
                            }
                        )
                break

    return {
        "fvgs": fvgs[-4:],
        "order_blocks": order_blocks[-4:],
        "structure_breaks": structure_breaks[-4:],
        "note": "SMC 区域由 OHLCV 代数规则确定性计算，代表算法结构区而非逐笔机构持仓。",
    }


def compute_volume_profile(
    bars: list[dict[str, Any]], lookback: int = 120, num_bins: int = 24, price_decimals: int = 2
) -> dict[str, Any]:
    """基于 OHLCV 价格区间均匀分配近似计算 Volume Profile (POC / VAH / VAL)，明确标注近似方法。"""
    sub = bars[-lookback:] if len(bars) > lookback else bars
    if not sub:
        return {"poc_price": 0.0, "vah_price": 0.0, "val_price": 0.0, "bins": [], "approximation_method": "ohlcv_range_partition_v1"}
    p_min = min(float(b["low"]) for b in sub)
    p_max = max(float(b["high"]) for b in sub)
    if p_max <= p_min:
        p = round(p_min, price_decimals)
        return {"poc_price": p, "vah_price": p, "val_price": p, "bins": [], "approximation_method": "ohlcv_range_partition_v1"}

    step = (p_max - p_min) / num_bins
    vols = [0.0] * num_bins
    for b in sub:
        lo, hi = float(b["low"]), float(b["high"])
        o = float(b.get("open", lo))
        c = float(b.get("close", hi))
        tp = (hi + lo + c) / 3.0
        v = max(1.0, float(b.get("volume") or 1.0))
        b_start = max(0, min(num_bins - 1, int((lo - p_min) / step)))
        b_end = max(0, min(num_bins - 1, int((hi - p_min) / step)))
        span = b_end - b_start + 1
        # 60% 成交量按全振幅分布，25% 集中于实体区间，15% 锚定典型价 (H+L+C)/3，抑制长影线对 POC 的稀释
        for k in range(b_start, b_end + 1):
            vols[k] += (0.60 * v) / span
        body_lo, body_hi = min(o, c), max(o, c)
        body_start = max(0, min(num_bins - 1, int((body_lo - p_min) / step)))
        body_end = max(0, min(num_bins - 1, int((body_hi - p_min) / step)))
        body_span = body_end - body_start + 1
        for k in range(body_start, body_end + 1):
            vols[k] += (0.25 * v) / body_span
        tp_idx = max(0, min(num_bins - 1, int((tp - p_min) / step)))
        vols[tp_idx] += 0.15 * v

    poc_idx = max(range(num_bins), key=lambda k: vols[k])
    poc_price = p_min + (poc_idx + 0.5) * step

    total_v = sum(vols)
    target_v = 0.70 * total_v
    acc_v = vols[poc_idx]
    low_i = high_i = poc_idx
    while acc_v < target_v and (low_i > 0 or high_i < num_bins - 1):
        v_down = vols[low_i - 1] if low_i > 0 else -1.0
        v_up = vols[high_i + 1] if high_i < num_bins - 1 else -1.0
        if v_up >= v_down and high_i < num_bins - 1:
            high_i += 1
            acc_v += vols[high_i]
        elif low_i > 0:
            low_i -= 1
            acc_v += vols[low_i]
        else:
            break

    val_price = p_min + low_i * step
    vah_price = p_min + (high_i + 1) * step
    max_v = max(vols) if any(vols) else 1.0
    bins_out = [
        {
            "price_low": round(p_min + k * step, price_decimals),
            "price_high": round(p_min + (k + 1) * step, price_decimals),
            "price_mid": round(p_min + (k + 0.5) * step, price_decimals),
            "rel_volume": round(vols[k] / max_v, 4),
            "is_poc": k == poc_idx,
            "in_value_area": low_i <= k <= high_i,
        }
        for k in range(num_bins)
    ]
    return {
        "poc_price": round(poc_price, price_decimals),
        "vah_price": round(vah_price, price_decimals),
        "val_price": round(val_price, price_decimals),
        "value_area_coverage": 0.70,
        "lookback_bars": len(sub),
        "approximation_method": "ohlcv_range_partition_v1",
        "note": "由 OHLCV 价格区间近似计算的成交分布，不等于真实持仓筹码成本。",
        "bins": bins_out,
    }
