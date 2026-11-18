"""v6 输出契约：把流水线内部报告投影为稳定的 result.json 与非稳定的 chart_data.json。

内部报告（analysis.analyze_bundle 的返回值）沿用 v5 字段名，供回测、历史回放与单元测试使用；
对外只写本模块投影出的两个文件。字段定义与稳定性规则见 assets/analysis.schema.json 与 references/output-fields.md。
"""
from __future__ import annotations

import json
from typing import Any, Callable

from market_rules import MARKET_SESSIONS
from price_text import collect_price_whitelist

SCHEMA_VERSION = "6.0.0"

_REQUEST_KEYS = (
    "symbol", "market", "asset_type", "display_bars", "structure_bars", "horizon_bars",
    "max_risk_pct", "color_scheme", "as_of", "adjustment", "benchmark_symbol",
)
# 只用于画路径的字段放进 chart_data，不进入稳定契约
_SCENARIO_DRAW_KEYS = ("waypoints_offset", "step_labels", "eta_bars")
_SCENARIO_DROP_KEYS = (*_SCENARIO_DRAW_KEYS, "target_within_horizon")
_QUOTABLE_SECTIONS = ("structure", "indicators", "scenarios", "plan", "volatility_range")
# result.json 落盘（UTF-8、indent=2）的体积预算；超出时按顺序裁剪基准结构摘要与次级别价区，见 _trim_to_size_budget
RESULT_SIZE_BUDGET_BYTES = 80 * 1024
BENCH_KEY_LEVELS_KEEP = 5  # 裁剪关键价位时每侧保留离基准收盘最近的档数


def artifact_files(safe_sym: str, has_backtest: bool) -> dict[str, str]:
    files = {
        "result_json": f"{safe_sym}_result.json",
        "chart_data_json": f"{safe_sym}_chart_data.json",
        "preview_png": f"{safe_sym}_preview.png",
        "dashboard_html": f"{safe_sym}_dashboard.html",
        "report_md": f"{safe_sym}_report.md",
    }
    if has_backtest:
        files["backtest_json"] = f"{safe_sym}_backtest.json"
    return files


def _strip_scenario(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in _SCENARIO_DROP_KEYS}


def _draw_only(item: dict[str, Any]) -> dict[str, Any]:
    return {k: item[k] for k in _SCENARIO_DRAW_KEYS if k in item}


def _market_layer_fields(ml: dict[str, Any] | None) -> dict[str, Any]:
    """内部 market_layer → market_context 的三个实验字段。缺少基准时三者都为 None。"""
    if ml is None:
        return {"structure": None, "cycle": None, "alignment": None}
    s = ml.get("structure")
    structure = None
    if s is not None:
        pj = s["projections"]
        structure = {
            "last_close": round(s["last_close"], int(ml["benchmark_price_decimals"])),
            "channel": s["wedge"],
            "key_levels": s["ladder"],
            "abc_scenario": s["scenario"],
            "wave_counts": s["wave_counts"],
            "patterns": [{k: v for k, v in p.items() if k != "invalidated"} for p in s["patterns"]],
            "scenarios": {
                "breakout_level": pj["breakout_level"],
                "breakdown_level": pj["breakdown_level"],
                "active": [_strip_scenario(x) for x in pj.get("scenarios") or []],
                "impulse_wave": pj.get("impulse_wave"),
            },
        }
    return {"structure": structure, "cycle": ml["cycle"], "alignment": ml.get("alignment")}


