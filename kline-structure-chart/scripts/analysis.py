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
from market_rules import get_market_microstructure_rules
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
    horizon_bars: int = 15,
    num_snapshots: int = 5,
) -> list[dict[str, Any]]:
    """预先计算历史截面的 15-bar 预测与随后真实收盘路径，供 HTML 看板时间推演滑块 (Time-Travel Replay) 零前视回放。"""
    n = len(completed)
    if n < 180:
        return []
    snapshots: list[dict[str, Any]] = []
    step = max(12, min(20, (n - 140) // max(1, num_snapshots)))
    cutoffs = [n - 1 - k * step for k in range(num_snapshots, 0, -1) if n - 1 - k * step >= 120]

    for cut_idx in cutoffs:
        sub_bars = completed[: cut_idx + 1]
        ind_sub = compute_all_indicators(sub_bars)
        reg_sub = classify_market_regime(sub_bars, ind_sub["snapshot"])
        fc_sub = compute_conformal_forecast(sub_bars, meta=meta, horizon_bars=horizon_bars)
        realized_bars = completed[cut_idx + 1 : min(n, cut_idx + 1 + horizon_bars)]
        realized_closes = [round(float(b["close"]), 2) for b in realized_bars]
        realized_days = [b["day"] for b in realized_bars]
        hit_80 = None
        if len(realized_closes) == horizon_bars:
            lo80, hi80 = fc_sub["conformal_80pct_band"][-1]
            hit_80 = lo80 <= realized_closes[-1] <= hi80
        snapshots.append(
            {
                "as_of": sub_bars[-1]["day"],
                "cut_idx": cut_idx,
                "base_close": round(float(sub_bars[-1]["close"]), 2),
                "regime": reg_sub["regime"],
                "regime_score": reg_sub["regime_score"],
                "horizon_dates": fc_sub["horizon_dates"],
                "q10": fc_sub["q10"],
                "q25": fc_sub["q25"],
                "q50": fc_sub["base_median_q50"],
                "q75": fc_sub["optimistic_q75"],
                "q90": fc_sub["q90"],
                "realized_days": realized_days,
                "realized_closes": realized_closes,
                "terminal_within_80pct_band": hit_80,
            }
        )
    return snapshots


def analyze_bundle(
    bundle: MarketDataBundle,
    request: AnalysisRequest,
    include_replay: bool = True,
    structure: dict[str, Any] | None = None,
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

    # 1. 结构层：由 structure.compute_structure_layer 统一计算（与 PNG 共用同一份结果，窗口与 --days 无关）
    layer = structure if structure is not None else compute_structure_layer(bars, intraday, show)
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
    ind_all = _indicators_on_window(hist, len(sview))
    ind_full = compute_all_indicators(completed)
    causal_pivs = causal_swing_pivots(sview, zz_thr)

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
    )
    div_patterns = detect_divergences(
        sview,
        causal_pivs,
        ind_all["series"]["rsi14"],
        ind_all["series"]["atr14"],
    )
    all_algebraic_patterns = cdl_patterns + geo_patterns + div_patterns
    smc_structures = detect_smc_structures(sview, causal_pivs, ind_all["series"]["atr14"], lookback=80)
    vol_profile = compute_volume_profile(sview, lookback=min(120, len(sview)), num_bins=24)

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
        benchmark_info = {
            "index_symbol": bench_bundle["instrument"]["symbol"],
            "index_name": bench_bundle["instrument"]["name"],
            "regime": b_reg["regime"],
            "regime_label_cn": b_reg["label_cn"],
            "regime_score": b_reg["regime_score"],
            "relative_strength_vs_index": rs_val,
            "rs_series": rs_series,
            "source": bench_bundle.get("source", "BUNDLE"),
        }
    else:
        benchmark_info = {
            "index_symbol": inst["symbol"],
            "index_name": inst["name"],
            "regime": target_regime["regime"],
            "regime_label_cn": target_regime["label_cn"],
            "regime_score": target_regime["regime_score"],
            "relative_strength_vs_index": 1.0,
            "rs_series": [],
            "source": bundle.get("provenance", {}).get("source", "SELF_INDEX"),
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

    rules = get_market_microstructure_rules(inst["market"], inst["asset_type"], inst["symbol"], day_str=view[-1]["day"])
    color_scheme = request.get("color_scheme")
    if not color_scheme or color_scheme == "auto":
        color_scheme = rules["color_scheme"]

    meta = {
        "symbol": inst["symbol"],
        "name": inst.get("name") or inst["symbol"],
        "market": inst["market"],
        "asset_type": inst["asset_type"],
        "currency": inst["currency"],
        "timeframe": (request.get("timeframes") or ["1D"])[0],
        "as_of": view[-1]["day"],
        "adjustment": bundle.get("provenance", {}).get("adjustment")
        or request.get("adjustment")
        or ("raw" if inst["asset_type"] == "index" else "split"),
        "color_scheme": color_scheme,
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
    future_proj = compute_conformal_forecast(
        completed,
        meta=meta,
        horizon_bars=horizon_bars,
        extra_context=extra_ctx,
    )

    # 8. 导出用于交互式 HTML 看板的完整时间序列
    nw_smooth = causal_nadaraya_watson([float(b["close"]) for b in view], bandwidth=3.0)
    disp_offset = len(completed) - len(view)
    view_bars_export: list[dict[str, Any]] = []
    for i, b in enumerate(view):
        full_i = disp_offset + i
        view_bars_export.append(
            {
                "idx": i,
                "day": b["day"],
                "open": round(float(b["open"]), 2),
                "high": round(float(b["high"]), 2),
                "low": round(float(b["low"]), 2),
                "close": round(float(b["close"]), 2),
                "volume": round(float(b.get("volume") or 0.0), 2),
                "complete": True,
                "ma20": round(float(ma_series[20][full_i]), 2) if ma_series[20][full_i] is not None else None,
                "ma60": round(float(ma_series[60][full_i]), 2) if ma_series[60][full_i] is not None else None,
                "ma120": round(float(ma_series[120][full_i]), 2) if ma_series[120][full_i] is not None else None,
                "ma250": round(float(ma_series[250][full_i]), 2) if ma_series[250][full_i] is not None else None,
                "kernel_smooth": round(nw_smooth[i], 2),
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
        _build_replay_snapshots(completed, meta=meta, horizon_bars=horizon_bars, num_snapshots=5)
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
            "wedge_structure": wedge_clean,
        },
    }

    report: AnalysisReport = {
        "schema_version": "5.0.0",
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
                needs_benchmark=inst["asset_type"] != "index",
                intraday_day=active_intra["day"] if active_intra else None,
            ),
            "provenance_errors": list(bundle.get("provenance", {}).get("errors") or []),
            "completed_bars_total": len(completed),
            "display_bars": len(view),
            "structure_bars": len(sview),
            "forecast_promotion_status": future_proj["promotion_status"],
        },
        "reproducibility": {
            "engine_version": "5.0.0",
            "bars_hash": compute_canonical_hash([(b["day"], b["close"]) for b in completed[-120:]]),
            "report_hash": "",
        },
    }
    adj_status = bundle.get("provenance", {}).get("adjustment_status") or {}
    if adj_status.get("requested", "raw") != "raw" and adj_status.get("applied") == "raw":
        report["quality"]["notes"].append(
            f"复权未生效（{adj_status.get('error')}），价格为不复权原始价，除权除息缺口可能被误识别为跳空/FVG"
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
    validate_report(report)
    return report
