from __future__ import annotations

from typing import Any


def classify_market_regime(
    bars: list[dict[str, Any]],
    ind_snapshot: dict[str, Any],
    vix_value: float | None = None,
) -> dict[str, Any]:
    """划分五类市场状态：TRENDING_BULL / TRENDING_BEAR / SQUEEZE_CONSOLIDATION / MEAN_REVERTING_RANGE / HIGH_VOLATILITY_CHAOS。"""
    trend = ind_snapshot.get("trend", {})
    vol = ind_snapshot.get("volatility", {})
    qlib = ind_snapshot.get("qlib_factors", {})

    adx14 = float(trend.get("adx14") or 18.0)
    pdi = float(trend.get("plus_di14") or 20.0)
    mdi = float(trend.get("minus_di14") or 20.0)
    bbw_pct = float(vol.get("bbw_percentile_120") or 50.0)
    hv_ratio = float(qlib.get("hv_ratio_20_60") or 1.0)
    atr20_pct = float(vol.get("atr20_pct") or 1.8)
    last_close = float(bars[-1]["close"])
    ema60 = float(trend.get("ema60") or last_close)
    # EMA200 预热不足时为 None：不得用 EMA60 冒充，相应评分项置 0 并在证据中注明
    ema200 = float(trend["ema200"]) if trend.get("ema200") is not None else None

    if (vix_value is not None and vix_value >= 28.0) or hv_ratio >= 1.55 or atr20_pct >= 4.5:
        regime = "HIGH_VOLATILITY_CHAOS"
        label_cn = "高波失序/极端波动"
        active_family = "DEFENSIVE_WAIT"
    elif bbw_pct <= 22.0 and adx14 < 24.0:
        regime = "SQUEEZE_CONSOLIDATION"
        label_cn = "窄幅波动率压缩(Squeeze蓄势)"
        active_family = "VOLATILITY_BREAKOUT"
    elif adx14 >= 23.0 and (last_close >= ema60 and pdi >= mdi):
        regime = "TRENDING_BULL"
        label_cn = "多头趋势扩张"
        active_family = "TREND_FOLLOWING"
    elif adx14 >= 23.0 and (last_close < ema60 and mdi > pdi):
        regime = "TRENDING_BEAR"
        label_cn = "空头趋势承压"
        active_family = "DEFENSIVE_OR_MEAN_REVERSION"
    else:
        regime = "MEAN_REVERTING_RANGE"
        label_cn = "宽幅区间震荡"
        active_family = "MEAN_REVERSION"

    # Regime thermometer score (0-100, >55 bullish environment, <45 bearish/defensive)
    base_score = 50.0
    if last_close > ema60:
        base_score += 12.0
    else:
        base_score -= 12.0
    if ema200 is not None:
        base_score += 10.0 if last_close > ema200 else -10.0
    if pdi > mdi:
        base_score += min(12.0, (pdi - mdi) * 0.6)
    else:
        base_score -= min(12.0, (mdi - pdi) * 0.6)
    if regime == "HIGH_VOLATILITY_CHAOS":
        base_score = min(base_score, 38.0)
    score = round(max(5.0, min(95.0, base_score)), 1)

    return {
        "regime": regime,
        "label_cn": label_cn,
        "regime_score": score,
        "routed_strategy_family": active_family,
        "evidence": {
            "adx14": adx14,
            "bbw_percentile_120": bbw_pct,
            "hv_ratio_20_60": hv_ratio,
            "vix": vix_value,
            "close_vs_ema60_pct": round((last_close / ema60 - 1.0) * 100.0, 2) if ema60 > 0 else 0.0,
            "close_vs_ema200_pct": round((last_close / ema200 - 1.0) * 100.0, 2) if ema200 else None,
        },
        "formula_verified": (
            f"ADX14={adx14:.1f}, BBW_Pct120={bbw_pct:.1f}%, HV20/60={hv_ratio:.2f}, "
            f"+DI={pdi:.1f}/-DI={mdi:.1f} -> {regime}"
        ),
    }


