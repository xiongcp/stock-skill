#!/usr/bin/env python3
"""M3 两阶段张量重测：阶段 A 落盘信号路径张量，阶段 B 以 float64 向量化复刻三重屏障碰撞。

阶段 A（`dump_paths`）：对每个 (symbol, 决策日, 计划) 用生产函数算出入场后 L 根K线的 OHLC 路径、
  涨跌停封死掩码、A 股 T+1 首日禁卖标志与双边费率（全部来自 market_rules，不在这里重写制度）。
阶段 B（`vectorized_label`）：整批 numpy float64 复刻 evaluation.label_triple_barrier_event 的撮合语义
  （T+1 首日禁卖、同根双触按止损、保本棘轮、一字跌停无法卖出顺延、开盘跳空按开盘价穿仓、双边成本 + 2bps 滑点）。

约束：只用 float64；规则被锁定后必须与线上函数 100% 逐笔一致（见 tests/test_tensor_vs_live_equivalence）。
未来路径只用于结果打标，严禁流回决策特征。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SKILL_SCRIPTS = ROOT / "kline-structure-chart" / "scripts"
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

from market_rules import (  # noqa: E402
    daily_limit_prices,
    get_daily_price_limit_pct,
    get_market_microstructure_rules,
    round_trip_cost_rate,
    tick_size_for,
)

PATH_LEN = 25  # horizon(15) + FUTURE_PAD(10)：与 run_backtest.make_pit_slice 的未来窗口一致
OUTCOMES = (
    "TIME_BARRIER",
    "TP_BARRIER",
    "SL_BARRIER",
    "SL_BARRIER_SAME_BAR_CONSERVATIVE",
    "TRAILING_PROFIT_STOP",
    "INVALID_PLAN_GEOMETRY",
    "LIMIT_UP_NO_FILL",
    "INSUFFICIENT_FUTURE_BARS",
)
_OC = {name: i for i, name in enumerate(OUTCOMES)}


def dump_paths(
    pit_bars: list[list[dict]],
    entry_idx: list[int],
    tp: list[float],
    sl: list[float],
    market: str,
    symbols: list[str],
    names: list[str],
    is_st: list[bool],
    entry_override: list[float | None] | None = None,
    horizon: int = 15,
    ratchet: tuple[float, float, float] = (0.90, 0.30, 0.08),
) -> dict[str, np.ndarray]:
    """阶段 A：把 N 个信号的入场后路径与制度参数打包成定长 float64 张量。"""
    n = len(pit_bars)
    out = {
        "open": np.full((n, PATH_LEN), np.nan),
        "high": np.full((n, PATH_LEN), np.nan),
        "low": np.full((n, PATH_LEN), np.nan),
        "close": np.full((n, PATH_LEN), np.nan),
        "locked": np.zeros((n, PATH_LEN), dtype=bool),
        "valid": np.zeros((n, PATH_LEN), dtype=bool),
        "entry_price": np.zeros(n),
        "tp": np.asarray(tp, dtype=np.float64),
        "sl": np.asarray(sl, dtype=np.float64),
        "fee": np.zeros(n),
        "t1": np.zeros(n, dtype=bool),
        "entry_blocked": np.zeros(n, dtype=bool),
        "horizon": np.full(n, horizon, dtype=np.int64),
        "ratchet": np.asarray(ratchet, dtype=np.float64),
    }
    for i in range(n):
        bars, e = pit_bars[i], entry_idx[i]
        if e >= len(bars):
            continue
        eb = bars[e]
        override = entry_override[i] if entry_override else None
        out["entry_price"][i] = float(override) if override is not None else float(eb["open"])
        limit_pct = get_daily_price_limit_pct(
            market, "equity", symbols[i], is_st=is_st[i], is_ipo_no_limit=False, day_str=eb["day"], name=names[i]
        )
        rules = get_market_microstructure_rules(
            market, "equity", symbols[i], is_st=is_st[i], is_ipo_no_limit=False, day_str=eb["day"], name=names[i]
        )
        out["fee"][i] = round_trip_cost_rate(rules) + 2.0 / 10000.0
        out["t1"][i] = not rules["can_intraday_roundtrip"]

        def limits(idx: int):
            if limit_pct is None or idx <= 0:
                return None
            cur = bars[idx]
            cc = float(cur["close"])
            scale = float(cur.get("raw_close", cc)) / cc if cc > 0 else 1.0
            ref = float(bars[idx - 1]["close"]) * scale
            up, down = daily_limit_prices(ref, limit_pct, market, "equity", cur["day"])
            return up, down, float(tick_size_for(ref, market, "equity", cur["day"])), scale

        el = limits(e)
        if el is not None and out["entry_price"][i] * el[3] >= el[0] - 0.5 * el[2]:
            out["entry_blocked"][i] = True
        for k in range(min(PATH_LEN, len(bars) - e)):
            b = bars[e + k]
            out["open"][i, k], out["high"][i, k] = float(b["open"]), float(b["high"])
            out["low"][i, k], out["close"][i, k] = float(b["low"]), float(b["close"])
            out["valid"][i, k] = True
            lm = limits(e + k)
            if lm is not None:
                _, down, tick, scale = lm
                hi_k, lo_k = float(b["high"]), float(b["low"])
                out["locked"][i, k] = hi_k * scale <= down + 0.5 * tick and lo_k == hi_k
    return out


def vectorized_label(t: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """阶段 B：整批 float64 三重屏障碰撞，语义与 evaluation.label_triple_barrier_event 一致。"""
    n = t["entry_price"].shape[0]
    op, hi, lo, cl = t["open"], t["high"], t["low"], t["close"]
    valid, locked = t["valid"], t["locked"]
    entry, tp, sl = t["entry_price"], t["tp"], t["sl"]
    trig_r, conf_r, lock_r = (float(x) for x in t["ratchet"])
    n_valid = valid.sum(axis=1)
    start = t["t1"].astype(np.int64)
    horizon = t["horizon"]

    outcome = np.full(n, _OC["TIME_BARRIER"], dtype=np.int64)
    tradable = np.ones(n, dtype=bool)
    invalid = (sl >= entry) | (tp <= entry)
    outcome[invalid] = _OC["INVALID_PLAN_GEOMETRY"]
    tradable &= ~invalid
    blocked = tradable & t["entry_blocked"]
    outcome[blocked] = _OC["LIMIT_UP_NO_FILL"]
    tradable &= ~blocked
    short = tradable & (start >= n_valid)
    outcome[short] = _OC["INSUFFICIENT_FUTURE_BARS"]
    tradable &= ~short

    end_k = np.minimum(n_valid - 1, np.maximum(start, horizon - 1))
    exit_k = end_k.copy()
    exit_px = np.where(n_valid > 0, cl[np.arange(n), np.clip(end_k, 0, PATH_LEN - 1)], np.nan)
    unit_risk = np.maximum(1e-6, entry - sl)
    active_sl = sl.copy()
    highest = entry.copy()

    # 入场当根（T+1 市场当日不可卖）：只更新最高价与棘轮，不检测出场
    first = tradable & (start > 0) & (lo[:, 0] > active_sl)
    highest = np.where(first, np.maximum(highest, hi[:, 0]), highest)
    arm = first & (highest >= entry + trig_r * unit_risk) & (cl[:, 0] >= entry + conf_r * unit_risk)
    active_sl = np.where(arm, np.maximum(active_sl, entry + lock_r * unit_risk), active_sl)

    done = ~tradable
    for k in range(PATH_LEN):
        live = ~done & (k >= start) & (k <= end_k) & ~locked[:, k]
        if not live.any():
            continue
        hit_sl = live & (lo[:, k] <= active_sl)
        hit_tp = live & (hi[:, k] >= tp)
        both = hit_sl & hit_tp
        sl_only = hit_sl & ~hit_tp
        tp_only = hit_tp & ~hit_sl
        stop_px = np.minimum(op[:, k], active_sl)
        outcome[both] = _OC["SL_BARRIER_SAME_BAR_CONSERVATIVE"]
        trailing = active_sl > sl
        outcome[sl_only & trailing] = _OC["TRAILING_PROFIT_STOP"]
        outcome[sl_only & ~trailing] = _OC["SL_BARRIER"]
        outcome[tp_only] = _OC["TP_BARRIER"]
        hit = both | sl_only | tp_only
        exit_k[hit] = k
        exit_px[both | sl_only] = stop_px[both | sl_only]
        exit_px[tp_only] = np.maximum(op[:, k], tp)[tp_only]
        done |= hit
        cont = live & ~hit
        highest = np.where(cont, np.maximum(highest, hi[:, k]), highest)
        arm = cont & (highest >= entry + trig_r * unit_risk) & (cl[:, k] >= entry + conf_r * unit_risk)
        active_sl = np.where(arm, np.maximum(active_sl, entry + lock_r * unit_risk), active_sl)

    # 时间屏障：到期日若一字跌停封死，顺延到首个可成交日（与线上 while 循环一致，行数极少故逐行处理）
    for i in np.flatnonzero(tradable & (outcome == _OC["TIME_BARRIER"])):
        j = int(exit_k[i])
        while locked[i, j] and j + 1 < n_valid[i]:
            j += 1
            if not locked[i, j]:
                exit_px[i] = min(op[i, j], active_sl[i]) if lo[i, j] <= active_sl[i] else cl[i, j]
                break
            exit_px[i] = cl[i, j]
        exit_k[i] = j

    gross = exit_px / entry - 1.0
    net = gross - t["fee"]
    return {
        "tradable": tradable,
        "outcome": outcome,
        "exit_k": exit_k,
        "exit_price": exit_px,
        "gross_return": np.where(tradable, gross, np.nan),
        "net_return": np.where(tradable, net, np.nan),
        "holding_bars": np.where(tradable, exit_k + 1, 0),
    }


def outcome_names(codes: np.ndarray) -> list[str]:
    return [OUTCOMES[int(c)] for c in codes]
