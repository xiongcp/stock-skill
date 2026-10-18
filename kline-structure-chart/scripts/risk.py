from __future__ import annotations

import math
from typing import Any

from market_rules import (
    get_market_microstructure_rules,
    price_decimals,
    round_shares_to_lot,
    round_to_tick,
    round_trip_cost_rate,
)


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
    )

    last_bar = bars[-1]
    last_c = float(last_bar["close"])
    raw_c = float(last_bar.get("raw_close", last_c))
    atr14 = float(ind_snapshot["volatility"].get("atr14") or (last_c * 0.018))
    atr14 = max(last_c * 0.005, atr14)
    rv = float(ind_snapshot["momentum"].get("rvol20") or 1.0)
    rule_score = float(mtf_score["rule_score"])

    r1 = float(projections["breakout_level"]["price"])
    s1 = float(projections["breakdown_level"]["price"])
    bull_target_ladder = float(projections["bull"]["target_price"])

    # Determine setup type: RIGHT_SIDE_BREAKOUT vs LEFT_SIDE_SUPPORT_RETEST vs ABSTAIN_WAIT
    has_bull_div = any(p["pattern"] == "RSI_BULLISH_DIVERGENCE" for p in algebraic_patterns)
    has_bull_candle = any(p["category"] == "candlestick" and p["direction"] == "BULLISH" for p in algebraic_patterns)

    if wedge.get("status") == "above" or (last_c >= r1 and rv >= 1.35):
        setup_type = "RIGHT_SIDE_BREAKOUT"
        setup_label_cn = "右侧顺势突破确认"
        if last_c >= r1:
            entry_low = round_to_tick(min(last_c, r1 * 0.996), market, asset_type, as_of_day)
            entry_high = round_to_tick(max(last_c, r1 * 1.005), market, asset_type, as_of_day)
        else:
            entry_low = round_to_tick(max(s1, last_c - 0.35 * atr14), market, asset_type, as_of_day)
            entry_high = round_to_tick(min(r1, last_c + 0.30 * atr14), market, asset_type, as_of_day)
        stop_loss = round_to_tick(min(s1, max(s1 - 0.25 * atr14, last_c - 1.35 * atr14)), market, asset_type, as_of_day)
    elif rule_score >= 52.0 and (has_bull_div or has_bull_candle or abs(last_c - s1) <= 0.8 * atr14):
        setup_type = "LEFT_SIDE_PULLBACK"
        setup_label_cn = "左侧支撑回踩观察"
        entry_low = round_to_tick(max(s1 * 0.995, last_c - 0.45 * atr14), market, asset_type, as_of_day)
        entry_high = round_to_tick(min(r1, last_c + 0.20 * atr14), market, asset_type, as_of_day)
        stop_loss = round_to_tick(min(s1 - 0.15 * atr14, max(s1 - 0.50 * atr14, last_c - 1.45 * atr14)), market, asset_type, as_of_day)
    else:
        setup_type = "WAIT_CONFIRMATION"
        setup_label_cn = "区间观察/等待右侧收盘突破"
        entry_low = round_to_tick(max(s1, last_c - 0.40 * atr14), market, asset_type, as_of_day)
        entry_high = round_to_tick(min(r1, last_c + 0.35 * atr14), market, asset_type, as_of_day)
        stop_loss = round_to_tick(min(s1 - 0.15 * atr14, max(s1 - 0.50 * atr14, last_c - 1.40 * atr14)), market, asset_type, as_of_day)
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
    elif float(benchmark_info.get("regime_score") or 50.0) < 35.0:
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

    vetoed = len(veto_reasons) > 0
    ema20 = float(ind_snapshot["trend"].get("ema20") or last_c)
    rsi14 = float(ind_snapshot["momentum"].get("rsi14") or 50.0)
    ext_atr = (last_c - ema20) / atr14
    overextended = (ext_atr > 2.0) or (rsi14 >= 70.0)
    tf_aligned = mtf_score.get("timeframes_aligned") or {}
    scoring_regime = tf_aligned.get("scoring_regime", "MOMENTUM_TREND")
    w_pos = float(tf_aligned.get("weekly_60w_position_pct") or 50.0)

    if scoring_regime == "MEAN_REVERSION_ADAPTED":
        if ext_atr > 0.35 or w_pos > 78.0 or setup_type == "RIGHT_SIDE_BREAKOUT":
            overextended = True
        elif not vetoed and ext_atr <= 0.15 and wedge.get("status") != "below":
            setup_type = "LEFT_SIDE_PULLBACK"
            setup_label_cn = "左侧支撑回踩观察"

    if not vetoed and not overextended and rule_score >= 65.0 and setup_type in ("RIGHT_SIDE_BREAKOUT", "LEFT_SIDE_PULLBACK"):
        action = "LONG_CONFIRMED"
        action_cn = "右侧/结构看多确认"
    elif not vetoed and not overextended and rule_score >= 52.0:
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
    b_tgt = float(bull_hf.get("target_freq") or 0.45)
    b_inv = float(bull_hf.get("invalidation_freq") or 0.40)
    dn_tgt = float(bear_hf.get("target_freq") or 0.40)
    if action == "LONG_CONFIRMED" and b_tgt < 0.30 and (b_inv > b_tgt + 0.12 or dn_tgt > b_tgt + 0.12):
        action = "WATCH_LONG_SETUP"
        action_cn = "条件观察（待放量/收盘确认）"

    # Position sizing calculation
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
            "unit_risk_per_share": round(unit_risk, 3),
            "reason": "未提供账户净值 (account_equity)，仅输出价格屏障与每股风险，不编造建议股数。",
        }
    else:
        risk_budget = float(account_equity) * float(max_risk_pct)
        adj_factor = float(last_bar.get("adj_factor", 1.0)) or 1.0
        raw_unit_risk = net_risk / adj_factor
        raw_shares = int(math.floor(risk_budget / max(1e-6, raw_unit_risk)))
        lot = int(rules["lot_size"])
        lot_shares = round_shares_to_lot(raw_shares, rules)
        max_cash_shares = round_shares_to_lot(int(math.floor(float(account_equity) / max(1e-6, raw_c))), rules)
        final_shares = max(0, min(lot_shares, max_cash_shares))
        position_sizing = {
            "tradable": True,
            "account_equity": round(float(account_equity), 2),
            "max_risk_pct": round(float(max_risk_pct) * 100.0, 2),
            "risk_budget_amount": round(risk_budget, 2),
            "lot_size": lot,
            "min_order_shares": int(rules.get("min_order_shares", lot)),
            "lot_step": int(rules.get("lot_step", lot)),
            "recommended_shares": final_shares,
            "estimated_notional": round(final_shares * raw_c, 2),
            "settlement_rule": rules["settlement"],
        }

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
    }

    p_dec = price_decimals(last_c, market, asset_type, as_of_day)
    retest_hold = float(projections["bull"]["retest_hold"])
    bear_target = float(projections["bear"]["target_price"])
    holder_defense = _holder_defense_plan(market, asset_type, as_of_day, p_dec, projections, stop_loss, s1, r1, bear_target)
    short_plan = _short_plan(
        market, asset_type, as_of_day, p_dec, projections, s1, r1, bear_target, atr14, fee_rate, benchmark_info
    )

    tb_plan = {
        "entry_zone": [entry_low, entry_high],
        "stop_loss_barrier": stop_loss,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "time_barrier_bars": horizon_bars,
        "risk_reward_ratio": rr_ratio,
        "left_side_constraints": {
            "required_evidence": "缩量企稳 + RSI 底背离或下轨支撑不破",
            "invalidation_level": stop_loss,
            "exit_rule": f"日线收盘跌破 {stop_loss:.{p_dec}f} 无条件离场",
        },
        "right_side_constraints": {
            "required_evidence": f"日线收盘站稳 {r1:.{p_dec}f} 且 RVOL20 >= 1.35 (当前 {rv:.2f})",
            "false_breakout_rule": f"突破后收盘跌回 {retest_hold:.{p_dec}f} 下方视为假突破",
        },
        "time_stop_rule": f"持仓满 {horizon_bars} 根K线未触及 TP1({tp1:.{p_dec}f}) 且偏离成本不足 0.5*ATR 时触发时间屏障离场",
        "market_microstructure": rules,
        "position_sizing": position_sizing,
        "holder_defense_plan": holder_defense,
        "short_plan": short_plan,
    }

    # Bull vs Bear structured falsification (TradingAgents style)
    bull_case: list[str] = [
        f"结构触发：若日线放量收盘站稳 {r1:.{p_dec}f}（{projections['breakout_level']['source']}），回踩不破 {retest_hold:.{p_dec}f}，上方空间指向 TP1 {tp1:.{p_dec}f} / TP2 {tp2:.{p_dec}f}。",
    ]
    for p in algebraic_patterns:
        if p["direction"] == "BULLISH":
            bull_case.append(f"数学形态证据：{p['name_cn']} ({p['formula_verified']})。")
    if float(benchmark_info.get("relative_strength_vs_index") or 1.0) >= 1.0:
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
            f"日线收盘跌破 {exit_level:.{p_dec}f}（硬止损屏障）离场",
        ]
    else:
        rules = [f"日线收盘跌破 {reduce_level:.{p_dec}f}（{src}）离场"]
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
    if float(benchmark_info.get("regime_score") or 50.0) >= 65.0:
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
