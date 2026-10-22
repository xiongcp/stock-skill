from __future__ import annotations

import math
import random
from typing import Any

from forecast import _local_sigma, compute_conformal_forecast
from indicators import atr_series, compute_all_indicators
from market_rules import (
    daily_limit_prices,
    get_daily_price_limit_pct,
    get_market_microstructure_rules,
    rescale_bars_as_of,
    round_to_tick,
    round_trip_cost_rate,
    tick_size_for,
)
from patterns import (
    causal_nadaraya_watson,
    causal_swing_pivots,
    detect_candlestick_patterns,
    detect_smc_structures,
)
from structure import HISTORY_BARS, STRUCT_DAYS, compute_structure_layer


def run_causal_replay_audit(bars: list[dict[str, Any]], check_steps: int = 8) -> dict[str, Any]:
    """按生产配置验证因果无前视不变性：
    1. 单边因果 Nadaraya-Watson 平滑在追加未来 K 线后绝不改变历史值；
    2. 在生产固定结构窗口（HISTORY_BARS / STRUCT_DAYS）与同一阈值下，截至 t_cut 已确认的拐点与 SMC 缺口在追加未来 K 线后不改变；
    3. 蜡烛形态检测在 t_cut 当日收盘确认后，不随未来 K 线追加而变化；
    4. 保形预测在截取 bars[:cut] 时与完整序列上按同一截止日计算的结果完全一致。
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
        sub_cdl = detect_candlestick_patterns(
            sub, sub_ind["series"]["atr14"], sub_ind["series"]["rvol20"], lookback=40, max_items=None
        )
        ext_cdl = {
            (p["pattern"], p["observed_at"]): p["formula_verified"]
            for p in detect_candlestick_patterns(
                ext_corrupted, ext_ind["series"]["atr14"], ext_ind["series"]["rvol20"], lookback=len(ext_corrupted), max_items=None
            )
            if str(p["observed_at"]) <= cut_day
        }
        for pat in sub_cdl:
            pkey = (pat["pattern"], pat["observed_at"])
            if pkey not in full_cdl or pkey not in ext_cdl or ext_cdl[pkey] != pat["formula_verified"]:
                mismatches.append(f"Candlestick pattern mutated at cut={cut}: {pkey}")

        # 4. 保形预测波动率与非一致性分数仅依赖截至 cut 的已收盘 K 线（不受 cut 之后极端未来价格影响）
        fc_a = compute_conformal_forecast(sub, meta=meta_stub, horizon_bars=15)
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

    return {
        "checked_cutoffs": checked,
        "modules_audited": [
            "nadaraya_watson",
            "structure_layer_pivots",
            "candlestick_patterns",
            "smc_structures",
            "conformal_forecast",
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

    limit_pct = get_daily_price_limit_pct(
        market, asset_type, symbol, is_st=is_st, is_ipo_no_limit=is_ipo_no_limit, day_str=entry_bar["day"], name=name
    )

    def _limit_prices(idx: int) -> tuple[float, float, float, float] | None:
        """第 idx 根K线在其原始价口径下的 (涨停价, 跌停价, 最小价位, 复权价→原始价系数)。

        复权序列不在价位网格上，先把前收盘换算到当根K线的原始价口径（除权日即除权参考价），再按交易所规则取整。
        """
        if limit_pct is None or idx <= 0:
            return None
        cur = bars[idx]
        cur_close = float(cur["close"])
        scale = float(cur.get("raw_close", cur_close)) / cur_close if cur_close > 0 else 1.0
        ref = float(bars[idx - 1]["close"]) * scale
        up, down = daily_limit_prices(ref, limit_pct, market, asset_type, cur["day"])
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
            active_sl = max(active_sl, entry_open + lock_r * unit_risk)

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
            active_sl = max(active_sl, entry_open + lock_r * unit_risk)

    if outcome == "TIME_BARRIER":
        while _is_limit_down_locked(exit_idx) and exit_idx + 1 < n:
            exit_idx += 1
            if not _is_limit_down_locked(exit_idx):
                b_un = bars[exit_idx]
                exit_price = min(float(b_un["open"]), active_sl) if float(b_un["low"]) <= active_sl else float(b_un["close"])
                break
            exit_price = float(bars[exit_idx]["close"])
        label = 1 if exit_price > entry_open else -1

    fee_rate = round_trip_cost_rate(rules) + 2.0 / 10000.0  # 另计 2bps 滑点
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
    inst_name = str(inst.get("name") or request.get("name") or "")
    is_st = bool(inst.get("is_st") or request.get("is_st", False))
    is_ipo_no_limit = bool(request.get("is_ipo_no_limit") or (bundle.get("extra_context") or {}).get("is_ipo_no_limit", False))
    n = len(bars)
    if n < 250 + horizon_bars + 2:
        raise ValueError(f"Need >= {250 + horizon_bars + 2} bars for plan backtest on {symbol}, got {n}")

    start = max(250, n - horizon_bars - 2 - decision_window)
    end = n - horizon_bars - 1
    bench = bundle.get("benchmark")
    sub_request = {**request, "display_bars": 120, "account_equity": None}
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
        pit_bars = rescale_bars_as_of(bars[: min(n, t + horizon_bars + 10)], t)
        sub_bundle = {
            "schema_version": "5.3.0",
            "market": market,
            "target": {"instrument": inst, "bars": pit_bars[: t + 1], "intraday": None},
            "benchmark": {**bench, "bars": [b for b in bench["bars"] if b["day"] <= cut_day]} if bench else None,
            # 历史 VIX 不可得，回放中不使用当前 VIX，避免前视
            "extra_context": {"vix": None, "earnings_date": None, "is_st": is_st, "is_ipo_no_limit": ipo_at_t},
            "provenance": bundle.get("provenance", {}),
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
            entry_open = float(pit_bars[t + 1]["open"])
            if entry_low <= entry_open <= entry_high and entry_open > stop_loss:
                res = label_triple_barrier_event(
                    pit_bars,
                    entry_idx=t + 1,
                    tp_price=float(plan["take_profit_1"]),
                    sl_price=stop_loss,
                    horizon_bars=horizon_bars,
                    market=market,
                    asset_type=asset_type,
                    symbol=symbol,
                    is_st=is_st,
                    is_ipo_no_limit=ipo_at_t,
                    trailing_stop=bool(ratchet.get("enabled", True)),
                    trigger_r=float(ratchet.get("trigger_r", 0.90)),
                    confirm_close_r=float(ratchet.get("confirm_close_r", 0.30)),
                    lock_r=float(ratchet.get("lock_r", 0.08)),
                    name=inst_name,
                )
                if res.get("tradable"):
                    trade_rec = {
                        **res,
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
        t += step

    years = max(1e-6, (end - start) / 252.0)
    total = len(all_trades)
    bh_ret = (float(bars[end]["close"]) / float(bars[start]["close"]) - 1.0) * 100.0

    # 保形区间样本外覆盖（与线上同一构造）及中位数对随机游走的误差
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
