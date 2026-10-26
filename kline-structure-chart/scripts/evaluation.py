from __future__ import annotations

import bisect
import math
import random
import re
from typing import Any

from forecast import _local_sigma, compute_conformal_forecast
from indicators import atr_series, compute_all_indicators
from market_rules import (
    calendar_covers,
    count_missing_trading_sessions,
    daily_limit_prices,
    get_daily_price_limit_pct,
    get_market_microstructure_rules,
    rescale_bars_as_of,
    round_to_tick,
    round_trip_cost_rate,
    strip_delisting_marks,
    tick_size_for,
)
from patterns import (
    causal_nadaraya_watson,
    causal_swing_pivots,
    detect_candlestick_patterns,
    detect_divergences,
    detect_geometric_patterns,
    detect_smc_structures,
)
from risk import compute_triple_barrier_plan
from strategies import (
    classify_market_regime,
    compute_multi_timeframe_score,
    evaluate_five_core_strategies,
)
from structure import HISTORY_BARS, STRUCT_DAYS, compute_structure_layer


def run_causal_replay_audit(bars: list[dict[str, Any]], check_steps: int = 8) -> dict[str, Any]:
    """按生产配置验证因果无前视不变性：
    1. 单边因果 Nadaraya-Watson 平滑在追加未来 K 线后绝不改变历史值；
    2. 在生产固定结构窗口（HISTORY_BARS / STRUCT_DAYS）与同一阈值下，截至 t_cut 已确认的拐点与 SMC 缺口在追加未来 K 线后不改变；
    3. 蜡烛形态检测在 t_cut 当日收盘确认后，不随未来 K 线追加而变化；
    4. 正常波动范围（split-conformal）在截取 bars[:cut] 时与完整序列上按同一截止日计算的结果完全一致。
    """
    n = len(bars)
    if n < 160:
        raise ValueError("Need >= 160 bars for causal replay audit")
    full_closes = [float(b["close"]) for b in bars]
    full_nw = causal_nadaraya_watson(full_closes, bandwidth=3.0)
    full_atr = atr_series(bars, 14)
    full_ind = compute_all_indicators(bars)
    full_cdl = {
        (p["pattern"], p["observed_at"]): p["formula_verified"]
        for p in detect_candlestick_patterns(bars, full_atr, full_ind["series"]["rvol20"], lookback=n, max_items=None)
    }

    mismatches: list[str] = []
    checked = 0
    step = max(10, (n - 140) // check_steps)
    meta_stub = {"market": "CN", "asset_type": "index", "symbol": "AUDIT.SH"}
    for cut in range(140, n - 5, step):
        sub = bars[:cut]
        cut_day = str(sub[-1]["day"])
        ext_corrupted = [dict(b) for b in sub] + [
            {
                **b,
                "open": float(b["open"]) * 5.0,
                "high": float(b["high"]) * 5.5,
                "low": float(b["low"]) * 4.5,
                "close": float(b["close"]) * 5.0,
                "volume": float(b.get("volume") or 0.0) * 10.0,
            }
            for b in bars[cut : min(n, cut + 25)]
        ]
        checked += 1
        # 1. 因果核平滑在追加极端未来 K 线后绝不改变历史值
        sub_nw = causal_nadaraya_watson([float(b["close"]) for b in sub], bandwidth=3.0)
        ext_nw = causal_nadaraya_watson([float(b["close"]) for b in ext_corrupted], bandwidth=3.0)
        if abs(sub_nw[-1] - full_nw[cut - 1]) > 1e-12 or abs(sub_nw[-1] - ext_nw[cut - 1]) > 1e-12:
            mismatches.append(f"NW kernel mutated at cut={cut}")

        # 2. 生产结构层 + 同一自适应阈值下的因果拐点与 SMC FVG
        layer_sub = compute_structure_layer(sub, None, 120)
        thr_sub = layer_sub["zz_thr"]
        sub_hist = sub[-HISTORY_BARS:]
        ext_hist = ext_corrupted[max(0, cut - HISTORY_BARS) :]
        sub_pivs = [p for p in causal_swing_pivots(sub_hist, thr_sub) if p["confirmed"]]
        ext_piv_map = {
            (p["day"], p["kind"]): (p["price"], p["confirmed_at_day"])
            for p in causal_swing_pivots(ext_hist, thr_sub)
            if p["confirmed"] and str(p.get("confirmed_at_day") or p["day"]) <= cut_day
        }
        for sp in sub_pivs:
            key = (sp["day"], sp["kind"])
            if key not in ext_piv_map or abs(ext_piv_map[key][0] - sp["price"]) > 1e-6:
                mismatches.append(f"Pivot mutated at cut={cut}: {sp}")

        sub_ind = compute_all_indicators(sub)
        ext_ind = compute_all_indicators(ext_corrupted)
        if abs((sub_ind["series"]["atr14"][-1] or 0.0) - (ext_ind["series"]["atr14"][cut - 1] or 0.0)) > 1e-9:
            mismatches.append(f"ATR14 mutated at cut={cut}")
        sub_smc = detect_smc_structures(sub[-STRUCT_DAYS:], causal_swing_pivots(sub[-STRUCT_DAYS:], thr_sub), sub_ind["series"]["atr14"][-STRUCT_DAYS:])
        for brk in sub_smc["structure_breaks"]:
            if brk["day"] > cut_day or brk["pivot_day"] > brk["day"]:
                mismatches.append(f"Future BOS leaked at cut={cut}: {brk}")

        # 3. 蜡烛形态在追加极端未来 K 线后无前视变化
        ext_tail_len = len(ext_corrupted) - cut
        sub_cdl = detect_candlestick_patterns(
            sub, sub_ind["series"]["atr14"], sub_ind["series"]["rvol20"], lookback=40, max_items=None
        )
        ext_cdl = {
            (p["pattern"], p["observed_at"]): p["formula_verified"]
            for p in detect_candlestick_patterns(
                ext_corrupted,
                ext_ind["series"]["atr14"],
                ext_ind["series"]["rvol20"],
                lookback=40 + ext_tail_len,
                max_items=None,
            )
            if str(p["observed_at"]) <= cut_day
        }
        for pat in sub_cdl:
            pkey = (pat["pattern"], pat["observed_at"])
            if pkey not in full_cdl or pkey not in ext_cdl or ext_cdl[pkey] != pat["formula_verified"]:
                mismatches.append(f"Candlestick pattern mutated at cut={cut}: {pkey}")

        # 4. 正常波动范围的波动率与非一致性分数仅依赖截至 cut 的已收盘 K 线（不受 cut 之后极端未来价格影响）
        fc_a = compute_conformal_forecast(sub, meta=meta_stub, horizon_bars=15, include_audit=False)
        sub_closes = [float(b["close"]) for b in sub]
        ext_closes = [float(b["close"]) for b in ext_corrupted]
        sub_lrets = [math.log(sub_closes[i] / sub_closes[i - 1]) for i in range(1, len(sub_closes))]
        ext_lrets = [math.log(ext_closes[i] / ext_closes[i - 1]) for i in range(1, len(ext_closes))]
        if abs(_local_sigma(sub_lrets, cut - 1) - _local_sigma(ext_lrets, cut - 1)) > 1e-12:
            mismatches.append(f"Conformal sigma leaked future bars at cut={cut}")
        sub_mature_scores = [
            abs(sub_closes[i + 15] / sub_closes[i] - 1.0) / (_local_sigma(sub_lrets, i) * math.sqrt(15))
            for i in range(60, cut - 15)
        ]
        ext_mature_scores = [
            abs(ext_closes[i + 15] / ext_closes[i] - 1.0) / (_local_sigma(ext_lrets, i) * math.sqrt(15))
            for i in range(60, cut - 15)
        ]
        if sub_mature_scores != ext_mature_scores or not all(
            fc_a["q10"][k] <= fc_a["base_median_q50"][k] <= fc_a["q90"][k] for k in range(len(fc_a["q10"]))
        ):
            mismatches.append(f"Conformal mature scores mutated at cut={cut}")

        # 5. Point-in-Time 复权回放：注入 cut 之后的未来 1拆5 除权因子，验证 rescale_bars_as_of 完全消除未来除权泄漏
        sub_pit = rescale_bars_as_of(sub, cut - 1)
        ext_split_leaked = [
            {
                **b,
                "raw_open": float(b.get("raw_open", b["open"])),
                "raw_high": float(b.get("raw_high", b["high"])),
                "raw_low": float(b.get("raw_low", b["low"])),
                "raw_close": float(b.get("raw_close", b["close"])),
                "adj_factor": float(b.get("adj_factor", 1.0)) * 0.2,
                "open": round(float(b["open"]) * 0.2, 4),
                "high": round(float(b["high"]) * 0.2, 4),
                "low": round(float(b["low"]) * 0.2, 4),
                "close": round(float(b["close"]) * 0.2, 4),
            }
            for b in sub
        ] + ext_corrupted[cut:]
        ext_pit_restored = rescale_bars_as_of(ext_split_leaked, cut - 1)[:cut]
        if any(abs(float(a["close"]) - float(b["close"])) > 1e-6 for a, b in zip(sub_pit, ext_pit_restored)):
            mismatches.append(f"PIT rescaling leaked future corporate action at cut={cut}")

        # 6. 策略评分与三重屏障风控决策：验证指标快照、多周期规则分、风控否决与入场/止损/止盈点位零前视
        sview_sub = layer_sub["sview"]
        sview_len = len(sview_sub)
        piv_sview = causal_swing_pivots(sview_sub, thr_sub)
        geo_sub = detect_geometric_patterns(
            sview_sub, piv_sview, sub_ind["series"]["atr14"][-sview_len:], sub_ind["series"]["rvol20"][-sview_len:]
        )
        div_sub = detect_divergences(
            sview_sub, piv_sview, sub_ind["series"]["rsi14"][-sview_len:], sub_ind["series"]["atr14"][-sview_len:]
        )
        pats_sub = sub_cdl + geo_sub + div_sub
        wedge_sub = {k: v for k, v in layer_sub["wedge"].items() if not k.startswith("_")}
        reg_sub = classify_market_regime(sview_sub, sub_ind["snapshot"])
        strat_sub = evaluate_five_core_strategies(sview_sub, sub_ind, reg_sub)
        bench_stub = {"index_symbol": "AUDIT.SH", "regime": reg_sub["regime"], "regime_score": reg_sub["regime_score"], "relative_strength_vs_index": 1.0, "benchmark_missing": False}
        mtf_sub = compute_multi_timeframe_score(sub, sub_ind, wedge_sub, pats_sub, sub_smc, strat_sub, bench_stub)
        dec_sub, plan_sub, _ = compute_triple_barrier_plan(
            sview_sub,
            meta={**meta_stub, "as_of": cut_day},
            ind_snapshot=sub_ind["snapshot"],
            wedge=wedge_sub,
            ladder=layer_sub["ladder"],
            projections=layer_sub["projections"],
            mtf_score=mtf_sub,
            algebraic_patterns=pats_sub,
            benchmark_info=bench_stub,
        )
        layer_pit = compute_structure_layer(ext_pit_restored, None, 120)
        ind_pit = compute_all_indicators(ext_pit_restored)
        reg_pit = classify_market_regime(layer_pit["sview"], ind_pit["snapshot"])
        strat_pit = evaluate_five_core_strategies(layer_pit["sview"], ind_pit, reg_pit)
        mtf_pit = compute_multi_timeframe_score(ext_pit_restored, ind_pit, {k: v for k, v in layer_pit["wedge"].items() if not k.startswith("_")}, pats_sub, sub_smc, strat_pit, bench_stub)
        dec_pit, plan_pit, _ = compute_triple_barrier_plan(
            layer_pit["sview"],
            meta={**meta_stub, "as_of": cut_day},
            ind_snapshot=ind_pit["snapshot"],
            wedge={k: v for k, v in layer_pit["wedge"].items() if not k.startswith("_")},
            ladder=layer_pit["ladder"],
            projections=layer_pit["projections"],
            mtf_score=mtf_pit,
            algebraic_patterns=pats_sub,
            benchmark_info=bench_stub,
        )
        if (
            dec_sub["action"] != dec_pit["action"]
            or dec_sub["veto_codes"] != dec_pit["veto_codes"]
            or abs(float(mtf_sub["rule_score"]) - float(mtf_pit["rule_score"])) > 1e-6
            or plan_sub["entry_zone"] != plan_pit["entry_zone"]
            or abs(float(plan_sub["stop_loss_barrier"]) - float(plan_pit["stop_loss_barrier"])) > 1e-6
        ):
            mismatches.append(f"Triple-barrier decision mutated at cut={cut}")
        if (
            layer_sub["scenario"] != layer_pit["scenario"]
            or layer_sub["wave_counts"] != layer_pit["wave_counts"]
            or layer_sub["projections"].get("scenarios") != layer_pit["projections"].get("scenarios")
            or layer_sub["projections"].get("impulse_wave") != layer_pit["projections"].get("impulse_wave")
        ):
            mismatches.append(f"Structure or scenarios mutated at cut={cut}")

    return {
        "checked_cutoffs": checked,
        "modules_audited": [
            "nadaraya_watson",
            "structure_layer_pivots",
            "structure_and_scenarios",
            "candlestick_patterns",
            "smc_structures",
            "conformal_forecast",
            "pit_rescaling",
            "triple_barrier_decision",
        ],
        "mismatches_count": len(mismatches),
        "mismatches": mismatches[:10],
        "passed": len(mismatches) == 0,
    }


def label_triple_barrier_event(
    bars: list[dict[str, Any]],
    entry_idx: int,
    tp_price: float,
    sl_price: float,
    horizon_bars: int = 15,
    market: str = "CN",
    asset_type: str = "equity",
    symbol: str = "600519.SH",
    is_st: bool = False,
    is_ipo_no_limit: bool = False,
    trailing_stop: bool = True,
    trigger_r: float = 0.90,
    confirm_close_r: float = 0.30,
    lock_r: float = 0.08,
    name: str = "",
    entry_price_override: float | None = None,
) -> dict[str, Any]:
    """三重屏障事件标定器（López de Prado, 2018）：
    - 在 signal_idx 收盘触发后，于 entry_idx = signal_idx + 1 开盘（或限价单成交价 entry_price_override）执行入场。
    - 同根 K 线若同时触及止盈与止损，采用保守规则（先判止损 SL_FIRST）。
    - A股 T+1 约束：入场当日 (entry_idx) 不可卖出离场，最早从 entry_idx + 1 起检测出场。
    - 时间屏障：最多持有 horizon_bars 根 K 线（entry_idx .. entry_idx + horizon_bars - 1）。
    - 涨跌停约束：若开盘即涨停无法买入，标记为 LIMIT_UP_NO_FILL。
    """
    n = len(bars)
    if entry_idx >= n:
        return {"tradable": False, "reason": "OUT_OF_RANGE"}

    entry_bar = bars[entry_idx]
    entry_open = float(entry_price_override) if entry_price_override is not None else float(entry_bar["open"])
    if sl_price >= entry_open or tp_price <= entry_open:
        return {
            "tradable": False,
            "reason": "INVALID_PLAN_GEOMETRY",
            "entry_day": entry_bar["day"],
            "note": "止损必须低于入场价、止盈必须高于入场价；拒绝用倒挂屏障产出胜场标签",
        }

    def _limit_prices(idx: int) -> tuple[float, float, float, float] | None:
        """第 idx 根K线在其原始价口径下的 (涨停价, 跌停价, 最小价位, 复权价→原始价系数)。

        复权序列不在价位网格上，先把前收盘换算到当根K线的原始价口径（除权日即除权参考价），再按交易所规则取整。
        """
        if idx <= 0:
            return None
        cur = bars[idx]
        day_limit_pct = get_daily_price_limit_pct(
            market, asset_type, symbol, is_st=is_st, is_ipo_no_limit=is_ipo_no_limit, day_str=cur["day"], name=name
        )
        if day_limit_pct is None:
            return None
        cur_close = float(cur["close"])
        scale = float(cur.get("raw_close", cur_close)) / cur_close if cur_close > 0 else 1.0
        ref = float(bars[idx - 1]["close"]) * scale
        up, down = daily_limit_prices(ref, day_limit_pct, market, asset_type, cur["day"])
        tick = float(tick_size_for(ref, market, asset_type, cur["day"]))
        return up, down, tick, scale

    entry_limits = _limit_prices(entry_idx)
    if entry_limits is not None and entry_open * entry_limits[3] >= entry_limits[0] - 0.5 * entry_limits[2]:
        return {"tradable": False, "reason": "LIMIT_UP_NO_FILL", "entry_day": entry_bar["day"]}

    rules = get_market_microstructure_rules(
        market, asset_type, symbol, is_st=is_st, is_ipo_no_limit=is_ipo_no_limit, day_str=entry_bar["day"], name=name
    )
    start_exit_idx = entry_idx + 1 if not rules["can_intraday_roundtrip"] else entry_idx
    if start_exit_idx >= n:
        # T+1 市场在最后一根 K 线入场时没有可卖出的下一根 K 线；不得退化成 T+0 当日平仓。
        return {"tradable": False, "reason": "INSUFFICIENT_FUTURE_BARS", "entry_day": entry_bar["day"]}
    end_idx = min(n - 1, max(start_exit_idx, entry_idx + max(1, horizon_bars) - 1))

    exit_idx = end_idx
    exit_price = float(bars[end_idx]["close"])
    outcome = "TIME_BARRIER"
    label = 0
    active_sl = sl_price
    unit_risk = max(1e-6, entry_open - sl_price)
    highest_since_entry = entry_open
    if start_exit_idx > entry_idx and float(entry_bar["low"]) > active_sl:
        e_hi = float(entry_bar["high"])
        e_cl = float(entry_bar["close"])
        highest_since_entry = max(highest_since_entry, e_hi)
        if (
            trailing_stop
            and highest_since_entry >= entry_open + trigger_r * unit_risk
            and e_cl >= entry_open + confirm_close_r * unit_risk
        ):
            active_sl = max(active_sl, round_to_tick(entry_open + lock_r * unit_risk, market, asset_type, entry_bar["day"]))

    def _is_limit_down_locked(idx: int) -> bool:
        """一字跌停（最高价 = 最低价 = 跌停价）：全天封死，无法卖出。"""
        limits = _limit_prices(idx)
        if limits is None:
            return False
        _, down, tick, scale = limits
        b_k = bars[idx]
        hi_k, lo_k = float(b_k["high"]), float(b_k["low"])
        return hi_k * scale <= down + 0.5 * tick and lo_k == hi_k

    for k in range(start_exit_idx, end_idx + 1):
        b = bars[k]
        lo, hi, op, cl = float(b["low"]), float(b["high"]), float(b["open"]), float(b["close"])
        # Check limit-down lock on CN equities (cannot sell if locked at limit down)
        if _is_limit_down_locked(k):
            continue

        hit_sl = lo <= active_sl
        hit_tp = hi >= tp_price
        if hit_sl and hit_tp:
            # Conservative same-bar resolution: assume stop-loss hit first
            exit_idx = k
            exit_price = min(op, active_sl)
            outcome = "SL_BARRIER_SAME_BAR_CONSERVATIVE"
            label = 1 if exit_price > entry_open else -1
            break
        if hit_sl:
            exit_idx = k
            exit_price = min(op, active_sl)
            outcome = "TRAILING_PROFIT_STOP" if active_sl > sl_price else "SL_BARRIER"
            label = 1 if exit_price > entry_open else -1
            break
        if hit_tp:
            exit_idx = k
            exit_price = max(op, tp_price)
            outcome = "TP_BARRIER"
            label = 1
            break

        # ATR Trailing Stop update at bar close (Alorse-style dynamic stop ratchet, applied from next bar)
        highest_since_entry = max(highest_since_entry, hi)
        if (
            trailing_stop
            and highest_since_entry >= entry_open + trigger_r * unit_risk
            and cl >= entry_open + confirm_close_r * unit_risk
        ):
            active_sl = max(active_sl, round_to_tick(entry_open + lock_r * unit_risk, market, asset_type, b["day"]))

    exit_rules = get_market_microstructure_rules(
        market, asset_type, symbol, is_st=is_st, is_ipo_no_limit=is_ipo_no_limit, day_str=bars[exit_idx]["day"], name=name
    )
    buy_bps = (
        float(rules.get("commission_bps") or 0.0)
        + float(rules.get("stamp_duty_buy_bps") or 0.0)
        + float(rules.get("regulatory_levies_buy_bps") or 0.0)
    )
    sell_bps = (
        float(exit_rules.get("commission_bps") or 0.0)
        + float(exit_rules.get("stamp_duty_sell_bps") or 0.0)
        + float(exit_rules.get("regulatory_levies_sell_bps") or 0.0)
    )
    fee_rate = (buy_bps + sell_bps + 2.0) / 10000.0  # 含买卖当日佣金、印花税、规费 + 2bps 滑点

    if outcome == "TIME_BARRIER":
        while _is_limit_down_locked(exit_idx) and exit_idx + 1 < n:
            exit_idx += 1
            if not _is_limit_down_locked(exit_idx):
                b_un = bars[exit_idx]
                exit_price = min(float(b_un["open"]), active_sl) if float(b_un["low"]) <= active_sl else float(b_un["close"])
                break
            exit_price = float(bars[exit_idx]["close"])
        exit_rules = get_market_microstructure_rules(
            market, asset_type, symbol, is_st=is_st, is_ipo_no_limit=is_ipo_no_limit, day_str=bars[exit_idx]["day"], name=name
        )
        sell_bps = (
            float(exit_rules.get("commission_bps") or 0.0)
            + float(exit_rules.get("stamp_duty_sell_bps") or 0.0)
            + float(exit_rules.get("regulatory_levies_sell_bps") or 0.0)
        )
        fee_rate = (buy_bps + sell_bps + 2.0) / 10000.0
        if _is_limit_down_locked(exit_idx):
            gross_lock = (exit_price / entry_open) - 1.0
            net_lock = gross_lock - fee_rate
            return {
                "tradable": False,
                "reason": "LOCKED_LIMIT_DOWN_UNTIL_END",
                "outcome": "LOCKED_LIMIT_DOWN_UNTIL_END",
                "barrier_label": -1,
                "entry_idx": entry_idx,
                "entry_day": entry_bar["day"],
                "entry_price": round(entry_open, 4),
                "exit_idx": exit_idx,
                "exit_day": bars[exit_idx]["day"],
                "exit_price": round(exit_price, 4),
                "holding_bars": exit_idx - entry_idx + 1,
                "locked_until_day": bars[exit_idx]["day"],
                "gross_return": round(gross_lock, 5),
                "net_return": round(net_lock, 5),
            }
        label = 1 if exit_price > entry_open else -1

    gross_ret = (exit_price / entry_open) - 1.0
    net_ret = gross_ret - fee_rate

    return {
        "tradable": True,
        "entry_idx": entry_idx,
        "entry_day": entry_bar["day"],
        "entry_price": round(entry_open, 4),
        "exit_idx": exit_idx,
        "exit_day": bars[exit_idx]["day"],
        "exit_price": round(exit_price, 4),
        "holding_bars": exit_idx - entry_idx + 1,
        "outcome": outcome,
        "barrier_label": label,
        "gross_return": round(gross_ret, 5),
        "net_return": round(net_ret, 5),
    }


def _wilson_ci(wins: int, n: int, z: float = 1.96) -> list[float] | None:
    """胜率的 Wilson 95% 区间（样本数 >= 2 时提供）：小样本或全胜/全负时不会像 Bootstrap 那样退化成 [p, p]。"""
    if n < 2:
        return None
    p = wins / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, center - half) * 100.0, 2), round(min(1.0, center + half) * 100.0, 2)]


