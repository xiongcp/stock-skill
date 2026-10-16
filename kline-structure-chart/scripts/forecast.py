from __future__ import annotations

import bisect
import math
from typing import Any

from market_rules import CALENDAR_COVERAGE_END, calendar_covers, get_daily_price_limit_pct, next_trading_dates, round_to_tick


def _zscore_vector(vals: list[float]) -> list[float]:
    if not vals:
        return []
    m = sum(vals) / len(vals)
    var = sum((x - m) ** 2 for x in vals) / len(vals)
    sd = math.sqrt(var)
    if sd <= 1e-12:
        return [0.0] * len(vals)
    return [(x - m) / sd for x in vals]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


def _build_patch_feature(bars: list[dict[str, Any]], end_idx: int, patch_len: int = 20) -> list[float] | None:
    start_idx = end_idx - patch_len + 1
    if start_idx < 1:
        return None
    closes = [float(bars[k]["close"]) for k in range(start_idx - 1, end_idx + 1)]
    vols = [math.log1p(max(0.0, float(bars[k].get("volume") or 0.0))) for k in range(start_idx, end_idx + 1)]
    rets = [math.log(closes[i] / closes[i - 1]) if closes[i - 1] > 0 else 0.0 for i in range(1, len(closes))]
    return _zscore_vector(rets) + [0.35 * x for x in _zscore_vector(vols)]


def match_historical_analogues(
    bars: list[dict[str, Any]],
    horizon_bars: int = 15,
    patch_len: int = 20,
    top_k: int = 10,
) -> dict[str, Any]:
    """在当前截面之前检索归一化量价 Patch 余弦相似度最高的 Top-K 历史片段，严格保证候选片段及其后续 horizon_bars 结果均在当前截面之前已完全收盘（零前视泄漏）。"""
    n = len(bars)
    cur_end = n - 1
    query_vec = _build_patch_feature(bars, cur_end, patch_len=patch_len)
    # 候选片段的终点 cand_end 必须满足 cand_end + horizon_bars <= cur_end - patch_len (彻底排除重叠与未来泄漏)
    max_cand_end = cur_end - horizon_bars - patch_len
    if query_vec is None or max_cand_end < patch_len:
        return {
            "available": False,
            "reason": "历史长度不足以构建无重叠相似样本库",
            "matches": [],
            "path_Returns_by_step": [],
        }

    candidates: list[tuple[float, int]] = []
    for cand_end in range(patch_len, max_cand_end + 1):
        vec = _build_patch_feature(bars, cand_end, patch_len=patch_len)
        if vec is None:
            continue
        sim = _cosine_similarity(query_vec, vec)
        candidates.append((sim, cand_end))

    candidates.sort(key=lambda x: x[0], reverse=True)
    selected: list[tuple[float, int]] = []
    for sim, cand_end in candidates:
        if any(abs(cand_end - prev_end) < patch_len for _, prev_end in selected):
            continue
        selected.append((sim, cand_end))
        if len(selected) >= top_k:
            break

    if len(selected) < 3:
        return {
            "available": False,
            "reason": f"非重叠历史相似样本数不足 ({len(selected)} < 3)",
            "matches": [],
            "path_returns_by_step": [],
        }

    matches_meta: list[dict[str, Any]] = []
    step_returns: list[list[float]] = [[] for _ in range(horizon_bars)]
    for sim, cand_end in selected:
        base_c = float(bars[cand_end]["close"])
        if base_c <= 0:
            continue
        cum_rets: list[float] = []
        for h in range(1, horizon_bars + 1):
            fut_c = float(bars[cand_end + h]["close"])
            r = fut_c / base_c - 1.0
            cum_rets.append(round(r, 5))
            step_returns[h - 1].append(r)
        matches_meta.append(
            {
                "end_day": bars[cand_end]["day"],
                "similarity": round(sim, 4),
                "horizon_return_pct": round(cum_rets[-1] * 100.0, 2),
                "cumulative_returns": cum_rets,
            }
        )

    return {
        "available": True,
        "patch_len": patch_len,
        "top_k": len(matches_meta),
        "mean_similarity": round(sum(m["similarity"] for m in matches_meta) / len(matches_meta), 4),
        "matches": matches_meta,
        "path_returns_by_step": step_returns,
    }


