from __future__ import annotations

from decimal import Decimal
from typing import Any

from contracts import (
    AnalysisReport,
    AnalysisRequest,
    MarketDataBundle,
    assess_data_quality,
    compute_canonical_hash,
    validate_report,
)
from forecast import compute_conformal_forecast
from indicators import compute_all_indicators
from market_rules import (
    DEFAULT_BENCHMARKS,
    get_market_microstructure_rules,
    price_decimals,
    rescale_bars_as_of,
    round_to_tick,
)
from patterns import (
    causal_nadaraya_watson,
    causal_swing_pivots,
    compute_volume_profile,
    detect_candlestick_patterns,
    detect_divergences,
    detect_geometric_patterns,
    detect_smc_structures,
)
from structure import HISTORY_BARS, STRUCT_DAYS, compute_structure_layer
from risk import compute_triple_barrier_plan
from strategies import (
    classify_market_regime,
    compute_multi_timeframe_score,
    evaluate_five_core_strategies,
)


def _compute_relative_strength(
    target_bars: list[dict[str, Any]],
    bench_bars: list[dict[str, Any]] | None,
    lookback: int = 20,
) -> tuple[float, list[dict[str, Any]]]:
    if not bench_bars or len(target_bars) < 5 or len(bench_bars) < 5:
        return 1.0, []
    bench_map = {b["day"]: float(b["close"]) for b in bench_bars if float(b["close"]) > 0}
    aligned: list[tuple[str, float, float]] = []
    for tb in target_bars:
        d = tb["day"]
        if d in bench_map and float(tb["close"]) > 0:
            aligned.append((d, float(tb["close"]), bench_map[d]))
    if len(aligned) < 5:
        return 1.0, []
    win = min(lookback, len(aligned) - 1)
    t0, b0 = aligned[-1 - win][1], aligned[-1 - win][2]
    t1, b1 = aligned[-1][1], aligned[-1][2]
    t_ret = t1 / t0 if t0 > 0 else 1.0
    b_ret = b1 / b0 if b0 > 0 else 1.0
    rs_ratio = round(t_ret / b_ret, 4) if b_ret > 0 else 1.0

    # Normalized 100-base RS series over display window
    sub = aligned[-min(120, len(aligned)) :]
    base_t, base_b = sub[0][1], sub[0][2]
    rs_series = [
        {
            "day": d,
            "target_norm": round(tc / base_t * 100.0, 2),
            "benchmark_norm": round(bc / base_b * 100.0, 2),
            "rs_line": round((tc / base_t) / (bc / base_b) * 100.0, 2),
        }
        for d, tc, bc in sub
    ]
    return rs_ratio, rs_series


def _tail_series(obj: Any, k: int) -> Any:
    if isinstance(obj, dict):
        return {key: _tail_series(val, k) for key, val in obj.items()}
    if isinstance(obj, list):
        return obj[-k:]
    return obj


def _indicators_on_window(hist: list[dict[str, Any]], window_len: int) -> dict[str, Any]:
    """在完整历史上计算指标（保证长周期均线有预热），序列截取最后 window_len 根与结构窗口对齐，快照取历史最后一根。"""
    ind = compute_all_indicators(hist)
    return {"series": _tail_series(ind["series"], window_len), "snapshot": ind["snapshot"]}