def _bootstrap_mean_ci(rets: list[float], n_boot: int = 400) -> list[float] | None:
    """单笔均值收益的确定性 Bootstrap 95% 置信区间（样本数 >= 2 时提供）。"""
    m = len(rets)
    if m < 2:
        return None
    rng = random.Random(42 + m)
    mean_rets = sorted(sum(rets[rng.randrange(m)] for _ in range(m)) / m * 100.0 for _ in range(n_boot))
    lo_i = max(0, int(0.025 * n_boot))
    hi_i = min(n_boot - 1, int(0.975 * n_boot))
    return [round(mean_rets[lo_i], 2), round(mean_rets[hi_i], 2)]


def _summarize_returns(rets: list[float], years: float) -> dict[str, Any]:
    if not rets:
        return {
            "trades": 0,
            "cumulative_return_pct": 0.0,
            "win_rate_pct": 0.0,
            "win_rate_95ci_pct": None,
            "mean_return_95ci_pct": None,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": 0.0,
        }
    eq = 1.0
    peak = 1.0
    max_dd = 0.0
    wins = [r for r in rets if r > 0]
    losses = [-r for r in rets if r < 0]
    for r in rets:
        eq *= 1.0 + r
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak)
    mean_r = sum(rets) / len(rets)
    var_r = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1) if len(rets) > 1 else 0.0
    sd_r = math.sqrt(var_r)
    trades_per_year = len(rets) / years if years > 0 else 0.0
    sharpe = (mean_r / sd_r) * math.sqrt(trades_per_year) if sd_r > 0 else 0.0
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (9.99 if wins else 0.0)
    return {
        "trades": len(rets),
        "cumulative_return_pct": round((eq - 1.0) * 100.0, 2),
        "win_rate_pct": round(len(wins) / len(rets) * 100.0, 2),
        "win_rate_95ci_pct": _wilson_ci(len(wins), len(rets)),
        "mean_return_95ci_pct": _bootstrap_mean_ci(rets),
        "sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "profit_factor": round(pf, 2),
    }


