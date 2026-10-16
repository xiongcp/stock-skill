from __future__ import annotations

import math
from typing import Any

from market_rules import get_market_microstructure_rules, round_to_tick, round_trip_cost_rate


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
    1. 二阶段过滤决策 (meta_labeling_decision)
    2. 左右侧双轨条件与 Triple-Barrier 出场计划 (triple_barrier_plan)
    3. TradingAgents 风格多空双向证伪与独立风控官否决权 (debate_and_veto)
    """
    market = meta["market"]
    asset_type = meta["asset_type"]
    symbol = meta["symbol"]
    as_of_day = meta.get("as_of")
    rules = get_market_microstructure_rules(market, asset_type, symbol, day_str=as_of_day)

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
        entry_low = round_to_tick(min(last_c, r1 * 0.996), market, asset_type, as_of_day)
        entry_high = round_to_tick(max(last_c, r1 * 1.005), market, asset_type, as_of_day)
        stop_loss = round_to_tick(min(s1, last_c - 1.5 * atr14), market, asset_type, as_of_day)
    elif rule_score >= 52.0 and (has_bull_div or has_bull_candle or abs(last_c - s1) <= 0.8 * atr14):
        setup_type = "LEFT_SIDE_PULLBACK"
        setup_label_cn = "左侧支撑回踩观察"
        entry_low = round_to_tick(max(s1 * 0.995, last_c - 0.5 * atr14), market, asset_type, as_of_day)
        entry_high = round_to_tick(last_c, market, asset_type, as_of_day)
        stop_loss = round_to_tick(min(s1 - 0.8 * atr14, last_c - 1.6 * atr14), market, asset_type, as_of_day)
    else:
        setup_type = "WAIT_CONFIRMATION"
        setup_label_cn = "区间观察/等待右侧收盘突破"
        entry_low = round_to_tick(s1, market, asset_type, as_of_day)
        entry_high = round_to_tick(r1, market, asset_type, as_of_day)
        stop_loss = round_to_tick(min(s1 - 0.8 * atr14, last_c - 1.5 * atr14), market, asset_type, as_of_day)

    entry_mid = (entry_low + entry_high) / 2.0
    unit_risk = max(atr14 * 0.5, entry_mid - stop_loss)
    tp1 = round_to_tick(entry_mid + 1.5 * unit_risk, market, asset_type, as_of_day)
    tp2 = round_to_tick(max(entry_mid + 2.5 * unit_risk, bull_target_ladder), market, asset_type, as_of_day)

    # Round-trip fee deduction in price units
    fee_rate = round_trip_cost_rate(rules)
    net_reward = max(0.0, (tp1 - entry_mid) - entry_mid * fee_rate)
    net_risk = unit_risk + entry_mid * fee_rate
    rr_ratio = round(net_reward / net_risk, 2) if net_risk > 0 else 0.0

    # Independent Risk Officer Veto checks
    veto_reasons: list[str] = []
    if float(benchmark_info.get("regime_score") or 50.0) < 35.0:
        veto_reasons.append(f"大盘环境评分偏弱 ({benchmark_info.get('regime_score')}<35)，压降个股做多评级")
    if rr_ratio < 1.20:
        veto_reasons.append(f"扣除交易成本后 TP1 净盈亏比不足 ({rr_ratio:.2f} < 1.20)")
    if wedge.get("status") == "below":
        veto_reasons.append("日线收盘处于结构下轨下方（破位状态未修复）")
    if setup_type == "RIGHT_SIDE_BREAKOUT" and rv < 1.15:
        veto_reasons.append(f"上攻突破缺乏成交量配合 (RVOL={rv:.2f} < 1.15)，警惕假突破")

    vetoed = len(veto_reasons) > 0
    if not vetoed and rule_score >= 65.0 and setup_type in ("RIGHT_SIDE_BREAKOUT", "LEFT_SIDE_PULLBACK"):
        action = "LONG_CONFIRMED"
        action_cn = "右侧/结构看多确认"
    elif not vetoed and rule_score >= 52.0:
        action = "WATCH_LONG_SETUP"
        action_cn = "条件观察（待放量/收盘确认）"
    elif rule_score <= 38.0 or wedge.get("status") == "below":
        action = "DEFENSIVE_AVOID"
        action_cn = "防守回避/破位警惕"
    else:
        action = "NEUTRAL_HOLD_WAIT"
        action_cn = "观望等待突破信号"

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
        lot_shares = (raw_shares // lot) * lot
        max_cash_shares = (int(math.floor(float(account_equity) / max(1e-6, raw_c))) // lot) * lot
        final_shares = max(0, min(lot_shares, max_cash_shares))
        position_sizing = {
            "tradable": True,
            "account_equity": round(float(account_equity), 2),
            "max_risk_pct": round(float(max_risk_pct) * 100.0, 2),
            "risk_budget_amount": round(risk_budget, 2),
            "lot_size": lot,
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
    }

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
            "exit_rule": f"日线收盘跌破 {stop_loss:.2f} 无条件离场",
        },
        "right_side_constraints": {
            "required_evidence": f"日线收盘站稳 {r1:.2f} 且 RVOL20 >= 1.35 (当前 {rv:.2f})",
            "false_breakout_rule": f"突破后收盘跌回 {projections['bull']['retest_hold']:.2f} 下方视为假突破",
        },
        "time_stop_rule": f"持仓满 {horizon_bars} 根K线未触及 TP1({tp1:.2f}) 且偏离成本不足 0.5*ATR 时触发时间屏障离场",
        "market_microstructure": rules,
        "position_sizing": position_sizing,
    }

    # Bull vs Bear structured falsification (TradingAgents style)
    bull_case: list[str] = [
        f"结构触发：若日线放量收盘站稳 {r1:.2f}（{projections['breakout_level']['source']}），回踩不破 {projections['bull']['retest_hold']:.2f}，上方空间指向 TP1 {tp1:.2f} / TP2 {tp2:.2f}。",
    ]
    for p in algebraic_patterns:
        if p["direction"] == "BULLISH":
            bull_case.append(f"数学形态证据：{p['name_cn']} ({p['formula_verified']})。")
    if float(benchmark_info.get("relative_strength_vs_index") or 1.0) >= 1.0:
        bull_case.append(
            f"相对强弱：标的相对基准指数 {benchmark_info.get('index_symbol')} 强弱比值为 {benchmark_info.get('relative_strength_vs_index'):.2f}。"
        )

    bear_case: list[str] = [
        f"空头证伪/失效线：若日线收盘跌破 {s1:.2f}（{projections['breakdown_level']['source']}）或跌穿硬止损屏障 {stop_loss:.2f}，多头结构立即失效，下方风险指向 {projections['bear']['target_price']:.2f}。",
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
        "final_verdict_cn": (
            f"【{action_cn}】综合规则分 {rule_score:.1f}/100，当前属于「{setup_label_cn}」；"
            f"关键突破位 {r1:.2f}，关键防守/止损位 {stop_loss:.2f}，TP1 {tp1:.2f}（净盈亏比 {rr_ratio:.2f}）。"
        ),
    }

    return meta_decision, tb_plan, debate