SIGMA_LOOKBACK = 60
SIGMA_FLOOR = 0.004
SIGMA_CAP = 0.08
CALIBRATION_WINDOW = 160  # 参与校准的最近成熟截面数
MIN_CALIBRATION_SCORES = 20
AUDIT_WARMUP = 40  # 样本外审计前至少积累的截面数


def _local_sigma(log_rets: list[float], end: int) -> float:
    """截面 end（含 closes[end]）时可得的近 SIGMA_LOOKBACK 根日对数收益 RMS；log_rets[k] = ln(C[k+1]/C[k])。"""
    win = log_rets[max(0, end - SIGMA_LOOKBACK) : end]
    if not win:
        return 0.015
    sig = math.sqrt(sum(r * r for r in win) / len(win))
    return max(SIGMA_FLOOR, min(SIGMA_CAP, sig))


def _conformal_quantile(sorted_scores: list[float], level: float) -> float:
    """split-conformal 有限样本分位：取第 ceil((n+1)·level) 个次序统计量（超出时取最大值）。"""
    n = len(sorted_scores)
    k = min(n, max(1, math.ceil((n + 1) * level)))
    return sorted_scores[k - 1]


def _clip_cumulative_return_by_daily_limit(
    prev_price: float,
    cand_price: float,
    daily_limit_pct: float | None,
) -> float:
    if daily_limit_pct is None or prev_price <= 0:
        return max(0.01, cand_price)
    max_p = prev_price * (1.0 + daily_limit_pct)
    min_p = max(0.01, prev_price * (1.0 - daily_limit_pct))
    return max(min_p, min(max_p, cand_price))


def _build_bands_from_multipliers(
    base_c: float,
    base_day: str,
    sigma_val: float,
    mult: dict[int, tuple[float, float]],
    horizon_bars: int,
    market: str,
    asset_type: str,
    daily_limit_pct: float | None,
    vix_scale: float = 1.0,
    earnings_date: Any = None,
    horizon_dates: list[str] | None = None,
) -> tuple[list[float], list[float], list[float], list[float], list[float], list[list[float]], list[list[float]]]:
    """用同一套构造生成 H 步分位区间：中心 = 最新收盘价，逐步施加涨跌停约束并按最小价位取整。"""
    q10_arr: list[float] = []
    q25_arr: list[float] = []
    q50_arr: list[float] = []
    q75_arr: list[float] = []
    q90_arr: list[float] = []
    band_50_arr: list[list[float]] = []
    band_80_arr: list[list[float]] = []
    prev = {"q10": base_c, "q25": base_c, "q50": base_c, "q75": base_c, "q90": base_c}
    for h in range(1, horizon_bars + 1):
        event_mult = (
            1.35
            if (earnings_date and horizon_dates and horizon_dates[h - 1] >= str(earnings_date))
            else 1.0
        )
        scale = sigma_val * math.sqrt(h) * vix_scale * event_mult
        m50, m80 = mult[h]
        raw = {
            "q10": base_c * (1.0 - m80 * scale),
            "q25": base_c * (1.0 - m50 * scale),
            "q50": base_c,
            "q75": base_c * (1.0 + m50 * scale),
            "q90": base_c * (1.0 + m80 * scale),
        }
        clipped = sorted(_clip_cumulative_return_by_daily_limit(prev[k], raw[k], daily_limit_pct) for k in raw)
        r10, r25, r50, r75, r90 = [round_to_tick(x, market, asset_type, base_day) for x in clipped]
        r25 = max(r10, r25)
        r50 = max(r25, r50)
        r75 = max(r50, r75)
        r90 = max(r75, r90)
        prev = {"q10": r10, "q25": r25, "q50": r50, "q75": r75, "q90": r90}
        q10_arr.append(r10)
        q25_arr.append(r25)
        q50_arr.append(r50)
        q75_arr.append(r75)
        q90_arr.append(r90)
        band_50_arr.append([r25, r75])
        band_80_arr.append([r10, r90])
    return q10_arr, q25_arr, q50_arr, q75_arr, q90_arr, band_50_arr, band_80_arr


