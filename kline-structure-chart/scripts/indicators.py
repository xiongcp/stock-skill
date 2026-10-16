from __future__ import annotations

import math
from typing import Any


def sma(values: list[float], n: int) -> list[float | None]:
    if n <= 0:
        raise ValueError("sma period must be positive")
    out: list[float | None] = [None] * len(values)
    running = 0.0
    for i, val in enumerate(values):
        running += float(val)
        if i >= n:
            running -= float(values[i - n])
        if i >= n - 1:
            out[i] = running / n
    return out


def ema(values: list[float], n: int) -> list[float | None]:
    if n <= 0:
        raise ValueError("ema period must be positive")
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    alpha = 2.0 / (n + 1.0)
    seed = sum(float(v) for v in values[:n]) / n
    out[n - 1] = seed
    prev = seed
    for i in range(n, len(values)):
        prev = alpha * float(values[i]) + (1.0 - alpha) * prev
        out[i] = prev
    return out


def atr_series(bars: list[dict[str, Any]], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(bars)
    if not bars:
        return out
    trs: list[float] = [float(bars[0]["high"]) - float(bars[0]["low"])]
    for i in range(1, len(bars)):
        h = float(bars[i]["high"])
        l = float(bars[i]["low"])
        pc = float(bars[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(bars) <= period:
        for i in range(len(bars)):
            out[i] = sum(trs[: i + 1]) / (i + 1)
        return out
    # Wilder / TA-Lib 口径：首根K线没有前收盘，不计入初值；ATR 首值落在下标 period = mean(TR[1..period])
    first = sum(trs[1 : period + 1]) / period
    out[period] = first
    prev = first
    for i in range(period + 1, len(bars)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def atr_pct(bars: list[dict[str, Any]], n: int = 20) -> float:
    if len(bars) < 2:
        return 0.015
    window = min(n, len(bars) - 1)
    trs = []
    for prev, cur in zip(bars[-window - 1 : -1], bars[-window:]):
        pc = float(prev["close"])
        if pc <= 0:
            continue
        tr = max(
            float(cur["high"]) - float(cur["low"]),
            abs(float(cur["high"]) - pc),
            abs(float(cur["low"]) - pc),
        )
        trs.append(tr / pc)
    return sum(trs) / len(trs) if trs else 0.015


def rsi(closes: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = float(closes[i]) - float(closes[i - 1])
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = (100.0 if avg_gain > 0 else 50.0) if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    for i in range(period + 1, len(closes)):
        diff = float(closes[i]) - float(closes[i - 1])
        gain = max(0.0, diff)
        loss = max(0.0, -diff)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            out[i] = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, list[float | None]]:
    n = len(closes)
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    dif: list[float | None] = [None] * n
    valid_dif: list[float] = []
    valid_indices: list[int] = []
    for i in range(n):
        if fast_ema[i] is not None and slow_ema[i] is not None:
            val = float(fast_ema[i]) - float(slow_ema[i])
            dif[i] = val
            valid_dif.append(val)
            valid_indices.append(i)
    dea: list[float | None] = [None] * n
    hist: list[float | None] = [None] * n
    if len(valid_dif) >= signal:
        sig_ema = ema(valid_dif, signal)
        for idx_pos, bar_i in enumerate(valid_indices):
            s_val = sig_ema[idx_pos]
            if s_val is not None and dif[bar_i] is not None:
                dea[bar_i] = s_val
                hist[bar_i] = float(dif[bar_i]) - s_val
    return {"macd": dif, "signal": dea, "hist": hist}


def stochastic(
    bars: list[dict[str, Any]],
    k_period: int = 14,
    k_smooth: int = 3,
    d_period: int = 3,
) -> dict[str, list[float | None]]:
    n = len(bars)
    raw_k: list[float] = []
    raw_idx: list[int] = []
    for i in range(n):
        if i < k_period - 1:
            continue
        window = bars[i - k_period + 1 : i + 1]
        hh = max(float(b["high"]) for b in window)
        ll = min(float(b["low"]) for b in window)
        c = float(bars[i]["close"])
        val = 50.0 if hh <= ll else max(0.0, min(100.0, (c - ll) / (hh - ll) * 100.0))
        raw_k.append(val)
        raw_idx.append(i)
    k_out: list[float | None] = [None] * n
    d_out: list[float | None] = [None] * n
    if len(raw_k) >= k_smooth:
        k_sma = sma(raw_k, k_smooth)
        valid_k: list[float] = []
        valid_k_idx: list[int] = []
        for pos, bar_i in enumerate(raw_idx):
            if k_sma[pos] is not None:
                k_out[bar_i] = k_sma[pos]
                valid_k.append(float(k_sma[pos]))
                valid_k_idx.append(bar_i)
        if len(valid_k) >= d_period:
            d_sma = sma(valid_k, d_period)
            for pos, bar_i in enumerate(valid_k_idx):
                if d_sma[pos] is not None:
                    d_out[bar_i] = d_sma[pos]
    return {"k": k_out, "d": d_out}


def bollinger_bands(
    closes: list[float],
    period: int = 20,
    num_std: float = 2.0,
) -> dict[str, list[float | None]]:
    n = len(closes)
    mid = sma(closes, period)
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    bbw: list[float | None] = [None] * n
    pct_b: list[float | None] = [None] * n
    for i in range(period - 1, n):
        m = mid[i]
        if m is None or m <= 0:
            continue
        window = [float(x) for x in closes[i - period + 1 : i + 1]]
        variance = sum((x - m) ** 2 for x in window) / period
        sd = math.sqrt(max(0.0, variance))
        u = m + num_std * sd
        l = m - num_std * sd
        upper[i] = u
        lower[i] = l
        bbw[i] = (u - l) / m
        pct_b[i] = 0.5 if u <= l else (float(closes[i]) - l) / (u - l)
    return {"mid": mid, "upper": upper, "lower": lower, "bbw": bbw, "pct_b": pct_b}


def bbw_percentile(bbw_series: list[float | None], window: int = 120) -> list[float | None]:
    n = len(bbw_series)
    out: list[float | None] = [None] * n
    for i in range(n):
        cur = bbw_series[i]
        if cur is None:
            continue
        start = max(0, i - window + 1)
        hist = [float(x) for x in bbw_series[start : i + 1] if x is not None]
        if len(hist) < 10:
            continue
        le_count = sum(1 for x in hist if x <= cur)
        out[i] = round(le_count / len(hist) * 100.0, 2)
    return out


def supertrend(
    bars: list[dict[str, Any]],
    period: int = 14,
    multiplier: float = 3.0,
) -> dict[str, list[Any]]:
    n = len(bars)
    atr_vals = atr_series(bars, period)
    st_line: list[float | None] = [None] * n
    direction: list[int | None] = [None] * n
    final_upper = 0.0
    final_lower = 0.0
    cur_dir = 1
    for i in range(n):
        a = atr_vals[i]
        if a is None:
            continue
        hl2 = (float(bars[i]["high"]) + float(bars[i]["low"])) / 2.0
        basic_upper = hl2 + multiplier * a
        basic_lower = hl2 - multiplier * a
        c = float(bars[i]["close"])
        if i == 0 or st_line[i - 1] is None:
            final_upper = basic_upper
            final_lower = basic_lower
            cur_dir = 1 if c >= hl2 else -1
        else:
            prev_c = float(bars[i - 1]["close"])
            if basic_upper < final_upper or prev_c > final_upper:
                final_upper = basic_upper
            if basic_lower > final_lower or prev_c < final_lower:
                final_lower = basic_lower
            if cur_dir == 1 and c < final_lower:
                cur_dir = -1
            elif cur_dir == -1 and c > final_upper:
                cur_dir = 1
        direction[i] = cur_dir
        st_line[i] = final_lower if cur_dir == 1 else final_upper
    return {"line": st_line, "direction": direction}


def adx(bars: list[dict[str, Any]], period: int = 14) -> dict[str, list[float | None]]:
    n = len(bars)
    adx_out: list[float | None] = [None] * n
    plus_di: list[float | None] = [None] * n
    minus_di: list[float | None] = [None] * n
    if n <= period:
        return {"adx": adx_out, "plus_di": plus_di, "minus_di": minus_di}
    trs: list[float] = [0.0] * n
    pdm: list[float] = [0.0] * n
    mdm: list[float] = [0.0] * n
    for i in range(1, n):
        h, l = float(bars[i]["high"]), float(bars[i]["low"])
        ph, pl, pc = float(bars[i - 1]["high"]), float(bars[i - 1]["low"]), float(bars[i - 1]["close"])
        trs[i] = max(h - l, abs(h - pc), abs(l - pc))
        up_move = h - ph
        down_move = pl - l
        pdm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        mdm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0

    sm_tr = sum(trs[1 : period + 1])
    sm_pdm = sum(pdm[1 : period + 1])
    sm_mdm = sum(mdm[1 : period + 1])
    dxs: list[tuple[int, float]] = []
    for i in range(period, n):
        if i > period:
            sm_tr = sm_tr - (sm_tr / period) + trs[i]
            sm_pdm = sm_pdm - (sm_pdm / period) + pdm[i]
            sm_mdm = sm_mdm - (sm_mdm / period) + mdm[i]
        pdi = 0.0 if sm_tr <= 0 else 100.0 * sm_pdm / sm_tr
        mdi = 0.0 if sm_tr <= 0 else 100.0 * sm_mdm / sm_tr
        plus_di[i] = pdi
        minus_di[i] = mdi
        denom = pdi + mdi
        dx = 0.0 if denom <= 0 else 100.0 * abs(pdi - mdi) / denom
        dxs.append((i, dx))
    if len(dxs) >= period:
        first_adx = sum(x[1] for x in dxs[:period]) / period
        adx_out[dxs[period - 1][0]] = first_adx
        prev_adx = first_adx
        for bar_i, dx_val in dxs[period:]:
            prev_adx = (prev_adx * (period - 1) + dx_val) / period
            adx_out[bar_i] = prev_adx
    return {"adx": adx_out, "plus_di": plus_di, "minus_di": minus_di}


def rvol(volumes: list[float], period: int = 20) -> list[float | None]:
    vol_ma = sma(volumes, period)
    out: list[float | None] = [None] * len(volumes)
    for i, (v, m) in enumerate(zip(volumes, vol_ma)):
        if m is not None and m > 0:
            out[i] = round(float(v) / m, 3)
    return out


def vwap_rolling(bars: list[dict[str, Any]], period: int = 20) -> list[float | None]:
    n = len(bars)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        sub = bars[i - period + 1 : i + 1]
        pv = 0.0
        vv = 0.0
        for b in sub:
            tp = (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0
            vol = float(b.get("volume") or 0.0)
            pv += tp * vol
            vv += vol
        out[i] = pv / vv if vv > 0 else float(bars[i]["close"])
    return out


def _rank_list(vals: list[float]) -> list[float]:
    indexed = sorted(enumerate(vals), key=lambda x: x[1])
    ranks = [0.0] * len(vals)
    for rank_idx, (orig_idx, _) in enumerate(indexed):
        ranks[orig_idx] = float(rank_idx + 1)
    return ranks


def price_volume_rank_corr(closes: list[float], volumes: list[float], window: int = 20) -> list[float | None]:
    """Qlib 风格量价秩相关系数 Spearman Rank Corr(Close, log(1 + Volume))。"""
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(window - 1, n):
        c_win = [float(x) for x in closes[i - window + 1 : i + 1]]
        v_win = [math.log1p(max(0.0, float(x))) for x in volumes[i - window + 1 : i + 1]]
        rc = _rank_list(c_win)
        rv = _rank_list(v_win)
        mc = sum(rc) / window
        mv = sum(rv) / window
        num = sum((a - mc) * (b - mv) for a, b in zip(rc, rv))
        den_c = math.sqrt(sum((a - mc) ** 2 for a in rc))
        den_v = math.sqrt(sum((b - mv) ** 2 for b in rv))
        out[i] = 0.0 if (den_c * den_v == 0) else round(num / (den_c * den_v), 4)
    return out


def compute_qlib_orthogonal_factors(bars: list[dict[str, Any]], atr_vals: list[float | None]) -> dict[str, Any]:
    """提取 Qlib Alpha158 风格量价特征（未做正交化）：实体占比、影线偏度、量价秩相关、ATR 归一化动量、历史波动率比值。"""
    if not bars:
        return {}
    closes = [float(b["close"]) for b in bars]
    volumes = [float(b.get("volume") or 0.0) for b in bars]
    last = bars[-1]
    o, h, l, c = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
    rng = max(1e-9, h - l)
    body_ratio = abs(c - o) / rng
    upper_wick = (h - max(o, c)) / rng
    lower_wick = (min(o, c) - l) / rng
    wick_skew = round(lower_wick - upper_wick, 4)

    pv_corr_series = price_volume_rank_corr(closes, volumes, window=20)
    pv_corr = pv_corr_series[-1] if pv_corr_series[-1] is not None else 0.0

    atr_last = atr_vals[-1] if (atr_vals and atr_vals[-1]) else max(1e-6, h - l)
    mom10 = (c - closes[-11]) / (atr_last * math.sqrt(10)) if len(closes) >= 11 and atr_last > 0 else 0.0

    # HV20 / HV60 ratio
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]
    hv20 = math.sqrt(sum(r * r for r in rets[-20:]) / max(1, len(rets[-20:]))) * math.sqrt(252) if rets else 0.2
    hv60 = math.sqrt(sum(r * r for r in rets[-60:]) / max(1, len(rets[-60:]))) * math.sqrt(252) if rets else 0.2
    hv_ratio = round(hv20 / hv60, 3) if hv60 > 0 else 1.0

    return {
        "body_to_range": round(body_ratio, 4),
        "upper_wick_ratio": round(upper_wick, 4),
        "lower_wick_ratio": round(lower_wick, 4),
        "wick_skewness": wick_skew,
        "corr_close_logvol_20d": pv_corr,
        "atr_norm_momentum_10d": round(mom10, 4),
        "hv20_annualized": round(hv20, 4),
        "hv60_annualized": round(hv60, 4),
        "hv_ratio_20_60": hv_ratio,
    }


def compute_all_indicators(bars: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [float(b["close"]) for b in bars]
    volumes = [float(b.get("volume") or 0.0) for b in bars]

    sma_map = {n: sma(closes, n) for n in (20, 60, 120, 250)}
    ema_map = {n: ema(closes, n) for n in (20, 50, 60, 120, 200, 250)}
    atr14 = atr_series(bars, 14)
    rsi14 = rsi(closes, 14)
    macd_res = macd(closes, 12, 26, 9)
    stoch_res = stochastic(bars, 14, 3, 3)
    bb_res = bollinger_bands(closes, 20, 2.0)
    bbw_pct = bbw_percentile(bb_res["bbw"], 120)
    st_res = supertrend(bars, 14, 3.0)
    adx_res = adx(bars, 14)
    rvol20 = rvol(volumes, 20)
    vwap20 = vwap_rolling(bars, 20)
    qlib_factors = compute_qlib_orthogonal_factors(bars, atr14)

    def _last(arr: list[Any], digits: int = 2) -> float | None:
        if not arr or arr[-1] is None:
            return None
        return round(float(arr[-1]), digits)

    return {
        "series": {
            "sma": sma_map,
            "ema": ema_map,
            "atr14": atr14,
            "rsi14": rsi14,
            "macd": macd_res,
            "stoch": stoch_res,
            "bollinger": bb_res,
            "bbw_percentile": bbw_pct,
            "supertrend": st_res,
            "adx": adx_res,
            "rvol20": rvol20,
            "vwap20": vwap20,
        },
        "snapshot": {
            "trend": {
                "sma20": _last(sma_map[20]),
                "sma60": _last(sma_map[60]),
                "sma120": _last(sma_map[120]),
                "sma250": _last(sma_map[250]),
                "ema20": _last(ema_map[20]),
                "ema50": _last(ema_map[50]),
                "ema60": _last(ema_map[60]),
                "ema200": _last(ema_map[200]),
                "vwap20": _last(vwap20),
                "supertrend_line": _last(st_res["line"]),
                "supertrend_dir": int(st_res["direction"][-1]) if st_res["direction"] and st_res["direction"][-1] is not None else 0,
                "adx14": _last(adx_res["adx"]),
                "plus_di14": _last(adx_res["plus_di"]),
                "minus_di14": _last(adx_res["minus_di"]),
            },
            "momentum": {
                "rsi14": _last(rsi14),
                "macd_dif": _last(macd_res["macd"], 3),
                "macd_dea": _last(macd_res["signal"], 3),
                "macd_hist": _last(macd_res["hist"], 3),
                "stoch_k": _last(stoch_res["k"]),
                "stoch_d": _last(stoch_res["d"]),
                "rvol20": _last(rvol20, 2) or 1.0,
            },
            "volatility": {
                "atr14": _last(atr14, 3),
                "atr20_pct": round(atr_pct(bars, 20) * 100.0, 2),
                "bb_upper": _last(bb_res["upper"]),
                "bb_mid": _last(bb_res["mid"]),
                "bb_lower": _last(bb_res["lower"]),
                "bbw": _last(bb_res["bbw"], 4),
                "bbw_percentile_120": _last(bbw_pct),
            },
            "qlib_factors": qlib_factors,
        },
    }
