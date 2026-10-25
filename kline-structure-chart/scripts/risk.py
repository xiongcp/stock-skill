from __future__ import annotations

import math
from typing import Any

from market_rules import (
    compute_round_trip_cost_detail,
    get_market_microstructure_rules,
    price_decimals,
    round_shares_to_lot,
    round_to_tick,
    round_trip_cost_rate,
    tick_size_for,
)


def _num_or(mapping: dict[str, Any], key: str, default: float) -> float:
    val = mapping.get(key)
    return default if val is None else float(val)


def _avoid_round_number_stop(
    raw_stop: float,
    entry_low: float,
    market: str,
    asset_type: str,
    as_of_day: str | None,
) -> float:
    """防程序化扫损（Anti-Stop-Hunt）：若量化后的止损位落在整数/半整数关口 ±1 tick 内，移到该关口下方 2 个 tick。

    只在 tick 远小于 0.5 的价位档生效（0.5 至少 20 个 tick）：港股 0.05 及以上价位档里几乎每个价位都离
    0.5 关口不超过 1 tick，若照常避让就等于把所有止损都放宽两档。
    """
    st = round_to_tick(raw_stop, market, asset_type, as_of_day)
    if st <= 0:
        return st
    tick = float(tick_size_for(st, market, asset_type, as_of_day))
    if st >= 5.0 and 0 < tick <= 0.025:
        nearest_half = round(st * 2.0) / 2.0
        if abs(st - nearest_half) <= 1.05 * tick:
            # 以关口为锚下移，避免 x.01 -> x.99 这类移动后仍贴着关口
            shifted = round_to_tick(nearest_half - 2.0 * tick, market, asset_type, as_of_day)
            if 0 < shifted < entry_low:
                return shifted
    return st


# 反量化规则的门禁范围（Dev 2021–2024 + Val 2025H1 的拦截归因结果，见 research/anti_quant_filter_study.py（research/archive 分支）：
# 证据支持的规则才改变 setup/评级；证据相反或样本不足的规则只输出提示，不降级、不否决。
_ANTI_QUANT_GATES: dict[str, frozenset[str]] = {
    "ignition_trap_downgrade": frozenset({"US"}),  # CN/HK 的"被拦截组"反而更好
    "algo_bleed_gate": frozenset({"CN", "US"}),  # HK Dev 拦截组反而更好
}