def _average_ranks(values: list[float]) -> list[float]:
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


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman 秩相关（并列取平均秩）；样本不足 3 个或任一侧无变化时返回 None。"""
    if len(xs) < 3:
        return None
    rx, ry = _average_ranks(xs), _average_ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx <= 0 or vy <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / math.sqrt(vx * vy)


RULE_SCORE_BUCKETS = ((0.0, 38.0), (38.0, 52.0), (52.0, 65.0), (65.0, 100.01))
MIN_IC_INDEPENDENT_SAMPLES = 20  # 有效独立样本低于此值时，IC 不足以说明规则分有排序能力
MIN_STABLE_ABS_IC = 0.05


def _forward_stats(rets: list[float]) -> dict[str, Any]:
    if not rets:
        return {"decisions": 0, "mean_forward_return_pct": None, "median_forward_return_pct": None, "up_share_pct": None}
    ordered = sorted(rets)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {
        "decisions": len(rets),
        "mean_forward_return_pct": round(sum(rets) / len(rets) * 100.0, 2),
        "median_forward_return_pct": round(median * 100.0, 2),
        "up_share_pct": round(sum(1 for r in rets if r > 0) / len(rets) * 100.0, 1),
    }


def _decision_diagnostics(rows: list[tuple[float, str, float]], horizon_bars: int, step: int) -> dict[str, Any]:
    """决策日诊断：规则分与未来 H 日收益的 Spearman IC、规则分分层与各动作的前瞻收益。

    相邻决策日的结果期重叠，有效独立样本约为 决策数 × 步长 / H；IC 只在有效独立样本 ≥ 20 且 |IC| ≥ 0.05 时才视为有排序能力。
    """
    scores = [r[0] for r in rows]
    rets = [r[2] for r in rows]
    ic = _spearman(scores, rets)
    eff = len(rows) * max(1, step) // max(1, horizon_bars)
    informative = ic is not None and eff >= MIN_IC_INDEPENDENT_SAMPLES and abs(ic) >= MIN_STABLE_ABS_IC
    if ic is None:
        verdict = "样本不足或规则分无变化，无法计算 IC"
    elif eff < MIN_IC_INDEPENDENT_SAMPLES:
        verdict = f"有效独立样本仅约 {eff} 个（< {MIN_IC_INDEPENDENT_SAMPLES}），IC 不稳定，规则分只作描述"
    elif not informative:
        verdict = f"|IC| < {MIN_STABLE_ABS_IC}，规则分在该标的上没有显示排序能力，只作描述"
    elif ic > 0:
        verdict = "规则分越高、未来收益越高（样本内回放统计，非未来保证）"
    else:
        verdict = "规则分与未来收益负相关，规则分在该标的上方向相反，只作描述"
    return {
        "rule_score_ic": {
            "spearman": None if ic is None else round(ic, 3),
            "decisions": len(rows),
            "effective_independent_samples": eff,
            "forward_horizon_bars": horizon_bars,
            "informative": informative,
            "verdict": verdict,
        },
        "rule_score_buckets": [
            {"range": [lo, min(hi, 100.0)], **_forward_stats([r[2] for r in rows if lo <= r[0] < hi])}
            for lo, hi in RULE_SCORE_BUCKETS
        ],
        "action_forward_returns": {
            action: _forward_stats([r[2] for r in rows if r[1] == action])
            for action in ("LONG_CONFIRMED", "WATCH_LONG_SETUP", "NEUTRAL_HOLD_WAIT", "DEFENSIVE_AVOID")
        },
    }


def _causal_is_st_at_cutoff(
    bars: list[dict[str, Any]],
    cut_idx: int,
    market: str,
    symbol: str,
    base_is_st: bool,
) -> bool:
    """因果校验历史截面 cut_idx 时的 ST 状态：
    若当前快照标记为 ST，但在主板 ST 适用 ±5% 涨跌停期间（2026-07-06 前），截至 cut_idx 的近 60 根无除权 K 线内
    出现过单日真实涨跌幅超过 ±5.5%，则证明该标的在 cut_idx 当时处于非 ST（±10%）交易状态，禁止把未来的戴帽状态回填到当时。
    """
    if not base_is_st or market != "CN":
        return False
    cut_day = str(bars[cut_idx]["day"])
    if get_daily_price_limit_pct(market, "equity", symbol, is_st=True, day_str=cut_day) != 0.05:
        return base_is_st
    for k in range(max(1, cut_idx - 59), cut_idx + 1):
        prev_b, cur_b = bars[k - 1], bars[k]
        if abs(float(cur_b.get("adj_factor", 1.0)) - float(prev_b.get("adj_factor", 1.0))) > 1e-9:
            continue
        prev_c = float(prev_b.get("raw_close", prev_b["close"]))
        if prev_c <= 0:
            continue
        hi = float(cur_b.get("raw_high", cur_b["high"]))
        lo = float(cur_b.get("raw_low", cur_b["low"]))
        if max(abs(hi / prev_c - 1.0), abs(lo / prev_c - 1.0)) > 0.055:
            return False
    return True


def _suspended_after(cut_day: str, next_day: str, bench_days: list[str], market: str) -> bool:
    """cut_day 之后标的是否停牌：标的下一根K线晚于基准的下一个交易日即为停牌。

    基准日序列天然包含节假日与台风等临时休市；没有基准时，只在休市表覆盖范围内按交易日历判断，范围外不判停牌。
    """
    k = bisect.bisect_right(bench_days, cut_day)
    if k < len(bench_days):
        return next_day > bench_days[k]
    if calendar_covers(cut_day, market) and calendar_covers(next_day, market):
        return count_missing_trading_sessions(cut_day, next_day, market) > 1
    return False


def run_plan_backtest(
    bundle: dict[str, Any],
    request: dict[str, Any],
    horizon_bars: int = 15,
    step: int = 1,
    decision_window: int = 300,
) -> dict[str, Any]:
    """逐日回放线上真实流水线：在每个决策日 t 只用 t 及之前的数据调用 analyze_bundle，
    对给出 LONG_CONFIRMED / WATCH_LONG_SETUP 的计划，按计划自身的入场区、止损、TP1 与时间屏障在 t+1 开盘撮合。

    所有规则在回测前固定，不从样本学习参数，因此不存在训练集泄漏到评估集的问题；持仓期间不重复开仓。
    """
    from analysis import analyze_bundle  # 延迟导入，避免与 analysis 的模块级依赖成环

    bars = bundle["target"]["bars"]
    inst = bundle["target"]["instrument"]
    market, asset_type, symbol = inst["market"], inst["asset_type"], inst["symbol"]
    # 退市整理期简称（含厂商快照简称）只反映当下状态，回放历史截面时去掉，避免把未来的退市否决回填到过去
    inst_name = strip_delisting_marks(str(inst.get("name") or request.get("name") or ""))
    base_provenance = dict(bundle.get("provenance") or {})
    if (base_provenance.get("instrument_meta") or {}).get("vendor_name"):
        base_provenance["instrument_meta"] = {
            **base_provenance["instrument_meta"],
            "vendor_name": strip_delisting_marks(str(base_provenance["instrument_meta"]["vendor_name"])),
        }
    is_st =bool(inst.get("is_st") or request.get("is_st", False))
    is_ipo_no_limit = bool(request.get("is_ipo_no_limit") or (bundle.get("extra_context") or {}).get("is_ipo_no_limit", False))
    n = len(bars)
    if n < 250 + horizon_bars + 2:
        raise ValueError(f"Need >= {250 + horizon_bars + 2} bars for plan backtest on {symbol}, got {n}")

    start = max(250, n - horizon_bars - 2 - decision_window)
    end = n - horizon_bars - 1
    bench = bundle.get("benchmark")
    bench_days = [str(b["day"]) for b in bench["bars"]] if bench else []
    sub_request_base = {**request, "display_bars": 120, "account_equity": None}
    trades: dict[str, list[dict[str, Any]]] = {"LONG_CONFIRMED": [], "WATCH_LONG_SETUP": []}
    all_trades: list[dict[str, Any]] = []
    actions_seen: dict[str, int] = {}
    veto_counts: dict[str, int] = {}
    decision_rows: list[tuple[float, str, float]] = []  # (rule_score, action, 未来 H 日收益)
    no_fill = 0
    decisions = 0
    busy_until_all = -1  # 合并组合持仓锁（持仓期间照常记录决策用于 IC 与动作前瞻收益）
    busy_until_by_action: dict[str, int] = {"LONG_CONFIRMED": -1, "WATCH_LONG_SETUP": -1}

    t = start
    while t < end:
        cut_day = bars[t]["day"]
        ipo_at_t = bool(is_ipo_no_limit and t < 5)
        st_at_t = _causal_is_st_at_cutoff(bars, t, market, symbol, is_st)
        name_at_t = inst_name if st_at_t else re.sub(r"^\*?S?\*?ST", "", inst_name, flags=re.IGNORECASE)
        inst_at_t = {**inst, "is_st": st_at_t, "name": name_at_t}
        sub_request = {**sub_request_base, "is_st": st_at_t, "name": name_at_t}
        pit_bars = rescale_bars_as_of(bars, t)
        sub_bundle = {
            "schema_version": "6.0.0",
            "market": market,
            "target": {"instrument": inst_at_t, "bars": pit_bars[: t + 1], "intraday": None},
            "benchmark": {**bench, "bars": [b for b in bench["bars"] if b["day"] <= cut_day]} if bench else None,
            # 历史 VIX 不可得，回放中不使用当前 VIX，避免前视
            "extra_context": {"vix": None, "earnings_date": None, "is_st": st_at_t, "is_ipo_no_limit": ipo_at_t},
            "provenance": {**base_provenance, "missing_closed_session": None, "stale_sessions": 0},
        }
        rep = analyze_bundle(sub_bundle, sub_request, include_replay=False, include_forecast=False)
        decisions += 1
        action = rep["meta_labeling_decision"]["action"]
        actions_seen[action] = actions_seen.get(action, 0) + 1
        for code in rep["meta_labeling_decision"].get("veto_codes") or []:
            veto_counts[code] = veto_counts.get(code, 0) + 1
        # 前瞻收益用全历史复权价（含分红送转）计算，只用于事后评估，不进入决策
        decision_rows.append(
            (float(rep["multi_timeframe_score"]["rule_score"]), action, float(bars[t + horizon_bars]["close"]) / float(bars[t]["close"]) - 1.0)
        )
        if action in trades:
            can_open_bucket = t > busy_until_by_action[action]
            can_open_all = t > busy_until_all
            if not can_open_bucket and not can_open_all:
                t += step
                continue
            plan = rep["triple_barrier_plan"]
            entry_low, entry_high = plan["entry_zone"]
            stop_loss = float(plan["stop_loss_barrier"])
            ratchet = plan.get("breakeven_ratchet") or {}
            suspended_next = _suspended_after(cut_day, str(pit_bars[t + 1]["day"]), bench_days, market)
            entry_open = float(pit_bars[t + 1]["open"])
            if not suspended_next and entry_low <= entry_open <= entry_high and entry_open > stop_loss:
                res = label_triple_barrier_event(
                    pit_bars,
                    entry_idx=t + 1,
                    tp_price=float(plan["take_profit_1"]),
                    sl_price=stop_loss,
                    horizon_bars=horizon_bars,
                    market=market,
                    asset_type=asset_type,
                    symbol=symbol,
                    is_st=st_at_t,
                    is_ipo_no_limit=ipo_at_t,
                    trailing_stop=bool(ratchet.get("enabled", True)),
                    trigger_r=float(ratchet.get("trigger_r", 0.90)),
                    confirm_close_r=float(ratchet.get("confirm_close_r", 0.30)),
                    lock_r=float(ratchet.get("lock_r", 0.08)),
                    name=name_at_t,
                )
                if res.get("tradable") or res.get("reason") == "LOCKED_LIMIT_DOWN_UNTIL_END":
                    clean_res = {k: v for k, v in res.items() if k not in ("reason", "locked_until_day")}
                    clean_res["tradable"] = True
                    trade_rec = {
                        **clean_res,
                        "signal_day": cut_day,
                        "action": action,
                        "setup_type": rep["meta_labeling_decision"]["setup_type"],
                        "plan_entry_zone": [entry_low, entry_high],
                        "plan_stop_loss": stop_loss,
                        "plan_take_profit_1": float(plan["take_profit_1"]),
                        "plan_breakeven_ratchet": ratchet,
                    }
                    if can_open_bucket:
                        trades[action].append(trade_rec)
                        busy_until_by_action[action] = int(res["exit_idx"])
                    if can_open_all:
                        all_trades.append(trade_rec)
                        busy_until_all = int(res["exit_idx"])
                elif can_open_all:
                    no_fill += 1
            elif can_open_all:
                no_fill += 1
        t += step

    years = max(1e-6, (end - start) / 252.0)
    total = len(all_trades)
    bh_ret = (float(bars[end]["close"]) / float(bars[start]["close"]) - 1.0) * 100.0

    # 正常波动范围样本外覆盖（split-conformal，与线上同一构造）及中位数对随机游走的误差
    eval_cuts = list(range(start, n - horizon_bars, horizon_bars))
    hit_50 = hit_80 = 0
    q50_err: list[float] = []
    rw_err: list[float] = []
    meta_stub = {
        "market": market,
        "asset_type": asset_type,
        "symbol": symbol,
        "name": inst_name,
        "is_st": is_st,
        "is_ipo_no_limit": is_ipo_no_limit,
    }
    for cut in eval_cuts:
        pit_sub = rescale_bars_as_of(bars[: cut + horizon_bars + 1], cut)
        fc = compute_conformal_forecast(
            pit_sub[: cut + 1],
            meta={**meta_stub, "is_ipo_no_limit": bool(is_ipo_no_limit and cut < 5)},
            horizon_bars=horizon_bars,
            include_audit=False,
        )
        actual = round_to_tick(float(pit_sub[cut + horizon_bars]["close"]), market, asset_type, pit_sub[cut + horizon_bars]["day"])
        base_c = float(pit_sub[cut]["close"])
        b50_lo, b50_hi = fc["conformal_50pct_band"][-1]
        b80_lo, b80_hi = fc["conformal_80pct_band"][-1]
        hit_50 += int(b50_lo - 1e-6 <= actual <= b50_hi + 1e-6)
        hit_80 += int(b80_lo - 1e-6 <= actual <= b80_hi + 1e-6)
        q50_err.append(abs(actual - float(fc["base_median_q50"][-1])) / base_c)
        rw_err.append(abs(actual - base_c) / base_c)
    n_eval = max(1, len(eval_cuts))
    full_fc_audit = compute_conformal_forecast(bars, meta=meta_stub, horizon_bars=horizon_bars)["calibration_audit"]

    return {
        "symbol": symbol,
        "market": market,
        "asset_type": asset_type,
        "horizon_bars": horizon_bars,
        "total_bars": n,
        "method": "LIVE_PLAN_REPLAY_V6",
        "decision_window": {"start_day": bars[start]["day"], "end_day": bars[end]["day"], "decisions": decisions, "step_bars": step},
        "entry_rule": (
            "决策日收盘给出 LONG_CONFIRMED/WATCH_LONG_SETUP 时，次日开盘价落在计划入场区内才成交；"
            "出场 = 盘中触及计划硬止损或 TP1 / 盘中触及 +0.90R 且收盘 ≥ +0.30R 后次日起止损上移至 +0.08R 保本位 / 持仓满期触发时间屏障"
        ),
        "actions_seen": actions_seen,
        "no_fill_outside_entry_zone": no_fill,
        "all_long_signals": _summarize_returns([float(x["net_return"]) for x in all_trades], years),
        "long_confirmed": _summarize_returns([float(x["net_return"]) for x in trades["LONG_CONFIRMED"]], years),
        "watch_long_setup": _summarize_returns([float(x["net_return"]) for x in trades["WATCH_LONG_SETUP"]], years),
        "buy_and_hold_return_pct": round(bh_ret, 2),
        "veto_reason_counts": dict(sorted(veto_counts.items(), key=lambda kv: -kv[1])),
        **_decision_diagnostics(decision_rows, horizon_bars, step),
        "sample_trades": all_trades[-3:],
        "sample_size_warning": (
            f"成交仅 {total} 笔（少于 30），胜率/夏普等统计量置信度很低，仅供参考" if total < 30 else None
        ),
        "conformal_forecast_oos": {
            "evaluated_cutoffs": len(eval_cuts),
            "nominal_50pct_coverage": round(hit_50 / n_eval, 3),
            "nominal_80pct_coverage": round(hit_80 / n_eval, 3),
            "q50_mae_pct": round(sum(q50_err) / n_eval * 100.0, 2),
            "random_walk_mae_pct": round(sum(rw_err) / n_eval * 100.0, 2),
            "rolling_all_cutoffs_coverage_50": full_fc_audit["nominal_50pct_empirical_coverage"],
            "rolling_all_cutoffs_coverage_80": full_fc_audit["nominal_80pct_empirical_coverage"],
            "rolling_evaluation_samples": full_fc_audit["evaluation_samples"],
            "note": (
                "evaluated_cutoffs 为按步长 H 抽样的无重叠独立截面子集；rolling_all_cutoffs_* 与主报告 calibration_audit 完全一致。"
                "q50 采用最新收盘价（无漂移），因此与随机游走误差一致。"
            ),
        },
    }


def _simulate_plan_on_bars(
    future_bars: list[dict[str, Any]],
    *,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    market: str = "CN",
    asset_type: str = "equity",
    symbol: str = "",
    is_st: bool = False,
    is_ipo_no_limit: bool = False,
    use_ratchet: bool = False,
    matured: bool = True,
) -> dict[str, Any]:
    """在 `as_of` 之后的真实 K 线序列上因果回放单笔 If-Then 计划（含 T+1、一字涨跌停锁死、跳空穿仓真实滑点与可选保本棘轮）。

    matured=False 表示观察期K线还没走完：未触发屏障时返回 PENDING，不按最后一根收盘当作到期打分。
    """
    if not future_bars:
        return {"triggered": False, "status": "PENDING" if not matured else "NO_FUTURE_BARS", "realized_r": None}
    risk = max(1e-6, abs(entry - stop))

    def _locked_at_limit(idx: int, side: str) -> bool:
        """一字板（最高价 = 最低价 = 涨停/跌停价），涨跌幅档位与价位取整按 market_rules（科创/创业板 20%、ST、北交所等）。"""
        if idx <= 0:
            return False
        b = future_bars[idx]
        lo, hi = float(b["low"]), float(b["high"])
        if lo != hi:
            return False
        day = str(b["day"])
        limit_pct = get_daily_price_limit_pct(
            market, asset_type, symbol, is_st=is_st, is_ipo_no_limit=is_ipo_no_limit, day_str=day
        )
        if limit_pct is None:
            return False
        prev = float(future_bars[idx - 1]["close"])
        up, down = daily_limit_prices(prev, limit_pct, market, asset_type, day)
        tick = float(tick_size_for(prev, market, asset_type, day))
        return hi >= up - 0.5 * tick if side == "up" else lo <= down + 0.5 * tick

    entry_idx: int | None = None
    for idx, b in enumerate(future_bars):
        lo, hi, op = float(b["low"]), float(b["high"]), float(b["open"])
        # 一字涨停无法买入成交
        if direction == "LONG" and _locked_at_limit(idx, "up"):
            continue
        if direction == "LONG" and (lo <= entry <= hi or op <= entry):
            entry_idx = idx
            break
        if direction == "SHORT" and (lo <= entry <= hi or op >= entry):
            entry_idx = idx
            break
    if entry_idx is None:
        return {"triggered": False, "status": "NEVER_TRIGGERED" if matured else "PENDING", "realized_r": 0.0 if matured else None}

    entry_day = str(future_bars[entry_idx]["day"])
    rules = get_market_microstructure_rules(
        market, asset_type, symbol, is_st=is_st, is_ipo_no_limit=is_ipo_no_limit, day_str=entry_day
    )
    t_plus_one = not rules["can_intraday_roundtrip"]
    active_stop = stop
    ratchet_armed = False
    if use_ratchet and t_plus_one:
        eb = future_bars[entry_idx]
        if direction == "LONG" and float(eb["low"]) > stop and float(eb["high"]) >= entry + 0.90 * risk and float(eb["close"]) >= entry + 0.30 * risk:
            active_stop = max(active_stop, entry + 0.08 * risk)
            ratchet_armed = True

    start_exit_idx = entry_idx + 1 if t_plus_one else entry_idx
    for idx in range(start_exit_idx, len(future_bars)):
        b = future_bars[idx]
        lo, hi, op, cl = float(b["low"]), float(b["high"]), float(b["open"]), float(b["close"])
        # 一字跌停全天封死无法卖出，须顺延至开板日
        if direction == "LONG" and _locked_at_limit(idx, "down"):
            continue
        if direction == "LONG":
            if lo <= active_stop:
                exit_px = min(op, active_stop)
                r_out = round((exit_px - entry) / risk, 2)
                st_label = "RATCHET_PROTECTED" if (ratchet_armed and exit_px >= entry) else "STOP_HIT"
                return {"triggered": True, "entry_day": entry_day, "exit_day": str(b["day"]), "exit_price": round(exit_px, 4), "status": st_label, "realized_r": r_out}
            if hi >= target:
                exit_px = max(op, target)
                return {"triggered": True, "entry_day": entry_day, "exit_day": str(b["day"]), "exit_price": round(exit_px, 4), "status": "TP_HIT", "realized_r": round((exit_px - entry) / risk, 2)}
            if use_ratchet and hi >= entry + 0.90 * risk and cl >= entry + 0.30 * risk:
                active_stop = max(active_stop, entry + 0.08 * risk)
                ratchet_armed = True
        else:
            if hi >= active_stop:
                exit_px = max(op, active_stop)
                r_out = round((entry - exit_px) / risk, 2)
                st_label = "RATCHET_PROTECTED" if (ratchet_armed and exit_px <= entry) else "STOP_HIT"
                return {"triggered": True, "entry_day": entry_day, "exit_day": str(b["day"]), "exit_price": round(exit_px, 4), "status": st_label, "realized_r": r_out}
            if lo <= target:
                exit_px = min(op, target)
                return {"triggered": True, "entry_day": entry_day, "exit_day": str(b["day"]), "exit_price": round(exit_px, 4), "status": "TP_HIT", "realized_r": round((entry - exit_px) / risk, 2)}
            if use_ratchet and lo <= entry - 0.90 * risk and cl <= entry - 0.30 * risk:
                active_stop = min(active_stop, entry - 0.08 * risk)
                ratchet_armed = True

    if not matured or (direction == "LONG" and _locked_at_limit(len(future_bars) - 1, "down")):
        return {"triggered": True, "entry_day": entry_day, "status": "PENDING", "realized_r": None}
    last_c = float(future_bars[-1]["close"])
    r_val = (last_c - entry) / risk if direction == "LONG" else (entry - last_c) / risk
    return {"triggered": True, "entry_day": entry_day, "exit_day": str(future_bars[-1]["day"]), "exit_price": round(last_c, 4), "status": "TIME_BARRIER_EXPIRED", "realized_r": round(r_val, 2)}


def reconcile_trade_ideas(
    ideas: list[dict[str, Any]],
    *,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    horizon_bars: int = 15,
) -> dict[str, Any]:
    """对账交易想法账本与实际行情走势，识别假跌破扫损后反包与保本棘轮保护等归因。"""
    items: list[dict[str, Any]] = []
    triggered_count = 0
    swept_saved_count = 0
    ratchet_saved_count = 0
    user_wins = 0
    audited_wins = 0
    pending_count = 0

    for idea in ideas:
        sym = str(idea.get("symbol") or "")
        market = str(idea.get("market") or "CN")
        as_of = str(idea.get("as_of") or "")
        direction = str(idea.get("direction") or "LONG").upper()
        all_bars = bars_by_symbol.get(sym) or []
        future_bars = [b for b in all_bars if str(b["day"]) > as_of][:horizon_bars]
        sim_kwargs = {
            "direction": direction,
            "market": market,
            "asset_type": str(idea.get("asset_type") or "equity"),
            "symbol": sym,
            "is_st": bool(idea.get("is_st", False)),
            "is_ipo_no_limit": bool(idea.get("is_ipo_no_limit", False)),
            "matured": len(future_bars) >= horizon_bars,
        }

        u_plan = idea.get("user_idea") or {}
        a_plan = idea.get("recommended_adjustments") or u_plan
        u_out = _simulate_plan_on_bars(future_bars, entry=float(u_plan["entry"]), stop=float(u_plan["stop"]), target=float(u_plan["target"]), use_ratchet=False, **sim_kwargs)
        a_out = _simulate_plan_on_bars(future_bars, entry=float(a_plan["entry"]), stop=float(a_plan["stop"]), target=float(a_plan["target"]), use_ratchet=True, **sim_kwargs)
        if "PENDING" in (u_out["status"], a_out["status"]):
            # 观察期未走完且仍有一方未出结果：不计入任何统计，等到期后再对账
            pending_count += 1
            items.append({
                "idea_id": idea.get("idea_id"),
                "symbol": sym,
                "as_of": as_of,
                "direction": direction,
                "user_outcome": u_out,
                "audited_outcome": a_out,
                "attribution": "PENDING",
            })
            continue
        if u_out["triggered"] or a_out["triggered"]:
            triggered_count += 1
        if u_out["status"] == "TP_HIT":
            user_wins += 1
        if a_out["status"] in ("TP_HIT", "RATCHET_PROTECTED"):
            audited_wins += 1

        if u_out["status"] == "STOP_HIT" and a_out["status"] == "TP_HIT":
            attribution = "STOP_SWEPT_THEN_TARGET"
            swept_saved_count += 1
        elif u_out["status"] == "STOP_HIT" and a_out["status"] == "RATCHET_PROTECTED":
            attribution = "RATCHET_PROTECTED_AFTER_SURGE"
            ratchet_saved_count += 1
        elif u_out["status"] == "STOP_HIT" and a_out["status"] == "STOP_HIT":
            attribution = "GENUINE_BREAKDOWN_STOP"
        elif a_out["status"] == "TP_HIT":
            attribution = "TARGET_ACHIEVED"
        elif not u_out["triggered"] and not a_out["triggered"]:
            attribution = "UNTRIGGERED"
        else:
            attribution = "TIME_EXPIRED"

        items.append({
            "idea_id": idea.get("idea_id"),
            "symbol": sym,
            "as_of": as_of,
            "direction": direction,
            "user_outcome": u_out,
            "audited_outcome": a_out,
            "attribution": attribution,
        })

    return {
        "total_ideas": len(ideas),
        "pending_count": pending_count,
        "triggered_count": triggered_count,
        "swept_stop_saved_count": swept_saved_count,
        "ratchet_saved_count": ratchet_saved_count,
        "user_tp_hit_count": user_wins,
        "audited_tp_hit_count": audited_wins,
        "items": items,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description="交易想法到期复盘与扫损归因对账器")
    parser.add_argument("--reconcile-ledger", type=Path, required=True, help="交易想法 JSONL 账本路径")
    parser.add_argument("--bars-json", type=Path, required=True, help="symbol -> bars 映射或单标的 bars JSON 路径")
    parser.add_argument("--symbol", default=None, help="当 --bars-json 为单标的数组时指定 symbol")
    parser.add_argument("--horizon-bars", type=int, default=15, help="对账观察期交易日数")
    args = parser.parse_args(argv)
    ideas = [json.loads(ln) for ln in args.reconcile_ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    raw_bars = json.loads(args.bars_json.read_text(encoding="utf-8"))
    bars_by_sym = {args.symbol or (ideas[0]["symbol"] if ideas else "UNKNOWN"): raw_bars} if isinstance(raw_bars, list) else raw_bars
    summary = reconcile_trade_ideas(ideas, bars_by_symbol=bars_by_sym, horizon_bars=args.horizon_bars)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