def build_result(report: dict[str, Any], files: dict[str, str]) -> dict[str, Any]:
    meta = report["meta"]
    request = report.get("request") or {}
    dec = report["meta_labeling_decision"]
    mtf = report["multi_timeframe_score"]
    deb = report["debate_and_veto"]
    mb = report["market_benchmark"]
    ml = report.get("market_layer")
    ml_fields = _market_layer_fields(ml)
    mi = report["mathematical_indicators"]
    sl = mi["structure_levels"]
    rs = report["rule_scenarios"]
    tb = report["triple_barrier_plan"]
    fp = report["future_projection_15d"]
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            **meta,
            "timezone": MARKET_SESSIONS[meta["market"]][0],
            "engine_version": SCHEMA_VERSION,
            "request": {k: request[k] for k in _REQUEST_KEYS if k in request},
        },
        "quality": dict(report["quality"]),
        "verdict": {
            "action": dec["action"],
            "action_cn": dec["action_cn"],
            "setup_type": dec["setup_type"],
            "setup_label_cn": dec["setup_label_cn"],
            "risk_veto": bool(deb["risk_officer_veto"]),
            "veto_reasons": list(deb.get("veto_reasons") or []),
            "veto_codes": list(deb.get("veto_codes") or []),
            "rule_score": mtf["rule_score"],
            "rating": mtf["rating"],
            "rating_cn": mtf["rating_cn"],
            "headline_cn": deb["final_verdict_cn"],
            "overextended": bool(dec.get("overextended")),
            "rule_detail": {
                "components": mtf.get("components"),
                "components_total": mtf.get("components_total"),
                "regime_adjustment": mtf.get("regime_adjustment"),
                "timeframes_aligned": mtf.get("timeframes_aligned"),
                "scoring_method": dec.get("scoring_method"),
                "note": mtf.get("note"),
            },
            "strategy_signals": list(report.get("strategy_signals") or []),
            "anti_quant_traps": dec.get("anti_quant_traps"),
            "bull_case": list(deb.get("bull_case") or []),
            "bear_case": list(deb.get("bear_case") or []),
        },
        "market_context": {
            "index_symbol": mb.get("index_symbol"),
            "index_name": mb.get("index_name"),
            "regime": mb.get("regime"),
            "regime_label_cn": mb.get("regime_label_cn"),
            "regime_score": mb.get("regime_score"),
            "relative_strength_vs_index": mb.get("relative_strength_vs_index"),
            "benchmark_missing": bool(mb.get("benchmark_missing")),
            "source": mb.get("source"),
            "bench_ret1": mb.get("bench_ret1"),
            "bench_ret5": mb.get("bench_ret5"),
            **ml_fields,
        },
        "structure": {
            "last_close": float(report["series_export"]["bars"][-1]["close"]),
            "regime": report["market_regime"],
            "key_levels": sl["key_levels"],
            "support_zone": sl["support_zone"],
            "resistance_zone": sl["resistance_zone"],
            "channel": sl["wedge_structure"],
            "fib_levels": sl["fib_levels"],
            "recent_gaps": sl["recent_gaps"],
            "abc_scenario": sl["abc_scenario"],
            "wave_counts": sl["wave_counts"],
            "patterns": [{k: v for k, v in p.items() if k != "invalidated"} for p in report["algebraic_patterns_detected"]],
            "smc": report["smc_structures"],
            "volume_profile": {k: v for k, v in report["volume_profile"].items() if k != "bins"},
            "tested_zones": sl.get("tested_zones"),
        },
        "indicators": {k: mi[k] for k in ("trend", "momentum", "volatility", "qlib_factors")},
        "scenarios": {
            "horizon_bars": rs["horizon_bars"],
            "atr_pct": rs.get("atr_pct"),
            "sigma_1d_pct": rs.get("sigma_1d_pct"),
            "min_trigger_distance_atr": rs.get("min_trigger_distance_atr"),
            "breakout_level": rs["breakout_level"],
            "breakdown_level": rs["breakdown_level"],
            "contested_levels": list(rs.get("contested_levels") or []),
            "paths": {k: _strip_scenario(rs[k]) for k in ("bull", "neutral", "bear")},
            "active": [_strip_scenario(s) for s in rs.get("scenarios") or []],
            "precondition_met_count": rs.get("precondition_met_count"),
            "impulse_wave": rs.get("impulse_wave"),
        },
        "plan": {
            "long": {k: v for k, v in tb.items() if k not in ("holder_defense_plan", "short_plan")},
            "holder_defense": tb["holder_defense_plan"],
            "short": tb["short_plan"],
        },
        "volatility_range": {
            "method": fp.get("method"),
            "promotion_status": fp["promotion_status"],
            "horizon_bars": fp["horizon_bars"],
            "horizon_dates": fp["horizon_dates"],
            "calendar_coverage": fp.get("calendar_coverage"),
            "q10": fp["q10"],
            "q25": fp["q25"],
            "q50": fp["base_median_q50"],
            "q75": fp["optimistic_q75"],
            "q90": fp["q90"],
            "q50_method": fp.get("q50_method"),
            "q50_note": fp.get("q50_note"),
            "band_50": fp.get("conformal_50pct_band"),
            "band_80": fp["conformal_80pct_band"],
            "daily_price_limit_pct": fp.get("daily_price_limit_pct"),
            "volatility_scaling": fp.get("volatility_scaling"),
            "calibration_audit": fp.get("calibration_audit"),
        },
        "files": dict(files),
        "reproducibility": {**report["reproducibility"], "engine_version": SCHEMA_VERSION},
    }
    # 可引用价位只从 result.json 自己的价位字段与最近 20 根K线收集：前者能在 result.json 里找到出处，
    # 最近 20 根K线的价格只存在于 chart_data.json（result.json 不含K线序列）
    sections = {k: result[k] for k in _QUOTABLE_SECTIONS}
    self_basis = bool(ml_fields["cycle"]) and ml_fields["cycle"].get("basis") == "SELF"
    if self_basis:
        sections["cycle"] = ml_fields["cycle"]  # 标的自身的周期价位属于标的
    wl = collect_price_whitelist(sections, int(meta.get("price_decimals") or 2), report["series_export"]["bars"])
    result["quotable_prices"] = {k: wl[k] for k in ("price_decimals", "tolerance", "prices")}
    bench_wl = None
    if ml is not None and not self_basis and ml.get("benchmark_price_decimals") is not None:
        bw = collect_price_whitelist(
            {"structure": ml_fields["structure"], "cycle": ml_fields["cycle"]}, int(ml["benchmark_price_decimals"]), []
        )
        bench_wl = {k: bw[k] for k in ("price_decimals", "tolerance", "prices")}
    result["quotable_prices"]["benchmark"] = bench_wl

    def target_prices() -> list[float]:  # 裁剪多次测试价区后按裁剪后的内容重算标的可引用价位
        secs = {k: result[k] for k in _QUOTABLE_SECTIONS}
        if self_basis:
            secs["cycle"] = ml_fields["cycle"]
        return collect_price_whitelist(secs, int(meta.get("price_decimals") or 2), report["series_export"]["bars"])["prices"]

    _trim_to_size_budget(result, target_prices)
    return result


