from __future__ import annotations

import math
from typing import Any

from market_rules import (
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


# 高置信反转计划的几何（预注册冻结项）：入场区 [-0.50, +0.25] ATR，TP1 +0.85 ATR、SL -2.20 ATR，均以入场区中点为锚
HC_ENTRY_ZONE_ATR = (-0.50, 0.25)
HC_TP1_ATR = 0.85
HC_SL_ATR = 2.20
# 暂停输出反转计划的市场与原因（docs/superpowers/specs/2026-10-20-backtest-acceptance-revision.md）：
# 港股在开发区没有超额（D6，第 7 节）；CN、US 在留出集 H1 上未通过预注册验收（D10，第 11 节）。
# 触发时只记录子类型，不给入场计划与任何统计数字；恢复须先通过前向 H2 验收。
HC_PAUSE_REASONS: dict[str, tuple[str, str]] = {
    "CN": ("PAUSED_H1_NOT_PASSED", "留出集 H1 验收未通过"),
    "US": ("PAUSED_H1_NOT_PASSED", "留出集 H1 验收未通过"),
    "HK": ("PAUSED_NO_DEV_EDGE", "开发区回测未显示超额，留出集 H1 也未通过"),
}
HC_PAUSED_MARKETS = frozenset(HC_PAUSE_REASONS)


def detect_high_conviction_rebound(
    market: str,
    *,
    close: float,
    open_: float,
    high: float,
    low: float,
    raw_close: float,
    atr14: float,
    ema20: float,
    rsi14: float,
    rsi14_prev: float | None,
    bench_ret1: float,
    bench_ret5: float,
    bench_bull_bar: bool,
) -> str | None:
    """高置信超跌/大盘共振反转触发器（纯函数）。线上 compute_triple_barrier_plan 与 research 回测共用这一实现。

    口径与开发集校准时一致：DEEP_OVERSOLD_TURN 的"拐头"指 RSI14 高于前一根；
    CN/US 的 BENCH_SYNC_REBOUND 要求大盘当日涨幅 >= 0.3%（港股 >= 0.5%）。返回子类型，未触发返回 None。
    """
    if atr14 <= 0 or close <= 0 or raw_close < 2.0:
        return None
    min_atr_pct = 0.020 if market == "HK" else 0.012
    if atr14 / close < min_atr_pct:
        return None
    rng = high - low
    if rng <= 0 or close <= open_:
        return None
    body_ratio = (close - open_) / rng
    ext_atr = (close - ema20) / atr14
    rsi_turned_up = rsi14_prev is not None and rsi14 > rsi14_prev

    if market == "HK":
        if bench_ret5 <= -0.025 and bench_ret1 >= 0.005 and ext_atr <= -1.0 and body_ratio >= 0.32:
            return "BENCH_SYNC_REBOUND"
        if rsi14 <= 26.0 and rsi_turned_up and body_ratio >= 0.35 and bench_ret1 >= 0.0:
            return "DEEP_OVERSOLD_TURN"
        return None
    if bench_ret5 <= -0.025 and bench_ret1 >= 0.003 and ext_atr <= -1.0 and body_ratio >= 0.32:
        return "BENCH_SYNC_REBOUND"
    if ((rsi14 <= 28.0 and rsi_turned_up) or ext_atr <= -1.85) and body_ratio >= 0.35 and (bench_bull_bar or bench_ret1 >= 0.0):
        return "DEEP_OVERSOLD_TURN"
    return None


def high_conviction_plan_levels(
    close: float,
    atr14: float,
    market: str,
    asset_type: str,
    as_of_day: str | None,
    tp_atr: float = HC_TP1_ATR,
    sl_atr: float = HC_SL_ATR,
) -> tuple[float, float, float, float]:
    """高置信反转计划的固定价位 (entry_low, entry_high, take_profit_1, stop_loss)，信号日确定后不随成交价移动。

    tp_atr / sl_atr 只供研究回测的几何网格使用，线上一律取默认常量。
    """
    entry_low = round_to_tick(max(0.01, close + HC_ENTRY_ZONE_ATR[0] * atr14), market, asset_type, as_of_day)
    entry_high = round_to_tick(close + HC_ENTRY_ZONE_ATR[1] * atr14, market, asset_type, as_of_day)
    entry_mid = 0.5 * (entry_low + entry_high)
    tp1 = round_to_tick(entry_mid + tp_atr * atr14, market, asset_type, as_of_day)
    sl = round_to_tick(max(0.01, entry_mid - sl_atr * atr14), market, asset_type, as_of_day)
    return entry_low, entry_high, tp1, sl


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

    # L2 历史条件频率守门（经验 Meta-Labeling）：若该标的自身历史上攻先到目标频率 < 30% 且显著低于失效或下探频率，将确认做多降级为观察
    bull_hf = (projections.get("bull") or {}).get("historical_frequency") or {}
    bear_hf = (projections.get("bear") or {}).get("historical_frequency") or {}
    b_tgt = min(_num_or(bull_hf, "target_freq", 0.45), _num_or(bull_hf, "raw_target_freq", 0.45))
    b_inv = max(_num_or(bull_hf, "invalidation_freq", 0.40), _num_or(bull_hf, "raw_invalidation_freq", 0.40))
    dn_tgt = max(_num_or(bear_hf, "target_freq", 0.40), _num_or(bear_hf, "raw_target_freq", 0.40))
    if action == "LONG_CONFIRMED" and b_tgt < 0.30 and (b_inv > b_tgt + 0.12 or dn_tgt > b_tgt + 0.12):
        action = "WATCH_LONG_SETUP"
        action_cn = "条件观察（待放量/收盘确认）"

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
        lot_shares = round_shares_to_lot(raw_shares, rules)
        max_cash_shares = round_shares_to_lot(
            int(math.floor(float(account_equity) / max(1e-6, entry_high_raw * (1.0 + fee_rate)))), rules
        )
        final_shares = max(0, min(lot_shares, max_cash_shares))
        # 风控否决或评级不是做多确认/条件观察时不给股数：股数本身就是买入建议，与"否决时只给观察位"冲突
        long_allowed = not vetoed and action in ("LONG_CONFIRMED", "WATCH_LONG_SETUP")
        position_sizing = {
            "tradable": True,
            "account_equity": round(float(account_equity), 2),
            "max_risk_pct": round(float(max_risk_pct) * 100.0, 2),
            "risk_budget_amount": round(risk_budget, 2),
            "lot_size": lot,
            "min_order_shares": int(rules.get("min_order_shares", lot)),
            "lot_step": int(rules.get("lot_step", lot)),
            "sizing_basis": "ENTRY_HIGH_WORST_FILL",
            "unit_risk_per_share": round(worst_unit_risk, 4),
            "recommended_shares": final_shares if long_allowed else None,
            "estimated_notional": round(final_shares * entry_high_raw, 2) if long_allowed else None,
            "max_loss_at_stop": round(final_shares * worst_unit_risk, 2) if long_allowed else None,
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
            "close_in_range": round(close_in_range, 4),
            "upper_wick_ratio": round(upper_wick_ratio, 4),
            "body_to_range": round(body_to_range, 4),
            "effort_vs_result": round(effort_vs_result, 4),
            "warnings_cn": anti_quant_warnings,
        },
    }

    # 高置信超跌/大盘共振反转层：触发条件与计划价位由共享纯函数给出，研究回测调用同一实现；目前三个市场均暂停输出计划
    hc_subtype: str | None = None
    if not benchmark_info.get("benchmark_missing"):
        hc_subtype = detect_high_conviction_rebound(
            market,
            close=last_c,
            open_=last_o,
            high=last_h,
            low=last_l,
            raw_close=float(last_bar.get("raw_close", last_c)),
            atr14=atr14,
            ema20=_num_or(ind_snapshot["trend"], "ema20", last_c),
            rsi14=rsi14,
            rsi14_prev=ind_snapshot["momentum"].get("rsi14_prev"),
            bench_ret1=_num_or(benchmark_info, "bench_ret1", 0.0),
            bench_ret5=_num_or(benchmark_info, "bench_ret5", 0.0),
            bench_bull_bar=bool(benchmark_info.get("bench_bull_bar", False)),
        )

    # 先大盘、后个股：大盘极弱（BENCHMARK_WEAK）已触发风控否决时，不得再给出反转入场计划与校准概率
    hc_suppressed_by_veto = hc_subtype is not None and "BENCHMARK_WEAK" in veto_codes
    hc_suppressed_subtype = hc_subtype if hc_suppressed_by_veto else None
    if hc_suppressed_by_veto:
        hc_subtype = None
    hc_paused_subtype = hc_subtype if market in HC_PAUSED_MARKETS else None
    if hc_paused_subtype is not None:
        hc_subtype = None

    hc_entry_low, hc_entry_high, hc_tp1, hc_sl = high_conviction_plan_levels(last_c, atr14, market, asset_type, as_of_day)
    meta_decision["high_conviction_rebound"] = hc_subtype is not None
    meta_decision["high_conviction_subtype"] = hc_subtype
    meta_decision["high_conviction_suppressed_by_veto"] = hc_suppressed_by_veto
    meta_decision["high_conviction_suppressed_subtype"] = hc_suppressed_subtype
    meta_decision["high_conviction_paused_subtype"] = hc_paused_subtype
    pause_method, pause_reason = HC_PAUSE_REASONS.get(market, ("", ""))
    # 反转层没有任何通过留出集验收的统计量，不输出概率或命中率数字（方案 D4=b、D10=a）
    meta_decision["decision_probability"] = {
        "available": False,
        "method": (
            "SUPPRESSED_BY_BENCHMARK_VETO"
            if hc_suppressed_by_veto
            else pause_method
            if hc_paused_subtype is not None
            else "NO_VALIDATED_STATISTIC"
            if hc_subtype is not None
            else "NOT_TRIGGERED"
        ),
        "event_definition": (
            "反转条件已触发，但大盘环境评分偏弱触发风控否决"
            if hc_suppressed_by_veto
            else f"反转条件已触发，但{pause_reason}，暂停输出反转计划与统计数字"
            if hc_paused_subtype is not None
            else "反转条件已触发，但没有通过留出集验收的统计量，不输出数字"
            if hc_subtype is not None
            else "未触发高置信超跌/大盘共振反转条件"
        ),
        "validation": "docs/superpowers/specs/2026-10-20-backtest-acceptance-revision.md 第 11 节：留出集 H1 未通过",
        "as_of": as_of_day,
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
        "high_conviction_plan": {
            "triggered": hc_subtype is not None,
            "subtype": hc_subtype,
            "entry_zone": [hc_entry_low, hc_entry_high],
            "take_profit_1": hc_tp1,
            "stop_loss_barrier": hc_sl,
            "time_barrier_bars": horizon_bars,
            "trailing_stop": False,
            "limit_order_sessions": 5,
            "rule_cn": (
                f"高置信超跌/大盘共振反转计划（{hc_subtype}）：5日内于 {hc_entry_low:.{p_dec}f}~{hc_entry_high:.{p_dec}f} 限价入场，"
                f"均值回归修复目标 TP1 {hc_tp1:.{p_dec}f}（+{HC_TP1_ATR:.2f}*ATR），结构崩溃止损 {hc_sl:.{p_dec}f}（-{HC_SL_ATR:.2f}*ATR），时间屏障 {horizon_bars} 根K线"
                if hc_subtype is not None
                else (
                    "反转条件已触发，但大盘环境评分偏弱触发风控否决，不给出反转入场计划"
                    if hc_suppressed_by_veto
                    else f"反转条件已触发（{hc_paused_subtype}），但{pause_reason}，暂不给出反转入场计划"
                    if hc_paused_subtype is not None
                    else "当前截面未触发高置信超跌/大盘共振反转条件"
                )
            ),
        },
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
