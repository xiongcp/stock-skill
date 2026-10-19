"""L2 经验概率层：逐点无前视的情景事件研究、95% Wilson 置信区间与小样本 Beta-Binomial 收缩。

所有历史触发日 t 必须满足 t + horizon_bars <= cur_idx（结果期在当前截面之前已完全收盘，零前视泄漏）。
当样本数 n < 30 时，通过 Beta-Binomial（三结果 Dirichlet-Multinomial 等价形式）向市场基准指数同一情景的频率收缩，
同时输出原始频率与收缩后频率，并显式标注「低样本」。
"""
from __future__ import annotations

import math
from typing import Any

MIN_RELIABLE_SAMPLES = 30
PRIOR_STRENGTH = 20.0  # Beta-Binomial 先验等效样本量 M（n < 30 时向基准频率收缩）
WARMUP_BARS = 60       # 逐日因果特征预热根数（MA60 / ATR20 / RSI14 / 60日结构窗口）
MIN_TRIGGER_GAP = 2    # 同一情景相邻触发日的最小间隔根数（避免单次连续驻留重复计入同一事件）

DEFAULT_PRIOR_FREQS: dict[str, tuple[float, float, float]] = {
    # (先到目标, 先到失效, 到期) 的无信息/温和基准先验，当无市场基准行情或基准样本为 0 时兜底
    "TREND_CONTINUATION": (0.46, 0.38, 0.16),
    "BREAKOUT_RETEST": (0.42, 0.42, 0.16),
    "FALSE_BREAKOUT_REVERSAL": (0.48, 0.36, 0.16),
    "RANGE_OSCILLATION": (0.56, 0.30, 0.14),
    "BREAKDOWN_DECLINE": (0.44, 0.40, 0.16),
    "C_WAVE_REVERSAL": (0.45, 0.39, 0.16),
    "OVERSOLD_BOUNCE": (0.50, 0.34, 0.16),
    "APEX_BREAKOUT": (0.47, 0.35, 0.18),
    "bull": (0.44, 0.40, 0.16),
    "neutral": (0.56, 0.30, 0.14),
    "bear": (0.44, 0.40, 0.16),
}

# 各情景在历史事件研究中的标准化屏障几何倍数（以触发日因果 ATR 为单位）：(目标距离倍数, 失效距离倍数)
SCENARIO_BARRIER_ATR: dict[str, tuple[float, float]] = {
    "TREND_CONTINUATION": (1.35, 1.15),
    "BREAKOUT_RETEST": (1.55, 1.05),
    "FALSE_BREAKOUT_REVERSAL": (1.20, 1.00),
    "RANGE_OSCILLATION": (0.90, 1.45),
    "BREAKDOWN_DECLINE": (1.45, 1.05),
    "C_WAVE_REVERSAL": (1.35, 1.10),
    "OVERSOLD_BOUNCE": (1.15, 1.15),
    "APEX_BREAKOUT": (1.40, 1.05),
    "bull": (1.50, 1.05),
    "neutral": (0.90, 1.45),
    "bear": (1.45, 1.05),
}


def wilson_ci_95(hits: int, n: int, z: float = 1.96) -> list[float]:
    """频率的 95% Wilson 置信区间（返回 0~1 比例，保留 4 位小数；n=0 时返回 [0.0, 1.0]）。"""
    if n <= 0:
        return [0.0, 1.0]
    p = max(0.0, min(1.0, hits / n))
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return [round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)]


def _quantile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_vals[lo])
    frac = pos - lo
    return float(sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac)