def _result_size(result: dict[str, Any]) -> int:
    """与 analyze_and_forecast 落盘同一种序列化下的 UTF-8 字节数。"""
    return len(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"))


def _refresh_benchmark_prices(result: dict[str, Any]) -> None:
    bench = result["quotable_prices"]["benchmark"]
    if bench is not None:
        bw = collect_price_whitelist(
            {"structure": result["market_context"]["structure"], "cycle": result["market_context"]["cycle"]}, bench["price_decimals"], []
        )
        result["quotable_prices"]["benchmark"] = {**bench, "prices": bw["prices"]}


def _trim_bench_wave_counts(result: dict[str, Any]) -> None:
    s = result["market_context"]["structure"]
    result["market_context"]["structure"] = {
        **s, "wave_counts": [w for w in s["wave_counts"] if w.get("role") == "主计数"] or s["wave_counts"][:1]
    }
    _refresh_benchmark_prices(result)


def _trim_bench_key_levels(result: dict[str, Any]) -> None:
    s = result["market_context"]["structure"]
    result["market_context"]["structure"] = {**s, "key_levels": {k: v[:BENCH_KEY_LEVELS_KEEP] for k, v in s["key_levels"].items()}}
    _refresh_benchmark_prices(result)


def _map_minor_zones(result: dict[str, Any], fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]) -> dict[str, Any]:
    tz = result["structure"]["tested_zones"]
    scales = [{**s, "zones": fn(s["zones"])} if s["scale"] == "MINOR" else s for s in tz["scales"]]
    return {**tz, "scales": scales}