def _build_replay_snapshots(
    completed: list[dict[str, Any]],
    meta: dict[str, Any],
    benchmark_info: dict[str, Any],
    benchmark_bars: list[dict[str, Any]] | None = None,
    horizon_bars: int = 15,
    num_snapshots: int = 5,
    snapshot_window: int = 60,
) -> list[dict[str, Any]]:
    """预先计算历史截面的 15-bar 预测、完整多周期规则分、截至该截面日的历史K线窗口与随后真实收盘路径，
    供 HTML 看板时间推演滑块 (Time-Travel Replay) 零前视回放（即使 --days=60 也不会泄漏截面之后的K线）。
    """
    n = len(completed)
    if n < 180:
        return []
    snapshots: list[dict[str, Any]] = []
    step = max(12, min(20, (n - 140) // max(1, num_snapshots)))
    cutoffs = [n - 1 - k * step for k in range(num_snapshots, 0, -1) if n - 1 - k * step >= 120]

    for cut_idx in cutoffs:
        pit_ext = rescale_bars_as_of(completed[: min(n, cut_idx + 1 + horizon_bars)], cut_idx)
        sub_bars = pit_ext[: cut_idx + 1]
        sub_p_dec = price_decimals(float(sub_bars[-1]["close"]), meta["market"], meta["asset_type"], sub_bars[-1]["day"])
        layer_sub = compute_structure_layer(
            sub_bars,
            None,
            snapshot_window,
            price_decimals=lambda price, day: price_decimals(price, meta["market"], meta["asset_type"], day),
            horizon_bars=horizon_bars,
            round_price=lambda price, day: round_to_tick(price, meta["market"], meta["asset_type"], day),
        )
        sview_sub = layer_sub["sview"]
        ind_sub_win = _indicators_on_window(layer_sub["hist"], len(sview_sub))
        ind_sub_full = compute_all_indicators(sub_bars)
        piv_sub = causal_swing_pivots(sview_sub, layer_sub["zz_thr"])
        cdl_sub = detect_candlestick_patterns(
            sview_sub, ind_sub_win["series"]["atr14"], ind_sub_win["series"]["rvol20"], lookback=12
        )
        geo_sub = detect_geometric_patterns(
            sview_sub, piv_sub, ind_sub_win["series"]["atr14"], ind_sub_win["series"]["rvol20"]
        )
        div_sub = detect_divergences(
            sview_sub, piv_sub, ind_sub_win["series"]["rsi14"], ind_sub_win["series"]["atr14"]
        )
        all_pats_sub = cdl_sub + geo_sub + div_sub
        smc_sub = detect_smc_structures(sview_sub, piv_sub, ind_sub_win["series"]["atr14"], lookback=80)
        reg_sub = classify_market_regime(sview_sub, ind_sub_win["snapshot"])
        strat_sub = evaluate_five_core_strategies(sview_sub, ind_sub_win, reg_sub)
        wedge_sub = {k: v for k, v in layer_sub["wedge"].items() if not k.startswith("_")}
        bench_info_sub = benchmark_info
        if benchmark_bars:
            cut_day = sub_bars[-1]["day"]
            b_sub = [b for b in benchmark_bars if str(b["day"]) <= cut_day]
            if b_sub:
                b_ind_sub = compute_all_indicators(b_sub[-HISTORY_BARS:])
                b_reg_sub = classify_market_regime(b_sub[-STRUCT_DAYS:], b_ind_sub["snapshot"])
                rs_sub, _ = _compute_relative_strength(sub_bars, b_sub, lookback=20)
                bench_info_sub = {
                    **benchmark_info,
                    "regime": b_reg_sub["regime"],
                    "regime_label_cn": b_reg_sub["label_cn"],
                    "regime_score": b_reg_sub["regime_score"],
                    "relative_strength_vs_index": rs_sub,
                    "benchmark_missing": False,
                }
        mtf_sub = compute_multi_timeframe_score(
            sub_bars, ind_sub_win, wedge_sub, all_pats_sub, smc_sub, strat_sub, bench_info_sub
        )
        fc_sub = compute_conformal_forecast(sub_bars, meta=meta, horizon_bars=horizon_bars)
        realized_bars = pit_ext[cut_idx + 1 : min(len(pit_ext), cut_idx + 1 + horizon_bars)]
        realized_closes = [round(float(b["close"]), sub_p_dec) for b in realized_bars]
        realized_days = [b["day"] for b in realized_bars]
        hit_80 = None
        if len(realized_closes) == horizon_bars:
            lo80, hi80 = fc_sub["conformal_80pct_band"][-1]
            act_last = round_to_tick(realized_closes[-1], meta["market"], meta["asset_type"], realized_days[-1])
            hit_80 = lo80 - 1e-6 <= act_last <= hi80 + 1e-6

        win_bars = layer_sub["view"]
        win_offset = len(sub_bars) - len(win_bars)
        ma_sub = layer_sub["ma"]
        nw_sub = causal_nadaraya_watson([float(b["close"]) for b in win_bars], bandwidth=3.0)
        bars_win_export: list[dict[str, Any]] = []
        for i, b in enumerate(win_bars):
            full_i = win_offset + i
            bars_win_export.append(
                {
                    "idx": i,
                    "day": b["day"],
                    "open": round(float(b["open"]), sub_p_dec),
                    "high": round(float(b["high"]), sub_p_dec),
                    "low": round(float(b["low"]), sub_p_dec),
                    "close": round(float(b["close"]), sub_p_dec),
                    "volume": round(float(b.get("volume") or 0.0), 2),
                    "ma20": round(float(ma_sub[20][full_i]), sub_p_dec) if ma_sub[20][full_i] is not None else None,
                    "ma60": round(float(ma_sub[60][full_i]), sub_p_dec) if ma_sub[60][full_i] is not None else None,
                    "ma120": round(float(ma_sub[120][full_i]), sub_p_dec) if ma_sub[120][full_i] is not None else None,
                    "ma250": round(float(ma_sub[250][full_i]), sub_p_dec) if ma_sub[250][full_i] is not None else None,
                    "kernel_smooth": round(nw_sub[i], sub_p_dec),
                    "rsi14": round(float(ind_sub_full["series"]["rsi14"][full_i]), 2)
                    if ind_sub_full["series"]["rsi14"][full_i] is not None
                    else None,
                    "rvol20": round(float(ind_sub_full["series"]["rvol20"][full_i]), 2)
                    if ind_sub_full["series"]["rvol20"][full_i] is not None
                    else None,
                }
            )

        snapshots.append(
            {
                "as_of": sub_bars[-1]["day"],
                "cut_idx": cut_idx,
                "base_close": round(float(sub_bars[-1]["close"]), sub_p_dec),
                "regime": reg_sub["regime"],
                "regime_score": reg_sub["regime_score"],
                "benchmark_regime_score": bench_info_sub.get("regime_score"),
                "benchmark_relative_strength": bench_info_sub.get("relative_strength_vs_index"),
                "rule_score": mtf_sub["rule_score"],
                "rating": mtf_sub["rating"],
                "horizon_dates": fc_sub["horizon_dates"],
                "q10": fc_sub["q10"],
                "q25": fc_sub["q25"],
                "q50": fc_sub["base_median_q50"],
                "q75": fc_sub["optimistic_q75"],
                "q90": fc_sub["q90"],
                "realized_days": realized_days,
                "realized_closes": realized_closes,
                "terminal_within_80pct_band": hit_80,
                "bars_window": bars_win_export,
            }
        )
    return snapshots


def analyze_bundle(
    bundle: MarketDataBundle,
    request: AnalysisRequest,
    include_replay: bool = True,
    structure: dict[str, Any] | None = None,
    include_forecast: bool = True,
) -> AnalysisReport:
    """100% 纯数学计算核心：从 OHLCV 矩阵计算全部指标、形态证据、SMC、多周期共振分、三重屏障与保形预测。"""
    target = bundle["target"]
    inst = target["instrument"]
    bars = target["bars"]
    intraday = target.get("intraday")
    extra_ctx = bundle.get("extra_context") or {}

    show = int(request.get("display_bars") or 120)
    struct_days = int(request.get("structure_bars") or STRUCT_DAYS)
    horizon_bars = int(request.get("horizon_bars") or 15)
    bench_bars_raw = (bundle.get("benchmark") or {}).get("bars")

    # 1. 结构层：由 structure.compute_structure_layer 统一计算（与 PNG 共用同一份结果，窗口与 --days 无关）
    layer = (
        structure
        if structure is not None
        else compute_structure_layer(
            bars,
            intraday,
            show,
            price_decimals=lambda price, day: price_decimals(price, inst["market"], inst["asset_type"], day),
            horizon_bars=horizon_bars,
            round_price=lambda price, day: round_to_tick(price, inst["market"], inst["asset_type"], day),
        )
    )
    completed, active_intra, data_note = layer["completed"], layer["active_intra"], layer["data_note"]
    if len(completed) < 60:
        raise ValueError(f"Insufficient completed bars ({len(completed)} < 60) for {inst['symbol']}")
    view, hist, sview, off = layer["view"], layer["hist"], layer["sview"], layer["off"]
    last_c = layer["last_c"]
    pivots, zz_thr = layer["pivots"], layer["zz_thr"]
    wedge_clean = {k: v for k, v in layer["wedge"].items() if not k.startswith("_")}
    scenario, fibs, gaps = layer["scenario"], layer["fibs"], layer["gaps"]
    ma_series = layer["ma"]
    background, ladder, projections = layer["background"], layer["ladder"], layer["projections"]

    # 2. 全量数学指标与 Qlib 风格量价因子计算：在固定 HISTORY_BARS 历史上预热（EMA200/SMA250 需要足够样本，且与 --days 无关），
    #    再把序列截到结构窗口长度，保持与 sview 下标对齐
    p_dec = price_decimals(float(last_c), inst["market"], inst["asset_type"], view[-1]["day"])
    ind_all = _indicators_on_window(hist, len(sview))
    ind_full = compute_all_indicators(completed) if include_forecast else ind_all
    causal_pivs = causal_swing_pivots(sview, zz_thr, price_decimals=p_dec)

    # 3. 100% 代数形态、背离、SMC 与 Volume Profile
    cdl_patterns = detect_candlestick_patterns(
        sview,
        ind_all["series"]["atr14"],
        ind_all["series"]["rvol20"],
        lookback=12,
    )
    geo_patterns = detect_geometric_patterns(
        sview,
        causal_pivs,
        ind_all["series"]["atr14"],
        ind_all["series"]["rvol20"],
        price_decimals=p_dec,
    )
    div_patterns = detect_divergences(
        sview,
        causal_pivs,
        ind_all["series"]["rsi14"],
        ind_all["series"]["atr14"],
    )
    all_algebraic_patterns = cdl_patterns + geo_patterns + div_patterns
    smc_structures = detect_smc_structures(sview, causal_pivs, ind_all["series"]["atr14"], lookback=80, price_decimals=p_dec)
    vol_profile = compute_volume_profile(sview, lookback=min(120, len(sview)), num_bins=24, price_decimals=p_dec)

    # 4. 大盘基准状态与相对强弱（先大盘、后个股）
    target_regime = classify_market_regime(
        sview,
        ind_all["snapshot"],
        vix_value=extra_ctx.get("vix"),
    )
    bench_bundle = bundle.get("benchmark")
    if bench_bundle and bench_bundle.get("bars"):
        b_bars = bench_bundle["bars"]
        b_ind = compute_all_indicators(b_bars[-HISTORY_BARS:])
        b_reg = classify_market_regime(b_bars[-struct_days:], b_ind["snapshot"], vix_value=extra_ctx.get("vix"))
        rs_val, rs_series = _compute_relative_strength(completed, b_bars, lookback=20)
        b_ret1 = (
            round(float(b_bars[-1]["close"]) / float(b_bars[-2]["close"]) - 1.0, 6)
            if len(b_bars) >= 2 and float(b_bars[-2]["close"]) > 0
            else 0.0
        )
        b_ret5 = (
            round(float(b_bars[-1]["close"]) / float(b_bars[-6]["close"]) - 1.0, 6)
            if len(b_bars) >= 6 and float(b_bars[-6]["close"]) > 0
            else 0.0
        )
        b_bull = bool(float(b_bars[-1]["close"]) > float(b_bars[-1]["open"]))
        benchmark_info = {
            "index_symbol": bench_bundle["instrument"]["symbol"],
            "index_name": bench_bundle["instrument"]["name"],
            "regime": b_reg["regime"],
            "regime_label_cn": b_reg["label_cn"],
            "regime_score": b_reg["regime_score"],
            "relative_strength_vs_index": rs_val,
            "rs_series": rs_series,
            "bench_ret1": b_ret1,
            "bench_ret5": b_ret5,
            "bench_bull_bar": b_bull,
            "source": bench_bundle.get("source", "BUNDLE"),
            "benchmark_missing": False,
        }
    else:
        # 指数或默认基准本身无需外部基准；个股缺少基准（如本地文件输入）时只能用自身状态占位，并显式标记
        is_market_benchmark = (
            inst["asset_type"] == "index"
            or inst["symbol"].upper() == DEFAULT_BENCHMARKS.get(inst["market"], {}).get("symbol", "").upper()
        )
        benchmark_info = {
            "index_symbol": inst["symbol"],
            "index_name": inst["name"],
            "regime": target_regime["regime"],
            "regime_label_cn": target_regime["label_cn"],
            "regime_score": target_regime["regime_score"],
            "relative_strength_vs_index": 1.0,
            "rs_series": [],
            "source": bundle.get("provenance", {}).get("source", "SELF_INDEX"),
            "benchmark_missing": not is_market_benchmark,
        }

    # 5. 五大策略池与多周期共振评分
    strategy_signals = evaluate_five_core_strategies(sview, ind_all, target_regime)
    mtf_score = compute_multi_timeframe_score(
        completed,
        ind_all,
        wedge_clean,
        all_algebraic_patterns,
        smc_structures,
        strategy_signals,
        benchmark_info,
    )

    is_st = bool(inst.get("is_st") or request.get("is_st") or extra_ctx.get("is_st", False))
    is_ipo_no_limit = bool(request.get("is_ipo_no_limit") or extra_ctx.get("is_ipo_no_limit", False))
    inst_name = str(request.get("name") or inst.get("name") or inst["symbol"])
    rules = get_market_microstructure_rules(
        inst["market"],
        inst["asset_type"],
        inst["symbol"],
        is_st=is_st,
        is_ipo_no_limit=is_ipo_no_limit,
        day_str=view[-1]["day"],
        board_lot=inst.get("lot_size") if inst["market"] == "HK" else None,
        name=inst_name,
    )
    color_scheme = request.get("color_scheme")
    if not color_scheme or color_scheme == "auto":
        color_scheme = rules["color_scheme"]

    p_dec = price_decimals(float(last_c), inst["market"], inst["asset_type"], view[-1]["day"])
    meta = {
        "symbol": inst["symbol"],
        "name": request.get("name") or inst.get("name") or inst["symbol"],
        "market": inst["market"],
        "asset_type": inst["asset_type"],
        "is_st": rules.get("is_st", is_st),
        "is_ipo_no_limit": is_ipo_no_limit,
        # 只传递真实取得的港股每手股数；假设值不写入 meta，避免下游把假设当成已知
        "board_lot": inst.get("lot_size") if inst["market"] == "HK" and inst["asset_type"] != "index" else None,
        "currency": inst["currency"],
        "timeframe": (request.get("timeframes") or ["1D"])[0],
        "as_of": view[-1]["day"],
        "adjustment": bundle.get("provenance", {}).get("adjustment")
        or request.get("adjustment")
        or ("raw" if inst["asset_type"] == "index" else "split"),
        "color_scheme": color_scheme,
        "price_decimals": p_dec,
        "data_source": bundle.get("provenance", {}).get("source", "UNKNOWN"),
    }

    # 6. 二阶段过滤、三重屏障风控与多空辩论
    meta_decision, tb_plan, debate = compute_triple_barrier_plan(
        sview,
        meta=meta,
        ind_snapshot=ind_all["snapshot"],
        wedge=wedge_clean,
        ladder=ladder,
        projections=projections,
        mtf_score=mtf_score,
        algebraic_patterns=all_algebraic_patterns,
        benchmark_info=benchmark_info,
        horizon_bars=horizon_bars,
        account_equity=request.get("account_equity"),
        max_risk_pct=float(request.get("max_risk_pct") or 0.01),
    )

    # 7. 未来 H 步（默认 15 日）分位数预测 + 序列保形预测校准
    if include_forecast:
        future_proj = compute_conformal_forecast(
            completed,
            meta=meta,
            horizon_bars=horizon_bars,
            extra_context=extra_ctx,
        )
    else:
        base_p = round(float(last_c), p_dec)
        future_proj = {
            "horizon_bars": horizon_bars,
            "horizon_dates": [],
            "q10": [base_p] * horizon_bars,
            "q25": [base_p] * horizon_bars,
            "base_median_q50": [base_p] * horizon_bars,
            "optimistic_q75": [base_p] * horizon_bars,
            "q90": [base_p] * horizon_bars,
            "conformal_80pct_band": [[base_p, base_p]] * horizon_bars,
            "promotion_status": "RESEARCH_CALIBRATION_WARMUP",
            "calendar_coverage": {"covered": True, "coverage_end": "2026-12-31", "estimated_dates": []},
        }

    # 8. 导出用于交互式 HTML 看板的完整时间序列（按品种最小价位保留小数，避免 ETF 0.001 价位被截成 2 位小数）
    p_dec = price_decimals(float(last_c), inst["market"], inst["asset_type"], view[-1]["day"])
    disp_offset = len(completed) - len(view)
    view_bars_export: list[dict[str, Any]] = []
    if include_forecast:
        nw_smooth = causal_nadaraya_watson([float(b["close"]) for b in view], bandwidth=3.0)
        for i, b in enumerate(view):
            full_i = disp_offset + i
            view_bars_export.append(
                {
                    "idx": i,
                    "day": b["day"],
                    "open": round(float(b["open"]), p_dec),
                    "high": round(float(b["high"]), p_dec),
                    "low": round(float(b["low"]), p_dec),
                    "close": round(float(b["close"]), p_dec),
                    "volume": round(float(b.get("volume") or 0.0), 2),
                    "complete": True,
                    "ma20": round(float(ma_series[20][full_i]), p_dec) if ma_series[20][full_i] is not None else None,
                    "ma60": round(float(ma_series[60][full_i]), p_dec) if ma_series[60][full_i] is not None else None,
                    "ma120": round(float(ma_series[120][full_i]), p_dec) if ma_series[120][full_i] is not None else None,
                    "ma250": round(float(ma_series[250][full_i]), p_dec) if ma_series[250][full_i] is not None else None,
                    "kernel_smooth": round(nw_smooth[i], p_dec),
                    "rsi14": round(float(ind_full["series"]["rsi14"][full_i]), 2)
                    if ind_full["series"]["rsi14"][full_i] is not None
                    else None,
                    "macd_hist": round(float(ind_full["series"]["macd"]["hist"][full_i]), 3)
                    if ind_full["series"]["macd"]["hist"][full_i] is not None
                    else None,
                    "rvol20": round(float(ind_full["series"]["rvol20"][full_i]), 2)
                    if ind_full["series"]["rvol20"][full_i] is not None
                    else None,
                }
            )

    replay_snapshots = (
        _build_replay_snapshots(
            completed,
            meta=meta,
            benchmark_info=benchmark_info,
            benchmark_bars=bench_bars_raw,
            horizon_bars=horizon_bars,
            num_snapshots=5,
        )
        if include_replay
        else []
    )

    sup_zone = (
        [ladder["supports"][1]["price"], ladder["supports"][0]["price"]]
        if len(ladder["supports"]) >= 2
        else ([ladder["supports"][0]["price"], ladder["supports"][0]["price"]] if ladder["supports"] else [float(last_c) * 0.97, float(last_c) * 0.98])
    )
    res_zone = (
        [ladder["resistances"][0]["price"], ladder["resistances"][1]["price"]]
        if len(ladder["resistances"]) >= 2
        else ([ladder["resistances"][0]["price"], ladder["resistances"][0]["price"]] if ladder["resistances"] else [float(last_c) * 1.02, float(last_c) * 1.03])
    )

    math_indicators = {
        "trend": ind_all["snapshot"]["trend"],
        "momentum": ind_all["snapshot"]["momentum"],
        "volatility": ind_all["snapshot"]["volatility"],
        "qlib_factors": ind_all["snapshot"]["qlib_factors"],
        "structure_levels": {
            "poc_price": vol_profile["poc_price"],
            "vah_price": vol_profile["vah_price"],
            "val_price": vol_profile["val_price"],
            "support_zone": sorted(sup_zone),
            "resistance_zone": sorted(res_zone),
            "key_levels": ladder,
            "fib_levels": fibs,
            "recent_gaps": gaps,
            "abc_scenario": scenario,
            "wave_counts": layer["wave_counts"],
            "wedge_structure": wedge_clean,
        },
    }

    report: AnalysisReport = {
        "schema_version": "5.3.0",
        "meta": meta,
        "request": request,
        "market_benchmark": benchmark_info,
        "mathematical_indicators": math_indicators,
        "algebraic_patterns_detected": all_algebraic_patterns,
        "smc_structures": smc_structures,
        "volume_profile": vol_profile,
        "market_regime": target_regime,
        "strategy_signals": strategy_signals,
        "multi_timeframe_score": mtf_score,
        "meta_labeling_decision": meta_decision,
        "triple_barrier_plan": tb_plan,
        "rule_scenarios": projections,
        "future_projection_15d": future_proj,
        "debate_and_veto": debate,
        "replay_snapshots": replay_snapshots,
        "series_export": {
            "bars": view_bars_export,
            "pivots": [dict(p, idx=p["idx"] + off) for p in pivots if p["idx"] + off >= 0],
            "background": background,
            "intraday": active_intra,
            "data_note": data_note,
        },
        "quality": {
            "data_source": meta["data_source"],
            **assess_data_quality(
                meta["data_source"],
                bench_bundle.get("source", "UNKNOWN") if bench_bundle else None,
                needs_benchmark=(
                    inst["asset_type"] != "index"
                    and inst["symbol"].upper() != DEFAULT_BENCHMARKS.get(inst["market"], {}).get("symbol", "").upper()
                ),
                intraday_day=active_intra["day"] if active_intra else None,
            ),
            "provenance_errors": list(bundle.get("provenance", {}).get("errors") or []),
            "completed_bars_total": len(completed),
            "display_bars": len(view),
            "structure_bars": len(sview),
            "forecast_promotion_status": future_proj["promotion_status"],
        },
        "reproducibility": {
            "engine_version": "5.3.0",
            "bars_hash": compute_canonical_hash([(b["day"], b["close"]) for b in completed[-120:]]),
            "report_hash": "",
        },
    }
    adj_status = bundle.get("provenance", {}).get("adjustment_status") or {}
    if adj_status.get("requested", "raw") != "raw" and adj_status.get("applied") == "raw":
        report["quality"]["notes"].append(
            f"复权未生效（{adj_status.get('error')}），价格为不复权原始价，除权除息缺口可能被误识别为跳空/FVG"
        )
    missing_session = bundle.get("provenance", {}).get("missing_closed_session")
    if missing_session:
        report["quality"]["notes"].append(
            f"数据只到 {completed[-1]['day']}：最近已收盘的交易日 {missing_session} 的日线数据源尚未提供（或标的停牌），"
            "本次分析不含该日行情，可稍后重跑"
        )
    # 回放快照沿用整份 provenance，只披露截至本次最后一根已收盘K线的剔除日期
    placeholders = {
        "target": [d for d in bundle.get("provenance", {}).get("placeholder_bars_removed") or [] if d <= completed[-1]["day"]],
        "benchmark": [d for d in (bench_bundle or {}).get("placeholder_bars_removed") or [] if d <= completed[-1]["day"]],
    }
    report["quality"]["placeholder_bars_removed"] = placeholders
    bench_symbol = ((bench_bundle or {}).get("instrument") or {}).get("symbol", "")
    for key, label in (("target", "标的"), ("benchmark", f"基准 {bench_symbol}".strip())):
        removed = placeholders[key]
        if removed:
            shown = "、".join(removed) if len(removed) <= 10 else f"最近 10 根为 {'、'.join(removed[-10:])}"
            report["quality"]["notes"].append(
                f"{label}日线剔除 {len(removed)} 根占位K线（成交量为 0 且开高低收四价相同，多为半日市缺数、停牌或停市日"
                f"按前收盘价补出，非真实成交），未用其他来源补数：{shown}"
            )
    inst_meta = bundle.get("provenance", {}).get("instrument_meta") or {}
    if rules.get("lot_size_source") == "ASSUMED_DEFAULT":
        report["quality"]["notes"].append(
            f"港股每手股数未能从行情快照获取（{inst_meta.get('error')}），仓位按 100 股/手假设计算，下单前请向券商核对"
        )
    if inst["market"] == "CN" and inst["asset_type"] != "index" and inst_meta.get("error") and not request.get("is_st"):
        report["quality"]["notes"].append(
            f"证券简称未能从行情快照获取（{inst_meta.get('error')}），ST 风险警示状态只依据 --st / --name 判断"
        )
    cal = future_proj["calendar_coverage"]
    if not cal["covered"]:
        report["quality"]["notes"].append(
            f"未来 {len(cal['estimated_dates'])} 个交易日超出 {inst['market']} 休市表覆盖范围（至 {cal['coverage_end']}），"
            "按周一至周五估算，节假日未扣除"
        )
    report["reproducibility"]["report_hash"] = compute_canonical_hash(
        {
            "meta": report["meta"],
            "indicators": report["mathematical_indicators"]["trend"],
            "score": report["multi_timeframe_score"]["rule_score"],
            "tb": report["triple_barrier_plan"]["entry_zone"],
            "q50": report["future_projection_15d"]["base_median_q50"],
        }
    )
    if include_forecast:
        validate_report(report)
    return report