def compute_triple_barrier_plan(
    bars: list[dict[str, Any]],
    meta: dict[str, Any],
    ind_snapshot: dict[str, Any],
    wedge: dict[str, Any],
    ladder: dict[str, Any],
    projections: dict[str, Any],
    mtf_score: dict[str, Any],
    algebraic_patterns: list[dict[str, Any]],
    benchmark_info: dict[str, Any],
    horizon_bars: int = 15,
    account_equity: float | None = None,
    max_risk_pct: float = 0.01,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """计算：
    1. 二阶段规则过滤决策 (meta_labeling_decision)
    2. 左右侧双轨条件与 Triple-Barrier 出场计划 (triple_barrier_plan)
    3. TradingAgents 风格多空双向证伪与独立风控官否决权 (debate_and_veto)
    """
    market = meta["market"]
    asset_type = meta["asset_type"]
    symbol = meta["symbol"]
    as_of_day = meta.get("as_of")
    is_st = bool(meta.get("is_st", False))
    is_ipo_no_limit = bool(meta.get("is_ipo_no_limit", False))
    rules = get_market_microstructure_rules(
        market,
        asset_type,
        symbol,
        is_st=is_st,
        is_ipo_no_limit=is_ipo_no_limit,
        day_str=as_of_day,
        board_lot=meta.get("board_lot"),
        name=str(meta.get("name") or ""),
    )

    last_bar = bars[-1]
    last_c = float(last_bar["close"])
    p_dec = price_decimals(last_c, market, asset_type, as_of_day)
    atr14 = _num_or(ind_snapshot["volatility"], "atr14", last_c * 0.018)
    atr14 = max(last_c * 0.005, atr14)
    rv = _num_or(ind_snapshot["momentum"], "rvol20", 1.0)
    rsi14 = _num_or(ind_snapshot["momentum"], "rsi14", 50.0)
    rule_score = float(mtf_score["rule_score"])

    r1 = float(projections["breakout_level"]["price"])
    s1 = float(projections["breakdown_level"]["price"])
    bull_target_ladder = float(projections["bull"]["target_price"])

    # Determine setup type: RIGHT_SIDE_BREAKOUT vs LEFT_SIDE_PULLBACK vs WAIT_CONFIRMATION
    has_bull_div = any(p["pattern"] == "RSI_BULLISH_DIVERGENCE" for p in algebraic_patterns)
    has_bull_candle = any(p["category"] == "candlestick" and p["direction"] == "BULLISH" for p in algebraic_patterns)
    recent_3_days = {b["day"] for b in bars[-3:]}
    has_liquidity_sweep = any(
        p["pattern"] == "LIQUIDITY_SWEEP_BULLISH" and p.get("observed_at") in recent_3_days for p in algebraic_patterns
    )
    has_bull_engulfing_recent = any(
        p["pattern"] == "CDL_BULLISH_ENGULFING" and p.get("observed_at") in recent_3_days for p in algebraic_patterns
    )
    last_o = float(last_bar["open"])
    last_h = float(last_bar["high"])
    last_l = float(last_bar["low"])
    prev_c = float(bars[-2]["close"]) if len(bars) >= 2 else last_o
    bar_span = max(1e-9, last_h - last_l)
    close_in_range = (last_c - last_l) / bar_span
    upper_wick_ratio = (last_h - max(last_c, last_o)) / bar_span
    lower_wick_ratio = (min(last_c, last_o) - last_l) / bar_span
    body_to_range = abs(last_c - last_o) / bar_span
    effort_vs_result = ((last_c - prev_c) / max(1e-6, atr14)) / max(0.25, rv)

    win60 = bars[-min(60, len(bars)) :]
    hi60 = max(float(b["high"]) for b in win60) if win60 else last_c
    lo60 = min(float(b["low"]) for b in win60) if win60 else last_c
    win20 = bars[-min(20, len(bars)) :]
    hi20 = max(float(b["high"]) for b in win20) if win20 else last_c
    lo20 = min(float(b["low"]) for b in win20) if win20 else last_l
    high_rank_20 = 0.5 if hi20 <= lo20 else (max(last_c, last_o) - lo20) / max(1e-9, hi20 - lo20)
    ema20 = _num_or(ind_snapshot["trend"], "ema20", last_c)
    ema50 = _num_or(ind_snapshot["trend"], "ema50", ema20)
    sma5 = sum(float(b["close"]) for b in bars[-5:]) / max(1, min(5, len(bars)))
    highs_falling_5d = (
        len(bars) >= 5
        and float(bars[-1]["high"]) < float(bars[-3]["high"]) < float(bars[-5]["high"])
        and last_c < sma5
    )
    ext_atr = (last_c - ema20) / atr14
    tf_aligned = mtf_score.get("timeframes_aligned") or {}
    scoring_regime = tf_aligned.get("scoring_regime", "MOMENTUM_TREND")
    w_pos = _num_or(tf_aligned, "weekly_60w_position_pct", 50.0)
    climax_distribution = (hi60 / max(1e-6, lo60) >= 1.60) and (last_c <= hi20 - 2.0 * atr14) and (last_c < ema20)

    # 反量化机制 3：识别机构 TWAP/VWAP 算法拆单阴跌（中短期均线空头排列 last_c < ema20 <= ema50），禁止仅凭单根缩量微阳线左侧接飞刀
    algo_grinding_bleed = (last_c < ema20) and (ema20 <= ema50)
    micro_bos = (
        len(bars) >= 4
        and last_c > max(float(b["high"]) for b in bars[-4:-1])
        and close_in_range >= 0.65
    )
    right_turn_confirmed = (
        micro_bos
        or has_bull_engulfing_recent
        or (has_liquidity_sweep and (has_bull_div or close_in_range >= 0.58))
        or (has_bull_div and last_c > last_o and close_in_range >= 0.60)
    )
    if algo_grinding_bleed and scoring_regime != "MEAN_REVERSION_ADAPTED" and market in _ANTI_QUANT_GATES["algo_bleed_gate"]:
        is_stabilizing_bar = right_turn_confirmed
    else:
        is_stabilizing_bar = (
            (last_c >= last_o)
            or (lower_wick_ratio >= 0.35)
            or has_bull_div
            or has_bull_candle
            or has_liquidity_sweep
        )
    is_volume_contraction = rv <= 1.10

    # 反量化机制 1：识别量化点火假突破/冲高回落派发（假阳真阴、长上影线 >40%、收盘未能站稳日内上半区 <58%）
    is_ignition_bull_trap = (last_c < last_o) or (upper_wick_ratio > 0.40) or (close_in_range < 0.58)
    is_bull_trap_churn = (
        rv >= 2.2
        and (body_to_range < 0.28 or upper_wick_ratio >= 0.42)
        and high_rank_20 >= 0.75
    )

    is_mr_left_pullback = (
        scoring_regime == "MEAN_REVERSION_ADAPTED"
        and rule_score >= 52.0
        and not climax_distribution
        and wedge.get("status") != "below"
        and ext_atr <= 0.15
        and w_pos <= 78.0
        and (is_volume_contraction or has_bull_div or has_bull_candle or has_liquidity_sweep)
    )
    is_std_left_pullback = (
        rule_score >= 65.0
        and not climax_distribution
        and is_stabilizing_bar
        and (
            has_bull_div
            or has_bull_candle
            or has_liquidity_sweep
            or (abs(last_c - s1) <= 0.8 * atr14 and is_volume_contraction)
        )
    )

    raw_breakout_signal = wedge.get("status") == "above" or (last_c >= r1 and rv >= 1.35)
    ignition_gate_active = market in _ANTI_QUANT_GATES["ignition_trap_downgrade"]
    if raw_breakout_signal and not (is_ignition_bull_trap and ignition_gate_active):
        setup_type = "RIGHT_SIDE_BREAKOUT"
        setup_label_cn = "右侧顺势突破确认"
        single_day_spike = ((last_c - prev_c) > 1.8 * atr14) and (last_c > r1 > s1)
        if last_c >= r1:
            if single_day_spike:
                # 突破日脉冲涨幅过大：禁止将入场区上沿追在最高点，收敛至突破位 r1 附近的回踩承接带
                retest_anchor = min(last_c - 0.10 * atr14, max(s1 + 0.20 * atr14, r1))
                entry_low = round_to_tick(max(s1 * 1.002, retest_anchor - 0.30 * atr14), market, asset_type, as_of_day)
                entry_high = round_to_tick(max(entry_low, min(last_c, retest_anchor + 0.15 * atr14)), market, asset_type, as_of_day)
            else:
                entry_low = round_to_tick(min(last_c, r1 * 0.996), market, asset_type, as_of_day)
                entry_high = round_to_tick(max(last_c, r1 * 1.005), market, asset_type, as_of_day)
        else:
            entry_low = round_to_tick(max(s1, last_c - 0.35 * atr14), market, asset_type, as_of_day)
            entry_high = round_to_tick(min(r1, last_c + 0.30 * atr14), market, asset_type, as_of_day)
        raw_sl = min(s1, max(s1 - 0.35 * atr14, last_c - 1.35 * atr14))
        stop_loss = _avoid_round_number_stop(raw_sl, entry_low, market, asset_type, as_of_day)
    elif is_std_left_pullback or is_mr_left_pullback:
        setup_type = "LEFT_SIDE_PULLBACK"
        setup_label_cn = "左侧支撑回踩观察"
        entry_low = round_to_tick(max(s1 * 0.995, last_c - 0.45 * atr14), market, asset_type, as_of_day)
        entry_high = round_to_tick(min(r1, last_c + 0.20 * atr14), market, asset_type, as_of_day)
        if has_liquidity_sweep:
            sweep_anchor = min(s1, last_l)
            raw_sl = min(sweep_anchor - 0.35 * atr14, max(sweep_anchor - 0.60 * atr14, last_c - 1.50 * atr14))
            stop_loss = _avoid_round_number_stop(raw_sl, entry_low, market, asset_type, as_of_day)
        else:
            raw_sl = min(s1 - 0.15 * atr14, max(s1 - 0.50 * atr14, last_c - 1.45 * atr14))
            stop_loss = _avoid_round_number_stop(raw_sl, entry_low, market, asset_type, as_of_day)
    else:
        setup_type = "WAIT_CONFIRMATION"
        setup_label_cn = (
            "冲高回落/假阳线待右侧二次确认"
            if (raw_breakout_signal and is_ignition_bull_trap and ignition_gate_active)
            else "区间观察/等待右侧收盘突破"
        )
        if last_c < last_o:
            entry_low = round_to_tick(max(s1, min(r1, last_c + 0.08 * atr14)), market, asset_type, as_of_day)
            entry_high = round_to_tick(max(entry_low, min(r1 * 1.003, last_c + 0.45 * atr14)), market, asset_type, as_of_day)
        else:
            entry_low = round_to_tick(max(s1, last_c - 0.20 * atr14), market, asset_type, as_of_day)
            entry_high = round_to_tick(min(r1, last_c + 0.35 * atr14), market, asset_type, as_of_day)
        raw_sl = min(s1 - 0.15 * atr14, max(s1 - 0.50 * atr14, last_c - 1.40 * atr14))
        stop_loss = _avoid_round_number_stop(raw_sl, entry_low, market, asset_type, as_of_day)
    if entry_low > entry_high:
        entry_low = round_to_tick(last_c * 0.995, market, asset_type, as_of_day)
        entry_high = round_to_tick(last_c * 1.005, market, asset_type, as_of_day)

    entry_mid = (entry_low + entry_high) / 2.0
    unit_risk = max(atr14 * 0.5, entry_mid - stop_loss)
    eff_swing_risk = min(unit_risk, 1.50 * atr14)
    if setup_type == "RIGHT_SIDE_BREAKOUT":
        struct_cap = max(r1, bull_target_ladder)
    else:
        struct_cap = r1 if r1 >= entry_mid + 1.15 * unit_risk else bull_target_ladder
    raw_tp1 = min(struct_cap, entry_mid + 1.5 * eff_swing_risk)
    if raw_tp1 <= entry_mid:
        raw_tp1 = entry_mid + 1.0 * atr14
    tp1 = round_to_tick(raw_tp1, market, asset_type, as_of_day)
    tp2 = round_to_tick(max(tp1 + 0.8 * atr14, bull_target_ladder), market, asset_type, as_of_day)

    # Round-trip fee deduction in price units
    fee_rate = round_trip_cost_rate(rules)
    net_reward = max(0.0, (tp1 - entry_mid) - entry_mid * fee_rate)
    net_risk = unit_risk + entry_mid * fee_rate
    rr_ratio = round(net_reward / net_risk, 2) if net_risk > 0 else 0.0

    # Independent Risk Officer Veto checks（veto_codes 为稳定编码，供回测统计否决原因）
    veto_reasons: list[str] = []
    veto_codes: list[str] = []
    if benchmark_info.get("benchmark_missing"):
        veto_codes.append("BENCHMARK_MISSING")
        veto_reasons.append("缺少大盘基准行情，无法执行先大盘、后个股检查，禁止给出做多确认")
    elif _num_or(benchmark_info, "regime_score", 50.0) < 35.0:
        veto_codes.append("BENCHMARK_WEAK")
        veto_reasons.append(f"大盘环境评分偏弱 ({benchmark_info.get('regime_score')}<35)，压降个股做多评级")
    if rr_ratio < 1.20:
        veto_codes.append("RR_INSUFFICIENT")
        veto_reasons.append(f"扣除交易成本后 TP1 净盈亏比不足 ({rr_ratio:.2f} < 1.20)")
    if wedge.get("status") == "below":
        veto_codes.append("BELOW_LOWER_RAIL")
        veto_reasons.append("日线收盘处于结构下轨下方（破位状态未修复）")
    if setup_type == "RIGHT_SIDE_BREAKOUT" and rv < 1.15:
        veto_codes.append("BREAKOUT_LOW_VOLUME")
        veto_reasons.append(f"上攻突破缺乏成交量配合 (RVOL={rv:.2f} < 1.15)，警惕假突破")
    # 计划几何不变量：止损必须低于整个入场区、TP1 必须高于整个入场区，否则在区内成交即刻处于止损下方或止盈即亏损
    if stop_loss >= entry_low or tp1 <= entry_high:
        veto_codes.append("PLAN_GEOMETRY_INVALID")
        veto_reasons.append(
            f"计划点位自相矛盾（入场区 {entry_low:.{p_dec}f}~{entry_high:.{p_dec}f}，止损 {stop_loss:.{p_dec}f}，TP1 {tp1:.{p_dec}f}），不可执行"
        )

    vetoed = len(veto_reasons) > 0
    ret5_atr = (last_c - float(bars[-6]["close"])) / max(1e-6, atr14) if len(bars) >= 6 else 0.0
    momentum_crowding = (
        (ret5_atr > 2.4)
        or (ext_atr > 1.65)
        or (w_pos >= 82.0)
    )
    overextended = (
        (ext_atr > 2.0)
        or (rsi14 >= 70.0)
        or climax_distribution
    )
    if scoring_regime == "MEAN_REVERSION_ADAPTED":
        if ext_atr > 0.35 or w_pos > 78.0 or setup_type == "RIGHT_SIDE_BREAKOUT":
            overextended = True

    anti_quant_warnings: list[str] = []
    if is_bull_trap_churn:
        anti_quant_warnings.append(
            f"【仅提示·高位放量滞涨】近20日高位（分位 {high_rank_20*100:.0f}%）放巨量（量比 {rv:.2f}x）却收长上影/窄实体（上影 {upper_wick_ratio*100:.0f}%、实体 {body_to_range*100:.0f}%），形态上接近量化对倒冲高派发；历史回溯未证实其预测力，不改变评级，请自行评估追高风险"
        )
    elif raw_breakout_signal and is_ignition_bull_trap:
        if ignition_gate_active:
            anti_quant_warnings.append(
                f"【防冲高回落假突破】盘中虽刺破压力位但收盘拉出上影线（上影占比 {upper_wick_ratio*100:.0f}%、日内收盘分位 {close_in_range*100:.0f}%）或收假阳真阴，已从右侧突破降级为观望确认"
            )
        else:
            anti_quant_warnings.append(
                f"【仅提示·冲高回落形态】收盘拉出上影线（上影占比 {upper_wick_ratio*100:.0f}%、日内收盘分位 {close_in_range*100:.0f}%）或收假阳真阴；"
                + ("A 股 T+1 下当日追入次日可能低开，" if market == "CN" else "")
                + "该市场历史回溯未证实此形态预测力，不改变评级"
            )
    if algo_grinding_bleed and not right_turn_confirmed:
        if market in _ANTI_QUANT_GATES["algo_bleed_gate"]:
            anti_quant_warnings.append(
                "【防阴跌接飞刀】收盘低于 EMA20 且 EMA20 不高于 EMA50（中短期均线走弱）；未见右侧微观破位收回（Micro-BOS）或放量吞没前，左侧回踩计划不予确认"
            )
        else:
            anti_quant_warnings.append(
                "【仅提示·均线走弱】收盘低于 EMA20 且 EMA20 不高于 EMA50；该市场历史回溯未证实此条件的预测力，不改变评级，左侧抄底风险请自行评估"
            )
    # 过热会实际阻止做多确认，必须优先披露；动量拥挤阈值更宽，放在前面会把降级说明盖成"不改变评级"
    if overextended:
        anti_quant_warnings.append(
            f"【过热降级】偏离 EMA20 {ext_atr:+.2f} ATR、RSI14 {rsi14:.1f} 或高位派发结构触发过热条件，已不给出做多确认"
        )
    elif momentum_crowding:
        anti_quant_warnings.append(
            f"【仅提示·短期涨幅偏大】5日推进 {ret5_atr:+.2f} ATR，偏离 EMA20 {ext_atr:+.2f} ATR，60周分位 {w_pos:.1f}%；历史回溯中此类截面表现并不差，不改变评级，仅提示追高回撤风险"
        )
    if has_liquidity_sweep:
        anti_quant_warnings.append(
            "【破底翻扫损收回提示】近期出现盘中刺穿波段前低扫损后放量收阳站回支撑（LIQUIDITY_SWEEP_BULLISH），防守止损已设置于扫损极值下方并避开整数挂单关口"
        )
    qlib_f = ind_snapshot.get("qlib_factors") or {}
    vpa_dryup = bool(qlib_f.get("vpa_pullback_dryup", False))
    vpa_utad = bool(qlib_f.get("vpa_upthrust_distribution", False))
    if vpa_utad and not is_bull_trap_churn:
        anti_quant_warnings.append(
            f"【量价VPA·高位放量上冲回落(UTAD)】近20日高位放量（量比 {rv:.2f}x）上冲但收盘落入日内偏弱区（分位 {close_in_range*100:.0f}%），供应涌现，警惕派发"
        )
    if vpa_dryup and setup_type == "LEFT_SIDE_PULLBACK":
        anti_quant_warnings.append(
            "【量价VPA·缩量回踩测试】近期回调量能萎缩且日内承接良好，符合供需衰竭（No Supply）企稳特征"
        )

    if not vetoed and not overextended and rule_score >= 65.0 and setup_type in ("RIGHT_SIDE_BREAKOUT", "LEFT_SIDE_PULLBACK"):
        action = "LONG_CONFIRMED"
        action_cn = "右侧/结构看多确认"
    elif (
        not vetoed
        and not overextended
        and rule_score >= 52.0
        and setup_type in ("RIGHT_SIDE_BREAKOUT", "LEFT_SIDE_PULLBACK")
        and (setup_type == "RIGHT_SIDE_BREAKOUT" or is_stabilizing_bar or has_bull_div or has_bull_candle)
    ):
        action = "WATCH_LONG_SETUP"
        action_cn = "条件观察（待放量/收盘确认）"
    elif rule_score <= 38.0 or wedge.get("status") == "below":
        action = "DEFENSIVE_AVOID"
        action_cn = "防守回避/破位警惕"
    else:
        action = "NEUTRAL_HOLD_WAIT"
        action_cn = "观望等待突破信号"

    # Position sizing calculation
    # 每股风险按入场区上沿（区内最差成交价）计：在入场区内任意价位成交，按止损价离场的亏损都不超过风险预算
    adj_factor = float(last_bar.get("adj_factor", 1.0)) or 1.0
    worst_unit_risk = ((entry_high - stop_loss) + entry_high * fee_rate) / adj_factor
    entry_high_raw = entry_high / adj_factor
    gap_risk_note = (
        "按止损价成交估算；跳空低开、跌停封板无法卖出或流动性不足时，实际亏损可能超过风险预算"
        + ("；A 股 T+1，买入当日不能卖出止损" if rules["settlement"] == "T+1" else "")
    )
    position_sizing: dict[str, Any]
    if asset_type == "index":
        position_sizing = {
            "tradable": False,
            "recommended_shares": None,
            "reason": "指数本身不可直接买卖，请映射至对应宽基 ETF 并提供账户资金后再计算仓位。",
        }
    elif account_equity is None or account_equity <= 0:
        position_sizing = {
            "tradable": True,
            "recommended_shares": None,
            "lot_size": rules["lot_size"],
            "sizing_basis": "ENTRY_HIGH_WORST_FILL",
            "unit_risk_per_share": round(worst_unit_risk, 4),
            "reason": "未提供账户净值 (account_equity)，仅输出价格屏障与每股风险，不编造建议股数。",
        }
    else:
        risk_budget = float(account_equity) * float(max_risk_pct)
        raw_shares = int(math.floor(risk_budget / max(1e-6, worst_unit_risk)))
        lot = int(rules["lot_size"])
        step = int(rules.get("lot_step", lot))
        lot_shares = round_shares_to_lot(raw_shares, rules)
        max_cash_shares = round_shares_to_lot(
            int(math.floor(float(account_equity) / max(1e-6, entry_high_raw * (1.0 + fee_rate)))), rules
        )
        final_shares = max(0, min(lot_shares, max_cash_shares))
        price_loss_per_share = (entry_high - stop_loss) / adj_factor
        cost_info = compute_round_trip_cost_detail(final_shares * entry_high_raw, rules, shares=final_shares)
        if cost_info["min_commission_applied"] and final_shares > 0:
            while final_shares > 0:
                ci = compute_round_trip_cost_detail(final_shares * entry_high_raw, rules, shares=final_shares)
                if final_shares * price_loss_per_share + ci["total_cost_amount"] <= risk_budget + 1e-6:
                    cost_info = ci
                    break
                final_shares = round_shares_to_lot(final_shares - step, rules)
            cost_info = compute_round_trip_cost_detail(final_shares * entry_high_raw, rules, shares=final_shares)
            gap_risk_note = (
                gap_risk_note
                + f"；小额单笔最低佣金/规费生效（双边最低佣金下往返固定摩擦至少约 { compute_round_trip_cost_detail(lot * entry_high_raw, rules)['total_cost_amount']:.2f} {meta.get('currency', '')}）"
            )
        max_loss_val = (
            round(final_shares * price_loss_per_share + cost_info["total_cost_amount"], 2)
            if (cost_info["min_commission_applied"] and final_shares > 0)
            else round(final_shares * worst_unit_risk, 2)
        )
        # 风控否决或评级不是做多确认/条件观察时不给股数：股数本身就是买入建议，与"否决时只给观察位"冲突
        long_allowed = not vetoed and action in ("LONG_CONFIRMED", "WATCH_LONG_SETUP")
        position_sizing = {
            "tradable": True,
            "account_equity": round(float(account_equity), 2),
            "max_risk_pct": round(float(max_risk_pct) * 100.0, 2),
            "risk_budget_amount": round(risk_budget, 2),
            "lot_size": lot,
            "min_order_shares": int(rules.get("min_order_shares", lot)),
            "lot_step": step,
            "sizing_basis": "ENTRY_HIGH_WORST_FILL",
            "unit_risk_per_share": round(worst_unit_risk, 4),
            "recommended_shares": final_shares if long_allowed else None,
            "estimated_notional": round(final_shares * entry_high_raw, 2) if long_allowed else None,
            "max_loss_at_stop": max_loss_val if long_allowed else None,
            "settlement_rule": rules["settlement"],
            "gap_risk_note": gap_risk_note,
        }
        if not long_allowed:
            position_sizing["reason"] = (
                "风控已否决做多（原因见 veto_reasons），不给出建议股数，以下点位只用于观察"
                if vetoed
                else f"当前评级为「{action_cn}」，不是做多确认或条件观察，不给出建议股数"
            )

    meta_decision = {
        "setup_type": setup_type,
        "setup_label_cn": setup_label_cn,
        "rule_score": rule_score,
        "scoring_method": "DETERMINISTIC_MULTI_FACTOR_RULE_V5",
        "action": action,
        "action_cn": action_cn,
        "risk_veto_applied": vetoed,
        "veto_reasons": veto_reasons,
        "veto_codes": veto_codes,
        "overextended": overextended,
        "anti_quant_traps": {
            "bull_trap_churn": is_bull_trap_churn,
            "ignition_bull_trap_downgraded": bool(raw_breakout_signal and is_ignition_bull_trap and ignition_gate_active),
            "gates_active": {
                "ignition_trap_downgrade": ignition_gate_active,
                "algo_bleed_gate": market in _ANTI_QUANT_GATES["algo_bleed_gate"],
                "bull_trap_churn": False,
                "momentum_crowding": False,
            },
            "algo_grinding_bleed": algo_grinding_bleed,
            "right_turn_confirmed": right_turn_confirmed,
            "momentum_crowding": momentum_crowding,
            "liquidity_sweep_reclaim": has_liquidity_sweep,
            "vpa_pullback_dryup": vpa_dryup,
            "vpa_upthrust_distribution": vpa_utad,
            "close_in_range": round(close_in_range, 4),
            "upper_wick_ratio": round(upper_wick_ratio, 4),
            "body_to_range": round(body_to_range, 4),
            "effort_vs_result": round(effort_vs_result, 4),
            "warnings_cn": anti_quant_warnings,
        },
    }

    retest_hold = float(projections["bull"]["retest_hold"])
    bear_target = float(projections["bear"]["target_price"])
    holder_defense = _holder_defense_plan(market, asset_type, as_of_day, p_dec, projections, stop_loss, s1, r1, bear_target)
    short_plan = _short_plan(
        market, asset_type, as_of_day, p_dec, projections, s1, r1, bear_target, atr14, fee_rate, benchmark_info
    )
    breakeven_trigger = round_to_tick(entry_mid + 0.90 * unit_risk, market, asset_type, as_of_day)
    breakeven_confirm = round_to_tick(entry_mid + 0.30 * unit_risk, market, asset_type, as_of_day)
    breakeven_stop = round_to_tick(entry_mid + 0.08 * unit_risk, market, asset_type, as_of_day)
    breakeven_ratchet = {
        "enabled": True,
        "trigger_r": 0.90,
        "confirm_close_r": 0.30,
        "lock_r": 0.08,
        "trigger_price": breakeven_trigger,
        "confirm_close_price": breakeven_confirm,
        "breakeven_stop_price": breakeven_stop,
        "rule_cn": (
            f"盘中最高价触及 +0.90R（以入场区中值计约 {breakeven_trigger:.{p_dec}f}）且当日收盘高于 +0.30R（约 {breakeven_confirm:.{p_dec}f}）后，"
            f"自次日起将保护性止损上移至 +0.08R 保本位（约 {breakeven_stop:.{p_dec}f}）"
        ),
    }
    t1_exit_note = "（A 股受 T+1 约束自入场次日起生效）" if rules["settlement"] == "T+1" else "（支持 T+0 当日止损）"
    rail_broken_up = wedge.get("status") == "above" and last_c < r1
    broke_rail_lvl = round_to_tick(float(wedge.get("upper_today") or s1), market, asset_type, as_of_day)
    rail_hold_lvl = round_to_tick(max(stop_loss, broke_rail_lvl - 0.25 * atr14), market, asset_type, as_of_day)
    right_req_ev = (
        f"日线收盘站稳已突破上轨 {broke_rail_lvl:.{p_dec}f} 之上（上方进一步突破位 {r1:.{p_dec}f}）且 RVOL20 >= 1.15 (当前 {rv:.2f})"
        if rail_broken_up
        else f"日线收盘站稳 {r1:.{p_dec}f} 且 RVOL20 >= 1.35 (当前 {rv:.2f})"
    )
    right_false_brk = (
        f"回踩收盘跌回上轨保护位 {rail_hold_lvl:.{p_dec}f} 下方视为假突破"
        if rail_broken_up
        else f"突破后收盘跌回 {retest_hold:.{p_dec}f} 下方视为假突破"
    )

    tb_plan = {
        "entry_zone": [entry_low, entry_high],
        "stop_loss_barrier": stop_loss,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "time_barrier_bars": horizon_bars,
        "risk_reward_ratio": rr_ratio,
        "breakeven_ratchet": breakeven_ratchet,
        "left_side_constraints": {
            "required_evidence": f"缩量企稳 (RVOL20 <= 1.10，当前 {rv:.2f}) + RSI 底背离或下轨支撑不破",
            "invalidation_level": stop_loss,
            "exit_rule": f"盘中最低价触及硬止损屏障 {stop_loss:.{p_dec}f} 无条件离场{t1_exit_note}",
        },
        "right_side_constraints": {
            "required_evidence": right_req_ev,
            "false_breakout_rule": right_false_brk,
        },
        "time_stop_rule": f"持仓满 {horizon_bars} 根K线未触及 TP1({tp1:.{p_dec}f}) 且偏离成本不足 0.5*ATR 时触发时间屏障离场",
        "market_microstructure": rules,
        "position_sizing": position_sizing,
        "holder_defense_plan": holder_defense,
        "short_plan": short_plan,
    }

    # Bull vs Bear structured falsification (TradingAgents style)
    if setup_type == "LEFT_SIDE_PULLBACK" or tp1 <= r1:
        bull_lead = (
            f"结构触发：若在支撑 {s1:.{p_dec}f} 上方企稳（入场/观察区 {entry_low:.{p_dec}f}~{entry_high:.{p_dec}f}，防守位 {stop_loss:.{p_dec}f}），"
            f"修复空间指向 TP1 {tp1:.{p_dec}f}，放量突破 {r1:.{p_dec}f}（{projections['breakout_level']['source']}）后指向 TP2 {tp2:.{p_dec}f}。"
        )
    else:
        bull_lead = (
            f"结构触发：若日线放量收盘站稳 {r1:.{p_dec}f}（{projections['breakout_level']['source']}），回踩不破 {retest_hold:.{p_dec}f}，"
            f"上方空间指向 TP1 {tp1:.{p_dec}f} / TP2 {tp2:.{p_dec}f}。"
        )
    bull_case: list[str] = [
        bull_lead,
    ]
    for p in algebraic_patterns:
        if p["direction"] == "BULLISH":
            bull_case.append(f"数学形态证据：{p['name_cn']} ({p['formula_verified']})。")
    if _num_or(benchmark_info, "relative_strength_vs_index", 1.0) >= 1.0:
        bull_case.append(
            f"相对强弱：标的相对基准指数 {benchmark_info.get('index_symbol')} 强弱比值为 {benchmark_info.get('relative_strength_vs_index'):.2f}。"
        )

    bear_case: list[str] = [
        f"空头证伪/失效线：若日线收盘跌破 {s1:.{p_dec}f}（{projections['breakdown_level']['source']}）或跌穿硬止损屏障 {stop_loss:.{p_dec}f}，多头结构立即失效，下方风险指向 {bear_target:.{p_dec}f}。",
    ]
    for p in algebraic_patterns:
        if p["direction"] == "BEARISH":
            bear_case.append(f"反向形态警示：{p['name_cn']} ({p['formula_verified']})。")
    if veto_reasons:
        bear_case.extend(f"独立风控否决项：{r}" for r in veto_reasons)

    debate = {
        "bull_case": bull_case[:4],
        "bear_case": bear_case[:4],
        "risk_officer_veto": vetoed,
        "veto_reasons": veto_reasons,
        "veto_codes": veto_codes,
        "final_verdict_cn": (
            f"【{action_cn}】综合规则分 {rule_score:.1f}/100，当前属于「{setup_label_cn}」；"
            f"关键突破位 {r1:.{p_dec}f}，关键防守/止损位 {stop_loss:.{p_dec}f}，TP1 {tp1:.{p_dec}f}（净盈亏比 {rr_ratio:.2f}）。"
        ),
    }

    return meta_decision, tb_plan, debate


def _holder_defense_plan(
    market: str,
    asset_type: str,
    day: str | None,
    p_dec: int,
    projections: dict[str, Any],
    stop_loss: float,
    s1: float,
    r1: float,
    bear_target: float,
) -> dict[str, Any]:
    """已持仓者的防守计划：收盘跌破下方触发位减仓、跌破硬止损离场；跌破后反抽不过反抽上限不宜回补。"""
    if asset_type == "index":
        return {"available": False, "reason": "指数不可直接持有，请映射到对应宽基 ETF 后再看防守位"}
    reduce_level = round_to_tick(s1, market, asset_type, day)
    exit_level = float(stop_loss)
    rebound_cap = float(projections["bear"]["rebound_cap"])
    src = projections["breakdown_level"]["source"]
    if exit_level < reduce_level:
        rules = [
            f"日线收盘跌破 {reduce_level:.{p_dec}f}（{src}）先减仓",
            f"盘中触及硬止损屏障 {exit_level:.{p_dec}f} 无条件离场",
        ]
    else:
        rules = [f"日线收盘跌破 {reduce_level:.{p_dec}f}（{src}）或盘中触及硬止损屏障 {exit_level:.{p_dec}f} 离场"]
    rules.append(f"跌破后反抽收盘未能站回 {rebound_cap:.{p_dec}f} 上方，不宜回补")
    return {
        "available": True,
        "applies_to": "已持仓者",
        "reduce_level": reduce_level,
        "exit_level": exit_level,
        "rebound_cap": rebound_cap,
        "upside_recheck_level": r1,
        "downside_reference": {"price": bear_target, "source": projections["bear"]["target_source"]},
        "rules": rules,
        "note": "A 股 T+1：当日买入的部分次日才能卖出" if market == "CN" else None,
    }


def _short_plan(
    market: str,
    asset_type: str,
    day: str | None,
    p_dec: int,
    projections: dict[str, Any],
    s1: float,
    r1: float,
    bear_target: float,
    atr14: float,
    fee_rate: float,
    benchmark_info: dict[str, Any],
) -> dict[str, Any]:
    """港股/美股的破位做空计划（三重屏障镜像）：收盘跌破下方触发位后次日执行，止损在破位位上方 1.5×ATR14 与反抽上限中较高者。

    A 股融券受标的名单、券源与额度限制，指数不可直接交易，均不提供做空计划。融券费用未计入盈亏比。
    """
    if asset_type == "index":
        return {"available": False, "reason": "指数不可直接交易"}
    if market == "CN":
        return {"available": False, "reason": "A 股融券受标的名单、券源与额度限制，本技能不提供做空计划；持仓者参考防守计划"}
    entry_high = round_to_tick(s1, market, asset_type, day)
    entry_low = round_to_tick(s1 - 0.25 * atr14, market, asset_type, day)
    entry_mid = (entry_low + entry_high) / 2.0
    rebound_cap = float(projections["bear"]["rebound_cap"])
    stop = round_to_tick(max(s1 + 1.5 * atr14, rebound_cap), market, asset_type, day)
    unit_risk = max(atr14 * 0.5, stop - entry_mid)
    tp1 = round_to_tick(max(entry_mid * 0.01, entry_mid - 1.5 * unit_risk), market, asset_type, day)
    tp2 = round_to_tick(max(entry_mid * 0.01, min(entry_mid - 2.5 * unit_risk, bear_target)), market, asset_type, day)
    net_reward = max(0.0, (entry_mid - tp1) - entry_mid * fee_rate)
    net_risk = unit_risk + entry_mid * fee_rate
    rr = round(net_reward / net_risk, 2) if net_risk > 0 else 0.0
    warnings = []
    if _num_or(benchmark_info, "regime_score", 50.0) >= 65.0:
        warnings.append(f"大盘环境评分偏强（{benchmark_info.get('regime_score')} ≥ 65），逆势做空风险高")
    if rr < 1.20:
        warnings.append(f"扣除交易成本后 TP1 净盈亏比不足（{rr:.2f} < 1.20，且未计融券费用）")
    constraints = (
        "港股只能卖空港交所指定的可卖空证券，且须先借入股票（禁止无担保卖空）；融券费用另计，以券商为准"
        if market == "HK"
        else "须券商确认可借券（Reg SHO locate 要求）；单日跌幅达 10% 触发 Rule 201 后，当日及次日只能以高于全国最优买价的价格卖空；融券费用另计"
    )
    return {
        "available": True,
        "trigger": f"日线收盘跌破 {s1:.{p_dec}f}（{projections['breakdown_level']['source']}）后次日执行",
        "entry_zone": [entry_low, entry_high],
        "stop_loss_barrier": stop,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "time_barrier_bars": projections.get("horizon_bars"),
        "risk_reward_ratio": rr,
        "invalidation": f"收盘站回 {stop:.{p_dec}f} 上方止损；收盘站上 {r1:.{p_dec}f} 则空头结构失效",
        "constraints": constraints,
        "warnings": warnings,
    }


def _is_round_number_price(price: float, market: str) -> bool:
    """判断止损价是否落在散户密集挂单的整数关口或半整数关口。"""
    if price <= 0:
        return False
    if price >= 500.0:
        return abs(price - round(price / 10.0) * 10.0) < 1e-6 or abs(price - round(price / 5.0) * 5.0) < 1e-6
    if price >= 50.0:
        return abs(price - round(price)) < 1e-6
    if price >= 5.0:
        return abs(price * 2.0 - round(price * 2.0)) < 1e-6
    return abs(price * 10.0 - round(price * 10.0)) < 1e-6


def audit_trade_idea(
    result: dict[str, Any],
    *,
    direction: str = "LONG",
    entry: float,
    stop: float,
    target: float,
    shares: int | None = None,
    note: str = "",
) -> dict[str, Any]:
    """对用户主观交易想法执行 6 维机械化双向风控审计（大盘否决冲突、追高延伸度、整数关口扫损陷阱、双边摩擦净盈亏比、保形区间可达性、If-Then 剧本）。"""
    import hashlib
    import json

    direction_u = str(direction or "LONG").upper()
    if direction_u not in ("LONG", "SHORT"):
        raise ValueError(f"direction 必须为 LONG 或 SHORT，收到: {direction!r}")
    entry_f, stop_f, target_f = float(entry), float(stop), float(target)
    if direction_u == "LONG" and not (stop_f < entry_f < target_f):
        raise ValueError(f"LONG 方向须满足 stop({stop_f}) < entry({entry_f}) < target({target_f})")
    if direction_u == "SHORT" and not (target_f < entry_f < stop_f):
        raise ValueError(f"SHORT 方向须满足 target({target_f}) < entry({entry_f}) < stop({stop_f})")

    meta = result.get("meta") or {}
    verdict = result.get("verdict") or {}
    plan = result.get("plan") or {}
    long_plan = plan.get("long") or plan
    ind = result.get("indicators") or {}
    structure = result.get("structure") or {}
    vr = result.get("volatility_range") or result.get("forecast") or {}

    symbol = str(meta.get("symbol") or "")
    market = str(meta.get("market") or "CN")
    asset_type = str(meta.get("asset_type") or "equity")
    as_of = str(meta.get("as_of") or "")
    last_close = float(structure.get("last_close") or meta.get("last_close") or entry_f)
    p_dec = int(meta.get("price_decimals") or 2)

    trend_ind = ind.get("trend") or {}
    vol_ind = ind.get("volatility") or {}
    ema20 = float(trend_ind.get("ema20") or last_close)
    atr14 = float(vol_ind.get("atr14") or max(0.01, abs(entry_f - stop_f)))

    rules = long_plan.get("market_microstructure") or {
        "market": market,
        "commission_bps": 2.5,
        "stamp_duty_buy_bps": 0.0,
        "stamp_duty_sell_bps": 5.0,
        "lot_size": 100,
    }
    lot_size = int(rules.get("lot_size") or 100)
    eff_shares = int(shares) if (shares and int(shares) > 0) else lot_size

    risk_veto = bool(verdict.get("risk_veto", False))
    veto_reasons = list(verdict.get("veto_reasons") or [])
    if direction_u == "LONG" and risk_veto:
        regime_check = {
            "status": "FAIL",
            "risk_veto": True,
            "veto_reasons": veto_reasons,
            "note_cn": f"系统风控官已否决做多（{'；'.join(veto_reasons)}），强行逆势开仓违背大盘先行与风控铁律",
        }
    else:
        regime_check = {
            "status": "PASS",
            "risk_veto": risk_veto,
            "veto_reasons": veto_reasons,
            "note_cn": "大盘与个股风控门禁未触发否决冲突",
        }

    ext_atr = (entry_f - ema20) / max(1e-6, atr14) if direction_u == "LONG" else (ema20 - entry_f) / max(1e-6, atr14)
    sys_entry_zone = list(long_plan.get("entry_zone") or [round(last_close - 0.5 * atr14, p_dec), round(last_close, p_dec)])
    if ext_atr > 1.80:
        anti_chase = {
            "status": "FAIL" if ext_atr > 2.20 else "WARN",
            "extension_from_ema20_atr": round(ext_atr, 2),
            "system_entry_zone": sys_entry_zone,
            "note_cn": (
                f"入场价 {entry_f:.{p_dec}f} 偏离 EMA20({ema20:.{p_dec}f}) 达 {ext_atr:.2f} 倍 ATR14，"
                f"属于高位追涨区间；建议等待缩量回踩 {sys_entry_zone[0]:.{p_dec}f}~{sys_entry_zone[1]:.{p_dec}f} 企稳或放量突破回踩确认后再执行"
            ),
        }
    else:
        anti_chase = {
            "status": "PASS",
            "extension_from_ema20_atr": round(ext_atr, 2),
            "system_entry_zone": sys_entry_zone,
            "note_cn": f"入场价偏离 EMA20 约 {ext_atr:.2f} ATR，未触发追高过热阈值",
        }

    is_round = _is_round_number_price(stop_f, market)
    sys_stop = float(long_plan.get("stop_loss_barrier") or long_plan.get("stop_loss") or round_to_tick(entry_f - 1.5 * atr14, market, asset_type, as_of))
    rec_stop = (
        sys_stop
        if (direction_u == "LONG" and sys_stop < entry_f)
        else round_to_tick(stop_f - 0.35 * atr14, market, asset_type, as_of)
    )
    if _is_round_number_price(rec_stop, market):
        rec_stop = round_to_tick(rec_stop - 2.0 * float(rules.get("tick_size") or 0.01), market, asset_type, as_of)

    if is_round:
        stop_trap = {
            "status": "WARN",
            "is_round_number": True,
            "user_stop": stop_f,
            "recommended_stop": rec_stop,
            "note_cn": (
                f"用户止损位 {stop_f:.{p_dec}f} 位于明显整数关口（极易被日内假跌破流动性扫损打穿）；"
                f"建议将硬止损下移至结构极值下方非整数位 {rec_stop:.{p_dec}f}，并以「5/15分钟放量跌破且反抽失败」作为执行确认"
            ),
        }
    else:
        stop_trap = {
            "status": "PASS",
            "is_round_number": False,
            "user_stop": stop_f,
            "recommended_stop": stop_f,
            "note_cn": f"止损位 {stop_f:.{p_dec}f} 已避开整数关口与密集扫损带",
        }

    notional = eff_shares * entry_f
    cost_detail = compute_round_trip_cost_detail(notional, rules, shares=eff_shares)
    fee_per_share = float(cost_detail["breakeven_price_delta"])
    gross_reward = abs(target_f - entry_f)
    gross_risk = abs(entry_f - stop_f)
    gross_rr = gross_reward / max(1e-6, gross_risk)
    net_reward = max(0.0, gross_reward - fee_per_share)
    net_risk = gross_risk + fee_per_share
    net_rr = net_reward / max(1e-6, net_risk)

    hk_t_violation = market == "HK" and asset_type == "equity" and gross_reward < 0.60
    if net_rr < 1.50 or hk_t_violation:
        hk_msg = (
            f"；港股双边印花税+规费下价差仅 {gross_reward:.2f} HKD（低于 0.60 HKD 保本底线及 1.50 HKD 推荐做T空间）"
            if hk_t_violation
            else ""
        )
        rr_check = {
            "status": "FAIL",
            "gross_rr": round(gross_rr, 2),
            "net_rr_after_cost": round(net_rr, 2),
            "round_trip_cost_amount": cost_detail["total_cost_amount"],
            "effective_cost_rate": cost_detail["effective_cost_rate"],
            "breakeven_price": round(entry_f + fee_per_share if direction_u == "LONG" else entry_f - fee_per_share, p_dec),
            "note_cn": (
                f"扣除双边佣金、印花税与规费（共 {cost_detail['total_cost_amount']:.2f} {meta.get('currency', '')}）后净盈亏比仅 {net_rr:.2f}（低于 1.50 门禁）{hk_msg}"
            ),
        }
    else:
        rr_check = {
            "status": "PASS",
            "gross_rr": round(gross_rr, 2),
            "net_rr_after_cost": round(net_rr, 2),
            "round_trip_cost_amount": cost_detail["total_cost_amount"],
            "effective_cost_rate": cost_detail["effective_cost_rate"],
            "breakeven_price": round(entry_f + fee_per_share if direction_u == "LONG" else entry_f - fee_per_share, p_dec),
            "note_cn": f"扣除全口径摩擦后净盈亏比 {net_rr:.2f} ≥ 1.50，满足正期望门槛",
        }

    q75_list = vr.get("q75") or []
    q90_list = vr.get("q90") or []
    q10_list = vr.get("q10") or []
    q25_list = vr.get("q25") or []
    q75 = float(q75_list[-1]) if q75_list else entry_f + atr14
    q90 = float(q90_list[-1]) if q90_list else entry_f + 2.0 * atr14
    q10 = float(q10_list[-1]) if q10_list else entry_f - 2.0 * atr14
    q25 = float(q25_list[-1]) if q25_list else entry_f - atr14
    if direction_u == "LONG" and target_f > q90:
        reach_check = {
            "status": "WARN",
            "q75_price": q75,
            "q90_price": q90,
            "note_cn": f"目标价 {target_f:.{p_dec}f} 超出未来 15 日正常波动范围上沿（{q90:.{p_dec}f}），建议在 q75（{q75:.{p_dec}f}）附近先分批锁定 TP1",
        }
    else:
        reach_check = {
            "status": "PASS",
            "q25_price": q25,
            "q75_price": q75,
            "q90_price": q90,
            "note_cn": f"目标价位于未来 15 日正常波动范围（{q10:.{p_dec}f}~{q90:.{p_dec}f}）可达范围内",
        }

    statuses = [regime_check["status"], anti_chase["status"], stop_trap["status"], rr_check["status"], reach_check["status"]]
    if regime_check["status"] == "FAIL" or rr_check["status"] == "FAIL" or anti_chase["status"] == "FAIL":
        overall = "REJECTED_BY_RISK_GATE"
    elif "WARN" in statuses:
        overall = "APPROVED_WITH_ADJUSTMENTS"
    else:
        overall = "APPROVED"

    rec_entry = round(min(entry_f, float(sys_entry_zone[1])), p_dec) if (direction_u == "LONG" and anti_chase["status"] != "PASS") else entry_f
    rec_target = round(max(target_f, rec_entry + 1.8 * abs(rec_entry - rec_stop)), p_dec) if (direction_u == "LONG" and rr_check["status"] == "FAIL") else target_f
    be_trigger = round(rec_entry + 0.90 * abs(rec_entry - rec_stop), p_dec)
    be_stop = round(rec_entry + max(fee_per_share, 0.08 * abs(rec_entry - rec_stop)), p_dec)

    playbook = {
        "if_trigger_cn": (
            f"IF 价格回踩至 {rec_entry:.{p_dec}f} 附近缩量企稳（或 15 分钟放量突破确认且不破 VWAP）"
            if direction_u == "LONG"
            else f"IF 价格反弹受阻于 {rec_entry:.{p_dec}f} 且放量跌破支撑"
        ),
        "then_action_cn": f"THEN 按计划分批建仓/加仓 {eff_shares} 股，初始防扫损止损设于 {rec_stop:.{p_dec}f}，第一目标 {rec_target:.{p_dec}f}",
        "invalidation_cn": f"INVALIDATION 若 5/15 分钟放量跌破 {rec_stop:.{p_dec}f} 且反抽失败（或日线收盘跌破），无条件执行风控离场，严禁死扛或向下摊平",
        "ratchet_cn": f"RATCHET 当盘中触及 {be_trigger:.{p_dec}f}（+0.9R）且收盘企稳后，自次日起将防守止损上移至真保本位 {be_stop:.{p_dec}f}，让利润奔跑",
    }

    digest_payload = json.dumps(
        {"symbol": symbol, "as_of": as_of, "direction": direction_u, "entry": entry_f, "stop": stop_f, "target": target_f},
        sort_keys=True,
    )
    idea_id = "idea_" + hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()[:12]

    return {
        "idea_id": idea_id,
        "symbol": symbol,
        "market": market,
        "asset_type": asset_type,
        "as_of": as_of,
        "direction": direction_u,
        "overall_verdict": overall,
        "note": note,
        "user_idea": {"entry": entry_f, "stop": stop_f, "target": target_f, "shares": eff_shares},
        "recommended_adjustments": {"entry": rec_entry, "stop": rec_stop, "target": rec_target, "shares": eff_shares},
        "checks": {
            "regime_and_veto": regime_check,
            "anti_chase": anti_chase,
            "round_number_stop_trap": stop_trap,
            "net_rr_and_friction": rr_check,
            "conformal_reachability": reach_check,
        },
        "if_then_playbook": playbook,
    }


def append_idea_to_ledger(audit_doc: dict[str, Any], ledger_path: Any) -> None:
    """将交易想法审计记录按幂等追加方式写入 JSONL 账本。"""
    import json
    from pathlib import Path

    lp = Path(ledger_path)
    lp.parent.mkdir(parents=True, exist_ok=True)
    existing_ids: set[str] = set()
    if lp.exists():
        for line in lp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    existing_ids.add(str(json.loads(line).get("idea_id")))
                except Exception:  # noqa: BLE001
                    continue
    if audit_doc["idea_id"] not in existing_ids:
        with lp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(audit_doc, ensure_ascii=False) + "\n")