def _build_causal_features(bars: list[dict[str, Any]]) -> dict[str, list[float]]:
    """只用截至当日 t 的已收盘数据计算逐日因果序列，供事件研究快速扫描。"""
    n = len(bars)
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    opens = [float(b["open"]) for b in bars]
    vols = [float(b.get("volume") or 0.0) for b in bars]

    ma20 = [0.0] * n
    ma60 = [0.0] * n
    run20 = 0.0
    run60 = 0.0
    for i, c in enumerate(closes):
        run20 += c
        run60 += c
        if i >= 20:
            run20 -= closes[i - 20]
        if i >= 60:
            run60 -= closes[i - 60]
        ma20[i] = run20 / min(i + 1, 20)
        ma60[i] = run60 / min(i + 1, 60)

    atr_pct = [0.018] * n
    tr_pct_arr = [0.0] * n
    for i in range(n):
        if i == 0:
            tr_pct_arr[0] = (highs[0] - lows[0]) / max(closes[0], 1e-9)
        else:
            prev_c = max(closes[i - 1], 1e-9)
            tr = max(highs[i] - lows[i], abs(highs[i] - prev_c), abs(lows[i] - prev_c))
            tr_pct_arr[i] = tr / prev_c
        win_start = max(0, i - 19)
        atr_pct[i] = max(0.004, sum(tr_pct_arr[win_start : i + 1]) / (i - win_start + 1))

    rsi14 = [50.0] * n
    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(1, n):
        chg = closes[i] - closes[i - 1]
        gain = max(0.0, chg)
        loss = max(0.0, -chg)
        if i <= 14:
            avg_gain += gain / 14.0
            avg_loss += loss / 14.0
            if i == 14:
                if avg_gain == 0.0 and avg_loss == 0.0:
                    rsi14[i] = 50.0
                elif avg_loss == 0.0:
                    rsi14[i] = 100.0
                else:
                    rs = avg_gain / avg_loss
                    rsi14[i] = 100.0 - 100.0 / (1.0 + rs)
        else:
            avg_gain = (avg_gain * 13.0 + gain) / 14.0
            avg_loss = (avg_loss * 13.0 + loss) / 14.0
            if avg_gain == 0.0 and avg_loss == 0.0:
                rsi14[i] = 50.0
            elif avg_loss == 0.0:
                rsi14[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi14[i] = 100.0 - 100.0 / (1.0 + rs)

    rvol20 = [1.0] * n
    v_run = 0.0
    for i, v in enumerate(vols):
        v_run += v
        if i >= 20:
            v_run -= vols[i - 20]
        mean_v = v_run / min(i + 1, 20)
        rvol20[i] = v / mean_v if mean_v > 0 else 1.0

    prev_hi20 = [highs[0]] * n
    prev_lo20 = [lows[0]] * n
    for i in range(1, n):
        w0 = max(0, i - 20)
        prev_hi20[i] = max(highs[w0:i])
        prev_lo20[i] = min(lows[w0:i])

    return {
        "closes": closes,
        "highs": highs,
        "lows": lows,
        "opens": opens,
        "vols": vols,
        "ma20": ma20,
        "ma60": ma60,
        "atr_pct": atr_pct,
        "rsi14": rsi14,
        "rvol20": rvol20,
        "prev_hi20": prev_hi20,
        "prev_lo20": prev_lo20,
        "channel_cache": [None] * n,  # type: ignore[dict-item]
        "event_cache": {},  # type: ignore[dict-item]
    }


_FEAT_CACHE: dict[tuple[int, str, str, float, float], dict[str, Any]] = {}


def _get_or_build_causal_features(bars: list[dict[str, Any]]) -> dict[str, list[float]]:
    if not bars:
        return _build_causal_features(bars)
    key = (
        len(bars),
        str(bars[0]["day"]),
        str(bars[-1]["day"]),
        round(float(bars[0]["close"]), 6),
        round(float(bars[-1]["close"]), 6),
    )
    cached = _FEAT_CACHE.get(key)
    if cached is not None:
        return cached
    feat = _build_causal_features(bars)
    if len(_FEAT_CACHE) > 64:
        _FEAT_CACHE.clear()
    _FEAT_CACHE[key] = feat
    return feat


def _local_channel_at(feat: dict[str, list[float]], t: int, lookback: int = 40) -> tuple[float, float, float, float, float]:
    """只用 [t - lookback + 1 .. t] 的因果高低点估算当时的局部上下轨、斜率与收敛比。"""
    cache = feat.get("channel_cache")
    if lookback == 40 and cache is not None and cache[t] is not None:
        return cache[t]  # type: ignore[return-value]
    highs = feat["highs"]
    lows = feat["lows"]
    closes = feat["closes"]
    s0 = max(0, t - lookback + 1)
    mid = (s0 + t) // 2
    if mid <= s0:
        c = closes[t]
        return c * 1.02, c * 0.98, 0.0, 0.0, 1.0
    sub_h1 = highs[s0 : mid + 1]
    h1_idx = s0 + sub_h1.index(max(sub_h1))
    sub_h2 = highs[mid : t + 1]
    h2_idx = mid + sub_h2.index(max(sub_h2))
    sub_l1 = lows[s0 : mid + 1]
    l1_idx = s0 + sub_l1.index(min(sub_l1))
    sub_l2 = lows[mid : t + 1]
    l2_idx = mid + sub_l2.index(min(sub_l2))

    su = (highs[h2_idx] - highs[h1_idx]) / max(1, h2_idx - h1_idx)
    sl = (lows[l2_idx] - lows[l1_idx]) / max(1, l2_idx - l1_idx)
    u_now = highs[h2_idx] + su * (t - h2_idx)
    l_now = lows[l2_idx] + sl * (t - l2_idx)
    hi_recent = max(highs[max(s0, t - 19) : t + 1])
    lo_recent = min(lows[max(s0, t - 19) : t + 1])
    u_now = max(u_now, hi_recent * 0.995)
    l_now = min(l_now, lo_recent * 1.005)
    if u_now <= l_now:
        u_now, l_now = hi_recent, lo_recent
    w0 = max(1e-6, highs[h1_idx] - lows[l1_idx])
    w_now = max(1e-6, u_now - l_now)
    res = (u_now, l_now, su, sl, w_now / w0)
    if lookback == 40 and cache is not None:
        cache[t] = res  # type: ignore[call-overload]
    return res


def _scenario_triggered_at(
    scenario_id: str,
    feat: dict[str, list[float]],
    t: int,
    direction: str = "BULLISH",
) -> bool:
    """逐日无前视判定第 t 根 K 线收盘时是否触发指定情景的前提与触发条件（只用 0..t 数据）。"""
    if t < WARMUP_BARS:
        return False
    closes = feat["closes"]
    highs = feat["highs"]
    lows = feat["lows"]
    opens = feat["opens"]
    ma20 = feat["ma20"]
    ma60 = feat["ma60"]
    atr_p = feat["atr_pct"][t]
    rsi = feat["rsi14"][t]
    rvol = feat["rvol20"][t]
    c = closes[t]
    atr_abs = max(1e-6, c * atr_p)
    u_now, l_now, su, sl, w_ratio = _local_channel_at(feat, t)
    mid_rail = 0.5 * (u_now + l_now)
    prev_hi20 = feat["prev_hi20"][t] if "prev_hi20" in feat else max(highs[t - 20 : t])
    prev_lo20 = feat["prev_lo20"][t] if "prev_lo20" in feat else min(lows[t - 20 : t])

    if scenario_id == "TREND_CONTINUATION":
        if direction == "BEARISH":
            return ma20[t] < ma60[t] and c <= ma20[t] and c <= mid_rail and (su < 0 or sl < 0)
        return ma20[t] > ma60[t] and c >= ma20[t] and c >= mid_rail and (su > 0 or sl > 0)

    if scenario_id == "BREAKOUT_RETEST":
        just_broke = c >= prev_hi20 * 0.998 or (c >= u_now * 0.998 and closes[t - 1] < u_now)
        near_upper = c >= mid_rail and abs(u_now - c) <= 1.5 * atr_abs and c >= ma20[t]
        return bool(just_broke or near_upper)

    if scenario_id == "FALSE_BREAKOUT_REVERSAL":
        upper_wick_reject = highs[t] >= prev_hi20 * 0.997 and c < highs[t] - 0.35 * atr_abs
        low_vol_poke = highs[t] >= u_now * 0.997 and (c < u_now or rvol < 1.15)
        return bool(upper_wick_reject or low_vol_poke)

    if scenario_id in ("RANGE_OSCILLATION", "neutral"):
        inside_box = (l_now + 0.6 * atr_abs) <= c <= (u_now - 0.6 * atr_abs)
        near_ma = abs(c - ma20[t]) <= 1.2 * atr_abs
        return bool(inside_box and near_ma and (u_now - l_now) >= 1.6 * atr_abs)

    if scenario_id == "BREAKDOWN_DECLINE":
        just_broke_dn = c <= prev_lo20 * 1.002 or (c <= l_now * 1.002 and closes[t - 1] > l_now)
        near_lower = c <= mid_rail and abs(c - l_now) <= 1.5 * atr_abs and c <= ma20[t]
        return bool(just_broke_dn or near_lower)

    if scenario_id == "C_WAVE_REVERSAL":
        win_hi = max(highs[t - 45 : t + 1])
        win_lo = min(lows[t - 45 : t + 1])
        span = max(1e-6, win_hi - win_lo)
        if direction == "BEARISH":
            return (win_hi - c) <= 0.8 * atr_abs and span >= 2.5 * atr_abs and (c < opens[t] or rsi >= 62.0)
        return (c - win_lo) <= 0.8 * atr_abs and span >= 2.5 * atr_abs and (c > opens[t] or rsi <= 38.0)

    if scenario_id == "OVERSOLD_BOUNCE":
        return bool(rsi <= 34.0 or (c - ma20[t]) <= -1.6 * atr_abs)

    if scenario_id == "APEX_BREAKOUT":
        converging = w_ratio < 0.88 or (su < 0 and sl > 0)
        narrow = (u_now - l_now) <= 4.0 * atr_abs
        return bool(converging and narrow)

    if scenario_id == "bull":
        return bool(c >= ma20[t] and (c >= mid_rail or abs(u_now - c) <= 1.5 * atr_abs))

    if scenario_id == "bear":
        return bool(c <= ma20[t] and (c <= mid_rail or abs(c - l_now) <= 1.5 * atr_abs))

    return False


def label_scenario_event(
    feat: dict[str, list[float]],
    t: int,
    scenario_id: str,
    horizon_bars: int,
    direction: str = "BULLISH",
) -> dict[str, Any]:
    """对触发日 t 在未来 [t+1 .. t+horizon_bars] 内用三重屏障标定结果：
    - HIT_TARGET：先触及目标位
    - HIT_INVALIDATION：先触及失效位（同根 K 线同时触及时按保守原则判先到失效）
    - EXPIRED：到期未触及任一屏障
    """
    closes = feat["closes"]
    highs = feat["highs"]
    lows = feat["lows"]
    c0 = closes[t]
    atr_abs = max(1e-6, c0 * feat["atr_pct"][t])
    tgt_mult, inv_mult = SCENARIO_BARRIER_ATR.get(scenario_id, (1.40, 1.05))
    end_k = min(len(closes) - 1, t + max(1, horizon_bars))

    outcome = "EXPIRED"
    bars_used = max(1, end_k - t)

    if scenario_id in ("RANGE_OSCILLATION", "neutral"):
        # 区间震荡：收盘不越出 ±inv_mult*ATR 边界，且在窗口内围绕中枢回归（触及 ±0.45*ATR 中枢带或保持在箱体内）
        upper_break = c0 + inv_mult * atr_abs
        lower_break = c0 - inv_mult * atr_abs
        hit_inv_bar: int | None = None
        revisit_bar: int | None = None
        for k in range(t + 1, end_k + 1):
            if closes[k] > upper_break or closes[k] < lower_break:
                hit_inv_bar = k - t
                break
            if k >= t + 2 and abs(closes[k] - c0) <= 0.55 * atr_abs and revisit_bar is None:
                revisit_bar = k - t
        if hit_inv_bar is not None:
            outcome = "HIT_INVALIDATION"
            bars_used = hit_inv_bar
        elif revisit_bar is not None or end_k > t:
            outcome = "HIT_TARGET"
            bars_used = revisit_bar if revisit_bar is not None else max(1, (end_k - t) // 2)
        if end_k > t:
            hi_win = max(highs[t + 1 : end_k + 1])
            lo_win = min(lows[t + 1 : end_k + 1])
            max_dev = max(abs(hi_win - c0), abs(lo_win - c0)) / c0 * 100.0
            min_dev = min(abs(cl - c0) for cl in closes[t + 1 : end_k + 1]) / c0 * 100.0
        else:
            max_dev = 0.0
            min_dev = 0.0
        return {
            "outcome": outcome,
            "bars_used": bars_used,
            "mfe_pct": round(max(0.1, (inv_mult * feat["atr_pct"][t] * 100.0) - min_dev), 3),
            "mae_pct": round(max_dev, 3),
        }

    if scenario_id == "APEX_BREAKOUT":
        # 双向变盘：向上或向下突破 ±0.6*ATR 后继续推进至 ±tgt_mult*ATR 为达标；若先突破一侧又反向跌破另一侧 ±inv_mult*ATR 为失效
        up_tgt = c0 + tgt_mult * atr_abs
        dn_tgt = max(0.01, c0 - tgt_mult * atr_abs)
        up_inv = c0 + inv_mult * atr_abs
        dn_inv = max(0.01, c0 - inv_mult * atr_abs)
        side = 0
        for k in range(t + 1, end_k + 1):
            if side == 0:
                if closes[k] >= c0 + 0.5 * atr_abs:
                    side = 1
                elif closes[k] <= c0 - 0.5 * atr_abs:
                    side = -1
            if side >= 0 and highs[k] >= up_tgt:
                if lows[k] <= dn_inv:
                    outcome = "HIT_INVALIDATION"
                else:
                    outcome = "HIT_TARGET"
                bars_used = k - t
                break
            if side <= 0 and lows[k] <= dn_tgt:
                if highs[k] >= up_inv:
                    outcome = "HIT_INVALIDATION"
                else:
                    outcome = "HIT_TARGET"
                bars_used = k - t
                break
            if (side == 1 and lows[k] <= dn_inv) or (side == -1 and highs[k] >= up_inv):
                outcome = "HIT_INVALIDATION"
                bars_used = k - t
                break
        up_move = (max(highs[t + 1 : end_k + 1]) / c0 - 1.0) * 100.0 if end_k > t else 0.0
        dn_move = (1.0 - min(lows[t + 1 : end_k + 1]) / c0) * 100.0 if end_k > t else 0.0
        return {
            "outcome": outcome,
            "bars_used": bars_used,
            "mfe_pct": round(max(0.0, max(up_move, dn_move)), 3),
            "mae_pct": round(max(0.0, min(up_move, dn_move)), 3),
        }

    is_bear = direction == "BEARISH" or scenario_id in ("FALSE_BREAKOUT_REVERSAL", "BREAKDOWN_DECLINE", "bear")
    if is_bear:
        target_p = max(0.01, c0 - tgt_mult * atr_abs)
        invalid_p = c0 + inv_mult * atr_abs
        for k in range(t + 1, end_k + 1):
            hit_inv = highs[k] >= invalid_p
            hit_tgt = lows[k] <= target_p
            if hit_inv and hit_tgt:
                outcome = "HIT_INVALIDATION"
                bars_used = k - t
                break
            if hit_inv:
                outcome = "HIT_INVALIDATION"
                bars_used = k - t
                break
            if hit_tgt:
                outcome = "HIT_TARGET"
                bars_used = k - t
                break
        mfe = max(0.0, (1.0 - min(lows[t + 1 : end_k + 1]) / c0) * 100.0) if end_k > t else 0.0
        mae = max(0.0, (max(highs[t + 1 : end_k + 1]) / c0 - 1.0) * 100.0) if end_k > t else 0.0
    else:
        target_p = c0 + tgt_mult * atr_abs
        invalid_p = max(0.01, c0 - inv_mult * atr_abs)
        for k in range(t + 1, end_k + 1):
            hit_inv = lows[k] <= invalid_p
            hit_tgt = highs[k] >= target_p
            if hit_inv and hit_tgt:
                outcome = "HIT_INVALIDATION"
                bars_used = k - t
                break
            if hit_inv:
                outcome = "HIT_INVALIDATION"
                bars_used = k - t
                break
            if hit_tgt:
                outcome = "HIT_TARGET"
                bars_used = k - t
                break
        mfe = max(0.0, (max(highs[t + 1 : end_k + 1]) / c0 - 1.0) * 100.0) if end_k > t else 0.0
        mae = max(0.0, (1.0 - min(lows[t + 1 : end_k + 1]) / c0) * 100.0) if end_k > t else 0.0

    return {
        "outcome": outcome,
        "bars_used": bars_used,
        "mfe_pct": round(mfe, 3),
        "mae_pct": round(mae, 3),
    }


def _event_definition_cn(scenario_id: str, direction: str, horizon_bars: int) -> str:
    """说明 L2 实际统计的标准化事件（与 label_scenario_event 同口径），避免把频率误读为情景目标价的到达频率。"""
    tgt, inv = SCENARIO_BARRIER_ATR.get(scenario_id, (1.40, 1.05))
    tail = "；以触发日 ATR 为单位的标准化屏障，不是本情景目标价/失效价的到达频率"
    if scenario_id in ("RANGE_OSCILLATION", "neutral"):
        return f"标准化事件：触发后 {horizon_bars} 根K线内收盘始终未越出触发日收盘 ±{inv:.2f}×ATR{tail}"
    if scenario_id == "APEX_BREAKOUT":
        return f"标准化事件：触发后 {horizon_bars} 根K线内向任一方向先推进 {tgt:.2f}×ATR、且未反向越过 {inv:.2f}×ATR{tail}"
    is_bear = direction == "BEARISH" or scenario_id in ("FALSE_BREAKOUT_REVERSAL", "BREAKDOWN_DECLINE", "bear")
    sign_t, sign_i = ("−", "+") if is_bear else ("+", "−")
    return (
        f"标准化事件：触发后 {horizon_bars} 根K线内先触及 {sign_t}{tgt:.2f}×ATR（目标）而非 {sign_i}{inv:.2f}×ATR（失效），"
        f"同根同时触及按失效计{tail}"
    )


def _scan_events(
    feat: dict[str, list[float]],
    scenario_id: str,
    horizon_bars: int,
    direction: str = "BULLISH",
    max_cutoff_idx: int | None = None,
) -> list[dict[str, Any]]:
    """在 [WARMUP_BARS .. max_cutoff_idx - horizon_bars] 内逐日扫描已完全成熟的触发事件。"""
    n = len(feat["closes"])
    cur_idx = (n - 1) if max_cutoff_idx is None else min(n - 1, max_cutoff_idx)
    last_mature_t = cur_idx - max(1, horizon_bars)
    if last_mature_t < WARMUP_BARS:
        return []

    ev_cache = feat.get("event_cache")
    cache_key = (scenario_id, horizon_bars, direction, last_mature_t)
    if isinstance(ev_cache, dict) and cache_key in ev_cache:
        return ev_cache[cache_key]

    events: list[dict[str, Any]] = []
    last_trig = -999
    for t in range(WARMUP_BARS, last_mature_t + 1):
        if t - last_trig < MIN_TRIGGER_GAP:
            continue
        if _scenario_triggered_at(scenario_id, feat, t, direction=direction):
            ev = label_scenario_event(feat, t, scenario_id, horizon_bars, direction=direction)
            events.append({"trigger_idx": t, **ev})
            last_trig = t
    if isinstance(ev_cache, dict):
        ev_cache[cache_key] = events
    return events


def compute_scenario_frequency(
    target_feat: dict[str, list[float]],
    scenario_id: str,
    horizon_bars: int = 15,
    direction: str = "BULLISH",
    bench_feat: dict[str, list[float]] | None = None,
    max_cutoff_idx: int | None = None,
    bench_max_cutoff_idx: int | None = None,
) -> dict[str, Any]:
    """计算单个情景的无前视历史条件频率、95% Wilson 区间、Beta-Binomial 收缩、到达用时与 MAE/MFE。"""
    events = _scan_events(target_feat, scenario_id, horizon_bars, direction=direction, max_cutoff_idx=max_cutoff_idx)
    n = len(events)
    k_tgt = sum(1 for e in events if e["outcome"] == "HIT_TARGET")
    k_inv = sum(1 for e in events if e["outcome"] == "HIT_INVALIDATION")
    k_exp = n - k_tgt - k_inv

    # 市场基准同情景频率（用于 n < 30 时 Beta-Binomial 收缩先验）
    default_p = DEFAULT_PRIOR_FREQS.get(scenario_id, (0.45, 0.39, 0.16))
    prior_source = "UNIFORM_PRIOR"
    prior_n = 0
    prior_tgt, prior_inv, prior_exp = default_p
    if bench_feat is not None:
        b_events = _scan_events(
            bench_feat, scenario_id, horizon_bars, direction=direction, max_cutoff_idx=bench_max_cutoff_idx
        )
        prior_n = len(b_events)
        if prior_n >= 5:
            prior_source = "BENCHMARK_INDEX"
            bt = sum(1 for e in b_events if e["outcome"] == "HIT_TARGET")
            bi = sum(1 for e in b_events if e["outcome"] == "HIT_INVALIDATION")
            be = prior_n - bt - bi
            # 基准频率本身用微弱平滑避免 0 概率边界
            prior_tgt = (bt + 1.0) / (prior_n + 3.0)
            prior_inv = (bi + 1.0) / (prior_n + 3.0)
            prior_exp = (be + 1.0) / (prior_n + 3.0)

    if n > 0:
        raw_tgt = k_tgt / n
        raw_inv = k_inv / n
        raw_exp = k_exp / n
    else:
        raw_tgt, raw_inv, raw_exp = prior_tgt, prior_inv, prior_exp

    low_sample = n < MIN_RELIABLE_SAMPLES
    # Beta-Binomial / Dirichlet-Multinomial 后验均值：(k + M * p_prior) / (n + M)
    post_tgt = (k_tgt + PRIOR_STRENGTH * prior_tgt) / (n + PRIOR_STRENGTH)
    post_inv = (k_inv + PRIOR_STRENGTH * prior_inv) / (n + PRIOR_STRENGTH)
    post_exp = max(0.0, 1.0 - round(post_tgt, 4) - round(post_inv, 4))

    if low_sample:
        shrunk_tgt = round(post_tgt, 4)
        shrunk_inv = round(post_inv, 4)
        shrunk_exp = round(max(0.0, 1.0 - shrunk_tgt - shrunk_inv), 4)
    else:
        shrunk_tgt = round(raw_tgt, 4)
        shrunk_inv = round(raw_inv, 4)
        shrunk_exp = round(max(0.0, 1.0 - shrunk_tgt - shrunk_inv), 4)

    ci_tgt = wilson_ci_95(k_tgt, n)
    ci_inv = wilson_ci_95(k_inv, n)
    ci_exp = wilson_ci_95(k_exp, n)

    target_bars_sorted = sorted(float(e["bars_used"]) for e in events if e["outcome"] == "HIT_TARGET")
    all_bars_sorted = sorted(float(e["bars_used"]) for e in events)
    timing_pool = target_bars_sorted if target_bars_sorted else all_bars_sorted
    q25_bars = _quantile(timing_pool, 0.25)
    med_bars = _quantile(timing_pool, 0.50)
    q75_bars = _quantile(timing_pool, 0.75)
    med_all_bars = _quantile(all_bars_sorted, 0.50)

    mfes = sorted(float(e["mfe_pct"]) for e in events)
    maes = sorted(float(e["mae_pct"]) for e in events)
    med_mfe = _quantile(mfes, 0.50)
    med_mae = _quantile(maes, 0.50)
    mean_mfe = round(sum(mfes) / len(mfes), 2) if mfes else None
    mean_mae = round(sum(maes) / len(maes), 2) if maes else None

    ci_lo_pct = round(ci_tgt[0] * 100.0, 1)
    ci_hi_pct = round(ci_tgt[1] * 100.0, 1)
    shrunk_tgt_pct = round(shrunk_tgt * 100.0, 1)
    raw_tgt_pct = round(raw_tgt * 100.0, 1)
    # 「历史条件频率」一词只用于实际观测频率 k/n（与 Wilson 区间同口径）；n < 30 时的收缩后估计单独标注
    if low_sample:
        display_cn = (
            f"历史条件频率 {raw_tgt_pct:.1f}%（n = {n} 低样本，95% 区间 {ci_lo_pct:.1f}%~{ci_hi_pct:.1f}%；"
            f"已向市场基准做 Beta-Binomial 收缩，收缩后估计 {shrunk_tgt_pct:.1f}%）"
        )
    else:
        display_cn = f"历史条件频率 {raw_tgt_pct:.1f}%（n = {n}，95% 区间 {ci_lo_pct:.1f}%~{ci_hi_pct:.1f}%）"

    return {
        "scenario_id": scenario_id,
        "horizon_bars": horizon_bars,
        "sample_size": n,
        "low_sample": low_sample,
        "min_reliable_samples": MIN_RELIABLE_SAMPLES,
        "target_hits": k_tgt,
        "invalidation_hits": k_inv,
        "expired_count": k_exp,
        "raw_target_freq": round(raw_tgt, 4),
        "raw_target_freq_pct": raw_tgt_pct,
        "raw_invalidation_freq": round(raw_inv, 4),
        "raw_invalidation_freq_pct": round(raw_inv * 100.0, 1),
        "raw_expired_freq": round(raw_exp, 4),
        "raw_expired_freq_pct": round(raw_exp * 100.0, 1),
        "shrunk_target_freq": shrunk_tgt,
        "shrunk_target_freq_pct": shrunk_tgt_pct,
        "shrunk_invalidation_freq": shrunk_inv,
        "shrunk_invalidation_freq_pct": round(shrunk_inv * 100.0, 1),
        "shrunk_expired_freq": shrunk_exp,
        "shrunk_expired_freq_pct": round(shrunk_exp * 100.0, 1),
        "posterior_target_freq": round(post_tgt, 4),
        "target_freq": shrunk_tgt,
        "target_freq_pct": shrunk_tgt_pct,
        "invalidation_freq": shrunk_inv,
        "invalidation_freq_pct": round(shrunk_inv * 100.0, 1),
        "expired_freq": shrunk_exp,
        "expired_freq_pct": round(shrunk_exp * 100.0, 1),
        "target_ci_95": ci_tgt,
        "target_ci_95_pct": [ci_lo_pct, ci_hi_pct],
        "invalidation_ci_95": ci_inv,
        "invalidation_ci_95_pct": [round(ci_inv[0] * 100.0, 1), round(ci_inv[1] * 100.0, 1)],
        "expired_ci_95": ci_exp,
        "expired_ci_95_pct": [round(ci_exp[0] * 100.0, 1), round(ci_exp[1] * 100.0, 1)],
        "shrinkage": {
            "method": "BETA_BINOMIAL_BENCHMARK_PRIOR",
            "applied": low_sample,
            "prior_source": prior_source,
            "prior_sample_size": prior_n,
            "prior_strength": PRIOR_STRENGTH,
            "prior_target_freq": round(prior_tgt, 4),
            "prior_invalidation_freq": round(prior_inv, 4),
            "prior_expired_freq": round(prior_exp, 4),
        },
        "bars_to_target": {
            "q25": round(q25_bars, 1) if q25_bars is not None else None,
            "median": round(med_bars, 1) if med_bars is not None else None,
            "q75": round(q75_bars, 1) if q75_bars is not None else None,
            "median_all_outcomes": round(med_all_bars, 1) if med_all_bars is not None else None,
        },
        "excursion_pct": {
            "median_mfe_pct": round(med_mfe, 2) if med_mfe is not None else None,
            "mean_mfe_pct": mean_mfe,
            "median_mae_pct": round(med_mae, 2) if med_mae is not None else None,
            "mean_mae_pct": mean_mae,
        },
        "display_cn": display_cn,
        "estimate_method": "BETA_BINOMIAL_SHRINKAGE" if low_sample else "RAW",
        "event_definition_cn": _event_definition_cn(scenario_id, direction, horizon_bars),
    }


def compute_all_base_rates(
    bars: list[dict[str, Any]],
    horizon_bars: int = 15,
    benchmark_bars: list[dict[str, Any]] | None = None,
    scenario_specs: list[tuple[str, str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """一次性在给定历史序列上计算多个 (scenario_id, direction) 的历史条件频率字典。"""
    target_feat = _get_or_build_causal_features(bars)
    bench_feat: dict[str, list[float]] | None = None
    if benchmark_bars and len(benchmark_bars) >= WARMUP_BARS + horizon_bars + 2:
        last_day = str(bars[-1]["day"])
        aligned_bench = [b for b in benchmark_bars if str(b["day"]) <= last_day]
        if len(aligned_bench) >= WARMUP_BARS + horizon_bars + 2:
            bench_feat = _get_or_build_causal_features(aligned_bench)

    specs = scenario_specs or [
        ("TREND_CONTINUATION", "BULLISH"),
        ("BREAKOUT_RETEST", "BULLISH"),
        ("FALSE_BREAKOUT_REVERSAL", "BEARISH"),
        ("RANGE_OSCILLATION", "NEUTRAL"),
        ("BREAKDOWN_DECLINE", "BEARISH"),
        ("C_WAVE_REVERSAL", "BULLISH"),
        ("OVERSOLD_BOUNCE", "BULLISH"),
        ("APEX_BREAKOUT", "BIDIRECTIONAL"),
        ("bull", "BULLISH"),
        ("neutral", "NEUTRAL"),
        ("bear", "BEARISH"),
    ]
    out: dict[str, dict[str, Any]] = {}
    for sc_id, direction in specs:
        key = f"{sc_id}:{direction}"
        out[key] = compute_scenario_frequency(
            target_feat,
            sc_id,
            horizon_bars=horizon_bars,
            direction=direction,
            bench_feat=bench_feat,
        )
        if sc_id not in out:
            out[sc_id] = out[key]
    return out