def evaluate_five_core_strategies(
    bars: list[dict[str, Any]],
    ind: dict[str, Any],
    regime_info: dict[str, Any],
) -> list[dict[str, Any]]:
    """五类纯数学主信号策略（独立可审计公式实现）：
    1. supertrend_ema_trend: Supertrend(14,3) 方向与 EMA200 趋势共振
    2. ema_ribbon_channel: EMA20/EMA50 多空排列与 EMA200 过滤
    3. bollinger_squeeze_breakout: BBW 分位压缩 + 上下轨突破 + RVOL 放量
    4. rsi_stoch_mean_reversion: RSI14 + Stochastic 超买超卖回归
    5. macd_zero_accel: MACD 零轴交叉与柱体连续加速
    """
    n = len(bars)
    if n < 30:
        return []
    series = ind["series"]
    snap = ind["snapshot"]
    last_c = float(bars[-1]["close"])
    last_day = bars[-1]["day"]

    ema20 = snap["trend"]["ema20"] or last_c
    ema50 = snap["trend"]["ema50"] or last_c
    ema200 = snap["trend"]["ema200"]  # 预热不足时为 None，EMA200 过滤条件视为不满足
    ema200_txt = f"{ema200:.2f}" if ema200 is not None else "N/A(预热不足)"
    st_dir_series = series["supertrend"]["direction"]
    st_line_series = series["supertrend"]["line"]
    st_dir = int(st_dir_series[-1] or 0)
    st_prev = int(st_dir_series[-2] or st_dir) if n >= 2 else st_dir
    st_line = float(st_line_series[-1] or last_c)

    bb_u = snap["volatility"]["bb_upper"] or last_c * 1.03
    bb_l = snap["volatility"]["bb_lower"] or last_c * 0.97
    bbw_pct_series = series["bbw_percentile"]
    prev_bbw_pct = float(bbw_pct_series[-2]) if (n >= 2 and bbw_pct_series[-2] is not None) else 50.0
    rv = float(snap["momentum"]["rvol20"] or 1.0)

    rsi_series = series["rsi14"]
    stoch_k = series["stoch"]["k"]
    rsi_now = float(rsi_series[-1] or 50.0)
    rsi_prev = float(rsi_series[-2] or rsi_now) if n >= 2 else rsi_now
    k_now = float(stoch_k[-1] or 50.0)
    k_prev = float(stoch_k[-2] or k_now) if n >= 2 else k_now

    macd_dif = series["macd"]["macd"]
    macd_hist = series["macd"]["hist"]
    dif_now = float(macd_dif[-1] or 0.0)
    h0 = float(macd_hist[-1] or 0.0)
    h1 = float(macd_hist[-2] or 0.0) if n >= 2 else 0.0
    h2 = float(macd_hist[-3] or 0.0) if n >= 3 else 0.0

    signals: list[dict[str, Any]] = []

    # Strategy 1: Supertrend + EMA200 Trend Following
    if st_dir == 1 and ema200 is not None and last_c > ema200:
        s1_state = "LONG_ACTIVE" if st_prev == 1 else "LONG_TRIGGERED"
        s1_dir = "BULLISH"
    elif st_dir == -1 and ema200 is not None and last_c < ema200:
        s1_state = "SHORT_ACTIVE" if st_prev == -1 else "SHORT_TRIGGERED"
        s1_dir = "BEARISH"
    else:
        s1_state = "NEUTRAL"
        s1_dir = "NEUTRAL"
    signals.append(
        {
            "strategy_id": "supertrend_ema_trend",
            "name_cn": "Supertrend + EMA200 趋势追踪",
            "family": "TREND_FOLLOWING",
            "direction": s1_dir,
            "state": s1_state,
            "as_of": last_day,
            "formula_verified": f"SupertrendDir={st_dir} (Line={st_line:.2f}) AND Close({last_c:.2f}) vs EMA200({ema200_txt})",
        }
    )

    # Strategy 2: EMA20 / EMA50 / EMA200 Ribbon
    if ema200 is not None and ema20 > ema50 and last_c > ema20 and last_c >= ema200:
        s2_dir, s2_state = "BULLISH", "LONG_ACTIVE"
    elif ema200 is not None and ema20 < ema50 and last_c < ema20 and last_c <= ema200:
        s2_dir, s2_state = "BEARISH", "SHORT_ACTIVE"
    else:
        s2_dir, s2_state = "NEUTRAL", "NEUTRAL"
    signals.append(
        {
            "strategy_id": "ema_ribbon_channel",
            "name_cn": "EMA 20/50/200 均线丝带",
            "family": "TREND_FOLLOWING",
            "direction": s2_dir,
            "state": s2_state,
            "as_of": last_day,
            "formula_verified": f"Close({last_c:.2f}), EMA20({ema20:.2f}), EMA50({ema50:.2f}), EMA200({ema200_txt})",
        }
    )

    # Strategy 3: Bollinger Squeeze Breakout
    if prev_bbw_pct <= 25.0 and last_c > bb_u and rv >= 1.35:
        s3_dir, s3_state = "BULLISH", "LONG_TRIGGERED"
    elif prev_bbw_pct <= 25.0 and last_c < bb_l and rv >= 1.35:
        s3_dir, s3_state = "BEARISH", "SHORT_TRIGGERED"
    else:
        s3_dir, s3_state = "NEUTRAL", "NEUTRAL"
    signals.append(
        {
            "strategy_id": "bollinger_squeeze_breakout",
            "name_cn": "Bollinger Squeeze 波动率突破",
            "family": "VOLATILITY_BREAKOUT",
            "direction": s3_dir,
            "state": s3_state,
            "as_of": last_day,
            "formula_verified": f"PrevBBW_Pct({prev_bbw_pct:.1f}%) <= 25% AND Close({last_c:.2f}) vs BB[{bb_l:.2f}, {bb_u:.2f}] AND RVOL({rv:.2f})",
        }
    )

    # Strategy 4: RSI + Stochastic Extreme Mean Reversion
    if (rsi_now <= 36.0 or (rsi_prev <= 32.0 and rsi_now > rsi_prev)) and (k_now <= 28.0 or (k_prev <= 22.0 and k_now > k_prev)):
        s4_dir, s4_state = "BULLISH", "LONG_TRIGGERED"
    elif (rsi_now >= 68.0 or (rsi_prev >= 70.0 and rsi_now < rsi_prev)) and (k_now >= 75.0 or (k_prev >= 80.0 and k_now < k_prev)):
        s4_dir, s4_state = "BEARISH", "SHORT_TRIGGERED"
    else:
        s4_dir, s4_state = "NEUTRAL", "NEUTRAL"
    signals.append(
        {
            "strategy_id": "rsi_stoch_mean_reversion",
            "name_cn": "RSI + Stochastic 极值回归",
            "family": "MEAN_REVERSION",
            "direction": s4_dir,
            "state": s4_state,
            "as_of": last_day,
            "formula_verified": f"RSI14={rsi_now:.1f} (prev={rsi_prev:.1f}), StochK={k_now:.1f} (prev={k_prev:.1f})",
        }
    )

    # Strategy 5: MACD Zero-Line Momentum Acceleration
    if dif_now >= 0 and h0 > h1 > h2 and h0 > 0:
        s5_dir, s5_state = "BULLISH", "LONG_ACTIVE"
    elif dif_now < 0 and h0 < h1 < h2 and h0 < 0:
        s5_dir, s5_state = "BEARISH", "SHORT_ACTIVE"
    else:
        s5_dir, s5_state = "NEUTRAL", "NEUTRAL"
    signals.append(
        {
            "strategy_id": "macd_zero_accel",
            "name_cn": "MACD 零轴动量加速",
            "family": "MOMENTUM",
            "direction": s5_dir,
            "state": s5_state,
            "as_of": last_day,
            "formula_verified": f"DIF={dif_now:.3f}, Hist=[{h2:.3f} -> {h1:.3f} -> {h0:.3f}]",
        }
    )

    return signals