def _trim_minor_broken_zones(result: dict[str, Any]) -> None:
    tz = result["structure"]["tested_zones"]
    gone = {z["zone_id"] for s in tz["scales"] if s["scale"] == "MINOR" for z in s["zones"] if z.get("state") == "BROKEN"}
    new_tz = _map_minor_zones(result, lambda zones: [z for z in zones if z["zone_id"] not in gone])
    new_tz["changes"] = [c for c in tz["changes"] if c["object_id"] not in gone]
    result["structure"] = {**result["structure"], "tested_zones": new_tz}


def _trim_minor_zone_summaries(result: dict[str, Any]) -> None:
    new_tz = _map_minor_zones(result, lambda zones: [{k: v for k, v in z.items() if k != "summary_cn"} for z in zones])
    result["structure"] = {**result["structure"], "tested_zones": new_tz}


def _trim_to_size_budget(result: dict[str, Any], target_prices: Callable[[], list[float]]) -> None:
    """超出体积预算时按顺序裁剪，每步之后检查一次：

    1~2. v6 规格 3.4：基准结构摘要先去掉备选浪型计数，再把关键价位每侧截到离收盘最近的几档；
    3~4. 多次测试价区规格 8.3：去掉次级别已破位的价区（连同指向它们的变化），再去掉次级别价区的 summary_cn。

    每步之后重算受影响的可引用价位（target_prices() 按裁剪后的内容重算标的价位），并在 quality.notes 写明裁了什么。
    主级别价区与其余字段不裁。新建对象替换，不回写内部报告。
    """
    budget = RESULT_SIZE_BUDGET_BYTES
    if _result_size(result) <= budget:
        return
    steps: list[tuple[str, Callable[[dict[str, Any]], None], bool]] = []
    if result["market_context"]["structure"] is not None:
        steps += [
            ("去掉基准浪型的备选计数", _trim_bench_wave_counts, False),
            ("基准关键价位每侧只保留离收盘最近的几档", _trim_bench_key_levels, False),
        ]
    if result["structure"].get("tested_zones") is not None:
        steps += [
            ("去掉次级别已破位的多次测试价区", _trim_minor_broken_zones, True),
            ("去掉次级别多次测试价区的文字摘要", _trim_minor_zone_summaries, True),
        ]
    if not steps:
        return
    base_notes = list(result["quality"].get("notes") or [])
    done: list[str] = []
    for label, trim, touches_target in steps:
        trim(result)
        done.append(label)
        if touches_target:
            result["quotable_prices"] = {**result["quotable_prices"], "prices": target_prices()}
        note = f"result.json 超出体积预算，已依次裁剪：{'；'.join(done)}；其余字段未裁剪"
        result["quality"]["notes"] = [*base_notes, note]
        if _result_size(result) <= budget:
            return
    result["quality"]["notes"] = [*base_notes, note + "；裁剪后仍超出预算"]


def build_chart_data(report: dict[str, Any]) -> dict[str, Any]:
    se = report["series_export"]
    rs = report["rule_scenarios"]
    return {
        "schema_version": SCHEMA_VERSION,
        "stability": "unstable",
        "note": "非稳定接口：只供本技能的 PNG/HTML 渲染器读取，字段可能在任何版本变化；对外请读 result.json",
        "bars": se["bars"],
        "pivots": se["pivots"],
        "background": se.get("background"),
        "intraday": se.get("intraday"),
        "data_note": se.get("data_note"),
        "rs_series": list(report["market_benchmark"].get("rs_series") or []),
        "volume_profile_bins": list(report["volume_profile"].get("bins") or []),
        "scenario_paths": {
            "paths": {k: _draw_only(rs[k]) for k in ("bull", "neutral", "bear")},
            "active": [{"id": s["id"], **_draw_only(s)} for s in rs.get("scenarios") or []],
        },
        "replay_snapshots": list(report.get("replay_snapshots") or []),
    }