def compute_conformal_forecast(
    bars: list[dict[str, Any]],
    meta: dict[str, Any],
    horizon_bars: int = 15,
    extra_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """未来 H 步分位区间：以最新收盘价为中心（鞅假设，不加任何漂移），按期限 h 分别做 split-conformal 校准。

    非一致性分数 s_{i,h} = |C_{i+h}/C_i - 1| / (σ_i·√h)，σ_i 为截面 i 当时可得的近 60 日收益 RMS；
    第 h 步的 50%/80% 区间为 C_now·(1 ± m_{h,α}·σ_now·√h)，再经涨跌停截断与价位表取整得到 q10..q90。
    calibration_audit 用同一套构造（含涨跌停截断与价位取整）做滚动样本外检验，只使用在被检验截面时已完全成熟的分数，
    审计对象就是图上实际展示的区间。
    """
    market = meta["market"]
    asset_type = meta["asset_type"]
    symbol = meta["symbol"]
    ctx = extra_context or {}
    is_st = bool(ctx.get("is_st") or meta.get("is_st", False))
    is_ipo_no_limit = bool(ctx.get("is_ipo_no_limit") or meta.get("is_ipo_no_limit", False))
    last_bar = bars[-1]
    last_c = float(last_bar["close"])
    last_day = last_bar["day"]
    n = len(bars)
    horizon_dates = next_trading_dates(last_day, horizon_bars, market=market)
    estimated_dates = [d for d in horizon_dates if not calendar_covers(d, market)]

    daily_limit_pct = get_daily_price_limit_pct(
        market=market,
        asset_type=asset_type,
        symbol=symbol,
        is_st=is_st,
        is_ipo_no_limit=is_ipo_no_limit,
        day_str=last_day,
    )

    closes = [float(b["close"]) for b in bars]
    opens = [float(b["open"]) for b in bars]
    log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, n)]
    sigma_now = _local_sigma(log_rets, n - 1)

    # 仅作信息披露：隔夜跳空占比（收盘到收盘波动已包含跳空，不再额外放大区间）
    gap_rets = [math.log(opens[i] / closes[i - 1]) for i in range(max(1, n - SIGMA_LOOKBACK), n)]
    gap_sigma = math.sqrt(sum(g * g for g in gap_rets) / len(gap_rets)) if gap_rets else 0.0
    gap_ratio = round(gap_sigma / sigma_now, 3) if sigma_now > 0 else 0.0

    # 未经历史校准的事件放大项（VIX / 财报），启用时在审计中显式标注
    vix_val = ctx.get("vix")
    earnings_date = ctx.get("earnings_date")
    vix_scale = 1.0
    if market == "US" and vix_val is not None and float(vix_val) > 20.0:
        vix_scale = min(1.60, 1.0 + (float(vix_val) - 20.0) * 0.02)

    # 1. 各期限非一致性分数：scores[h][i] 对应截面 i（i + h <= n - 1 才算成熟）
    calib_start = max(SIGMA_LOOKBACK, n - 1 - horizon_bars - CALIBRATION_WINDOW)
    sigmas = {i: _local_sigma(log_rets, i) for i in range(calib_start, n)}
    scores: dict[int, list[tuple[int, float]]] = {}
    for h in range(1, horizon_bars + 1):
        rows: list[tuple[int, float]] = []
        for i in range(calib_start, n - h):
            r = closes[i + h] / closes[i] - 1.0
            rows.append((i, abs(r) / (sigmas[i] * math.sqrt(h))))
        scores[h] = rows

    calibrated = len(scores[horizon_bars]) >= MIN_CALIBRATION_SCORES
    mult: dict[int, tuple[float, float]] = {}
    for h in range(1, horizon_bars + 1):
        vals = sorted(s for _, s in scores[h])
        if calibrated and len(vals) >= MIN_CALIBRATION_SCORES:
            mult[h] = (_conformal_quantile(vals, 0.50), _conformal_quantile(vals, 0.80))
        else:
            mult[h] = (0.6745, 1.2816)  # 正态近似兜底

    # 2. 滚动样本外审计：在每个评估截面 e 只用 j + h <= e 的已成熟分数估计乘数，
    #    并通过 _build_bands_from_multipliers 施加涨跌停截断与价位取整后，直接检验实际收盘价是否落在输出区间内
    per_h_hits: dict[int, list[tuple[int, int]]] = {h: [] for h in range(1, horizon_bars + 1)}
    q50_abs_errs: list[float] = []
    rw_abs_errs: list[float] = []
    if calibrated:
        matured_by_h: dict[int, list[float]] = {h: [] for h in range(1, horizon_bars + 1)}
        ptrs: dict[int, int] = {h: 0 for h in range(1, horizon_bars + 1)}
        for e in range(calib_start, n - 1):
            for h in range(1, horizon_bars + 1):
                rows_h = scores[h]
                k = ptrs[h]
                while k < len(rows_h) and rows_h[k][0] + h <= e:
                    bisect.insort(matured_by_h[h], rows_h[k][1])
                    k += 1
                ptrs[h] = k
            max_mature_h = min(horizon_bars, n - 1 - e)
            ready_horizons = [h for h in range(1, max_mature_h + 1) if len(matured_by_h[h]) >= AUDIT_WARMUP]
            if not ready_horizons:
                continue
            mult_e: dict[int, tuple[float, float]] = {}
            for h in range(1, max_mature_h + 1):
                mh = matured_by_h[h]
                if len(mh) >= AUDIT_WARMUP:
                    mult_e[h] = (_conformal_quantile(mh, 0.50), _conformal_quantile(mh, 0.80))
                else:
                    mult_e[h] = (0.6745, 1.2816)
            e_day = bars[e]["day"]
            e_limit = get_daily_price_limit_pct(
                market=market,
                asset_type=asset_type,
                symbol=symbol,
                is_st=is_st,
                is_ipo_no_limit=is_ipo_no_limit,
                day_str=e_day,
            )
            _, _, e_q50, _, _, e_b50, e_b80 = _build_bands_from_multipliers(
                closes[e], e_day, sigmas[e], mult_e, max_mature_h, market, asset_type, e_limit
            )
            for h in ready_horizons:
                actual_c = round_to_tick(closes[e + h], market, asset_type, bars[e + h]["day"])
                lo50, hi50 = e_b50[h - 1]
                lo80, hi80 = e_b80[h - 1]
                per_h_hits[h].append((int(lo50 - 1e-6 <= actual_c <= hi50 + 1e-6), int(lo80 - 1e-6 <= actual_c <= hi80 + 1e-6)))
            if horizon_bars in ready_horizons:
                actual_term = closes[e + horizon_bars]
                base_c_e = closes[e]
                q50_abs_errs.append(abs(actual_term - e_q50[horizon_bars - 1]) / base_c_e)
                rw_abs_errs.append(abs(actual_term - base_c_e) / base_c_e)

    terminal = per_h_hits[horizon_bars]
    eval_n = len(terminal)
    cov_50 = round(sum(a for a, _ in terminal) / eval_n, 3) if eval_n else None
    cov_80 = round(sum(b for _, b in terminal) / eval_n, 3) if eval_n else None
    q50_mae_pct = round(sum(q50_abs_errs) / len(q50_abs_errs) * 100.0, 2) if q50_abs_errs else None
    rw_mae_pct = round(sum(rw_abs_errs) / len(rw_abs_errs) * 100.0, 2) if rw_abs_errs else None
    per_horizon = [
        {
            "h": h,
            "samples": len(per_h_hits[h]),
            "coverage_50": round(sum(a for a, _ in per_h_hits[h]) / len(per_h_hits[h]), 3) if per_h_hits[h] else None,
            "coverage_80": round(sum(b for _, b in per_h_hits[h]) / len(per_h_hits[h]), 3) if per_h_hits[h] else None,
        }
        for h in range(1, horizon_bars + 1)
    ]

    # 3. 生成当前截面展示区间：与样本外审计调用完全相同的 _build_bands_from_multipliers
    q10_arr, q25_arr, q50_arr, q75_arr, q90_arr, band_50_arr, band_80_arr = _build_bands_from_multipliers(
        last_c,
        last_day,
        sigma_now,
        mult,
        horizon_bars,
        market,
        asset_type,
        daily_limit_pct,
        vix_scale=vix_scale,
        earnings_date=earnings_date,
        horizon_dates=horizon_dates,
    )

    overlays = []
    if vix_scale > 1.0:
        overlays.append(f"VIX={vix_val} 放大 ×{vix_scale:.3f}")
    if earnings_date:
        overlays.append(f"财报日 {earnings_date} 之后放大 ×1.35")
    promotion_status = (
        "PROMOTED"
        if (
            calibrated
            and eval_n >= 30
            and cov_50 is not None
            and abs(cov_50 - 0.50) <= 0.10
            and cov_80 is not None
            and abs(cov_80 - 0.80) <= 0.10
            and not overlays
        )
        else "RESEARCH_CALIBRATION_WARMUP"
    )

    analogues = match_historical_analogues(bars, horizon_bars=horizon_bars, patch_len=20, top_k=10)

    return {
        "method": "PER_HORIZON_SPLIT_CONFORMAL_MARTINGALE_V6",
        "promotion_status": promotion_status,
        "horizon_bars": horizon_bars,
        "horizon_dates": horizon_dates,
        "calendar_coverage": {
            "covered": not estimated_dates,
            "coverage_end": CALENDAR_COVERAGE_END.get(market),
            "estimated_dates": estimated_dates,
        },
        "q10": q10_arr,
        "q25": q25_arr,
        "bearish_invalid_q25": q25_arr,
        "base_median_q50": q50_arr,
        "optimistic_q75": q75_arr,
        "q90": q90_arr,
        "q50_method": "MARTINGALE_LAST_CLOSE",
        "q50_note": "中位数取最新收盘价（不加规则分或类比漂移）；回测显示漂移项未优于随机游走，故不使用。",
        "conformal_50pct_band": band_50_arr,
        "conformal_80pct_band": band_80_arr,
        "daily_price_limit_pct": daily_limit_pct,
        "volatility_scaling": {
            "sigma_1d": round(sigma_now, 5),
            "sigma_lookback_bars": SIGMA_LOOKBACK,
            "overnight_gap_ratio": gap_ratio,
            "vol_scale_multiplier": round(vix_scale, 3),
            "vix": vix_val,
            "vix_source": ctx.get("vix_source"),
            "earnings_date": earnings_date,
        },
        "calibration_audit": {
            "calibrated": calibrated,
            "evaluation_samples": eval_n,
            "effective_independent_samples": eval_n // horizon_bars,
            "nominal_50pct_empirical_coverage": cov_50,
            "nominal_80pct_empirical_coverage": cov_80,
            "q50_mae_pct": q50_mae_pct,
            "random_walk_mae_pct": rw_mae_pct,
            "per_horizon_coverage": per_horizon,
            "q50_residual_multiplier": round(mult[horizon_bars][0], 3),
            "q80_residual_multiplier": round(mult[horizon_bars][1], 3),
            "uncalibrated_overlays": overlays,
            "coverage_scope_note": (
                "覆盖率为对实际输出区间（含涨跌停截断与最小价位取整的同一构造）的滚动样本外实测，只用当时已成熟的分数；"
                "逐期限边际覆盖，非整条路径联合概率；相邻截面结果期重叠，有效独立样本约为 evaluation_samples / H。"
            ),
        },
        "historical_analogues": {
            "available": analogues["available"],
            "used_in_quantiles": False,
            "top_k": analogues.get("top_k", 0),
            "mean_similarity": analogues.get("mean_similarity", 0.0),
            "matches": analogues.get("matches", [])[:5],
        },
    }