def aggregate_bars_to_weekly(daily_bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将已收盘日线聚合成周线（仅消费已完成日线，杜绝未收盘周线跨周泄漏）。"""
    if not daily_bars:
        return []
    weeks: list[dict[str, Any]] = []
    cur_key: tuple[int, int] | None = None
    cur_bucket: list[dict[str, Any]] = []
    from datetime import datetime

    for b in daily_bars:
        if not b.get("complete", True):
            continue
        dt = datetime.strptime(b["day"][:10], "%Y-%m-%d").date()
        iso_y, iso_w, _ = dt.isocalendar()
        key = (iso_y, iso_w)
        if cur_key != key:
            if cur_bucket:
                weeks.append(
                    {
                        "day": cur_bucket[-1]["day"],
                        "open": float(cur_bucket[0]["open"]),
                        "high": max(float(x["high"]) for x in cur_bucket),
                        "low": min(float(x["low"]) for x in cur_bucket),
                        "close": float(cur_bucket[-1]["close"]),
                        "volume": sum(float(x.get("volume") or 0.0) for x in cur_bucket),
                        "complete": True,
                    }
                )
            cur_key = key
            cur_bucket = [b]
        else:
            cur_bucket.append(b)
    if cur_bucket:
        weeks.append(
            {
                "day": cur_bucket[-1]["day"],
                "open": float(cur_bucket[0]["open"]),
                "high": max(float(x["high"]) for x in cur_bucket),
                "low": min(float(x["low"]) for x in cur_bucket),
                "close": float(cur_bucket[-1]["close"]),
                "volume": sum(float(x.get("volume") or 0.0) for x in cur_bucket),
                "complete": True,
            }
        )
    return weeks


def _average_ranks_simple(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0
        i = j + 1
    return ranks


def compute_causal_momentum_ic(
    daily_bars: list[dict[str, Any]],
    horizon: int = 15,
    lookback: int = 240,
    step: int = 5,
) -> float:
    """只用截至当日已完全成熟的样本 (s + horizon <= len(daily_bars) - 1)，
    无前视计算过去 20 日均线偏离度 (Close_s / SMA20_s - 1) 与随后 horizon 日收益的 Spearman 秩相关。
    > 0 表示该标的近期呈动量延续特征；< 0 表示该标的近期呈短期反转/均值回归特征。
    """
    n = len(daily_bars)
    last_mature = n - 1 - horizon
    if last_mature < 40:
        return 0.0
    closes = [float(b["close"]) for b in daily_bars]
    start_s = max(20, last_mature - lookback)
    xs: list[float] = []
    ys: list[float] = []
    for s in range(start_s, last_mature + 1, step):
        ma20_s = sum(closes[s - 19 : s + 1]) / 20.0
        if ma20_s <= 0 or closes[s] <= 0:
            continue
        xs.append(closes[s] / ma20_s - 1.0)
        ys.append(closes[s + horizon] / closes[s] - 1.0)
    if len(xs) < 6:
        return 0.0
    rx, ry = _average_ranks_simple(xs), _average_ranks_simple(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx <= 0 or vy <= 0:
        return 0.0
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / ((vx * vy) ** 0.5)


def compute_multi_timeframe_score(
    daily_bars: list[dict[str, Any]],
    ind: dict[str, Any],
    wedge: dict[str, Any],
    algebraic_patterns: list[dict[str, Any]],
    smc: dict[str, Any],
    strategy_signals: list[dict[str, Any]],
    benchmark_info: dict[str, Any],
) -> dict[str, Any]:
    """计算可逐项解释的多周期共振规则分 (rule_score: 0~100)，按组封顶防止相关指标重复加权，并明确与有监督 Meta-Labeling 区分。"""
    from indicators import ema

    weekly_bars = aggregate_bars_to_weekly(daily_bars)
    w_closes = [float(b["close"]) for b in weekly_bars]
    w_ema20 = ema(w_closes, min(20, max(3, len(w_closes) // 2))) if len(w_closes) >= 6 else [None]
    w_bull = bool(w_closes and w_ema20[-1] is not None and w_closes[-1] >= float(w_ema20[-1]))

    snap = ind["snapshot"]
    last_c = float(daily_bars[-1]["close"])
    ema200 = snap["trend"]["ema200"]
    rv = float(snap["momentum"]["rvol20"] or 1.0)
    corr_pv = float(snap["qlib_factors"].get("corr_close_logvol_20d") or 0.0)

    # 1. 大周期方向组 (Macro Trend Group: 0 ~ 25)
    macro_pts = 12.5
    if w_bull:
        macro_pts += 6.5
    else:
        macro_pts -= 5.0
    if ema200 is not None:
        macro_pts += 6.0 if last_c >= float(ema200) else -5.0
    macro_pts = max(0.0, min(25.0, macro_pts))

    # 2. 中周期结构与形态组 (Structure & Pattern Group: 0 ~ 25)
    struct_pts = 12.5
    if wedge.get("status") == "above":
        struct_pts += 8.0
    elif wedge.get("status") == "below":
        struct_pts -= 8.0
    bull_pats = sum(1 for p in algebraic_patterns if p.get("direction") == "BULLISH")
    bear_pats = sum(1 for p in algebraic_patterns if p.get("direction") == "BEARISH")
    struct_pts += max(-6.0, min(6.0, (bull_pats - bear_pats) * 3.0))
    if any(b["type"] == "BULLISH_BOS" for b in smc.get("structure_breaks", [])):
        struct_pts += 3.0
    if any(b["type"] == "BEARISH_BOS" for b in smc.get("structure_breaks", [])):
        struct_pts -= 3.0
    struct_pts = max(0.0, min(25.0, struct_pts))

    # 3. 策略池与微观触发组 (Strategy & Momentum Group: 0 ~ 25)
    strat_pts = 12.5
    bull_strats = sum(1 for s in strategy_signals if s["direction"] == "BULLISH")
    bear_strats = sum(1 for s in strategy_signals if s["direction"] == "BEARISH")
    strat_pts += max(-10.0, min(10.0, (bull_strats - bear_strats) * 3.5))
    strat_pts = max(0.0, min(25.0, strat_pts))

    # 4. 量价配合与大盘共振组 (Volume & Benchmark Resonance Group: 0 ~ 25)
    res_pts = 12.5
    b_score = float(benchmark_info.get("regime_score") or 50.0)
    rs_ratio = float(benchmark_info.get("relative_strength_vs_index") or 1.0)
    res_pts += max(-6.0, min(6.0, (b_score - 50.0) * 0.2))
    if rs_ratio >= 1.02:
        res_pts += 3.5
    elif rs_ratio <= 0.98:
        res_pts -= 3.5
    if rv >= 1.25 and last_c >= float(daily_bars[-1]["open"]):
        res_pts += 3.0
    elif corr_pv < -0.25:
        res_pts -= 2.5
    res_pts = max(0.0, min(25.0, res_pts))

    total_score = round(macro_pts + struct_pts + strat_pts + res_pts, 1)

    # 5. 无前视动量/反转体制自适应旋转与周线 60 周结构位置协同
    mom_ic = compute_causal_momentum_ic(daily_bars, horizon=15)
    tr = snap["trend"]
    ema20 = float(tr.get("ema20") or last_c)
    ema60 = float(tr.get("ema60") or last_c)
    ema200_val = float(ema200) if ema200 is not None else last_c
    sma250_val = float(tr.get("sma250") or last_c)
    atr14 = max(last_c * 0.005, float(snap["volatility"].get("atr14") or (last_c * 0.018)))
    rsi14 = float(snap["momentum"].get("rsi14") or 50.0)
    ext_atr = (last_c - ema20) / atr14

    w_win = weekly_bars[-min(60, len(weekly_bars)) :]
    w_hi = max(float(b["high"]) for b in w_win) if w_win else last_c
    w_lo = min(float(b["low"]) for b in w_win) if w_win else last_c
    w_pos = (last_c - w_lo) / max(1e-6, w_hi - w_lo)

    macro_weak = (ema60 < ema200_val) or (ema60 < sma250_val) or (ema200_val < sma250_val)
    is_mr_regime = mom_ic <= -0.10 and macro_weak
    if is_mr_regime:
        rev_adj = max(-14.0, min(14.0, -ext_atr * 6.0 + (50.0 - rsi14) * 0.25))
        blend = min(0.85, max(0.45, abs(mom_ic) * 2.2))
        inverted = 100.0 - total_score
        total_score = round(max(5.0, min(95.0, (1.0 - blend) * total_score + blend * inverted + rev_adj)), 1)

    if total_score >= 65.0:
        rating = "BULLISH_RESONANCE"
        rating_cn = "多周期多头共振"
    elif total_score <= 38.0:
        rating = "BEARISH_PRESSURE"
        rating_cn = "多周期空头承压"
    else:
        rating = "NEUTRAL_WATCH"
        rating_cn = "多空分歧/等待确认"

    return {
        "rule_score": total_score,
        "rating": rating,
        "rating_cn": rating_cn,
        "components": {
            "macro_trend_25": round(macro_pts, 1),
            "structure_pattern_25": round(struct_pts, 1),
            "strategy_trigger_25": round(strat_pts, 1),
            "volume_benchmark_25": round(res_pts, 1),
        },
        "timeframes_aligned": {
            "weekly_trend": "BULLISH" if w_bull else "BEARISH_OR_FLAT",
            "daily_structure": wedge.get("status", "inside"),
            "active_bull_strategies": bull_strats,
            "active_bear_strategies": bear_strats,
            "causal_momentum_ic_15d": round(mom_ic, 3),
            "scoring_regime": "MEAN_REVERSION_ADAPTED" if is_mr_regime else "MOMENTUM_TREND",
            "weekly_60w_position_pct": round(w_pos * 100.0, 1),
        },
        "note": "rule_score 为确定性多维度规则加权分（0~100），不代表统计胜率或预测概率。",
    }
