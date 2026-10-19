"""L3 受约束的 LLM 研判层辅助模块：生成证据包 (*_judgment_pack.json) 与默认受约束研判。

设计原则（见 OPTIMIZATION_PLAN.md 第 3.3 节与 O3-30）：
1. 每条证据 E1…En 必须带精确的 json_path，可直接在 result.json 中定位并复核。
2. 显式列出相互冲突的信号 C1…Cm，防止只挑顺眼证据。
3. 每个情景同时给出 L2 历史条件频率与归一化互斥基准概率（和为 100%），供研判做有界调整（n>=30 最多 ±15%，n<30 最多 ±10%）。
4. 汇总 result.json 中的全部合法价格白名单，严禁捏造 JSON 之外的价格数字。
"""
from __future__ import annotations

import re
from typing import Any

_PATH_TOKEN_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")

# validate_judgment --auto-init 生成的是按证据条数机械调整的预填模板，不是 Agent 的主观研判；
# 日志与报告据此区分来源，score_judgments 只对 Agent 研判计算「LLM 调整」技能分
AUTO_INIT_GENERATOR = "AUTO_INIT_TEMPLATE"
AGENT_ORIGIN = "AGENT"


def resolve_json_path(data: Any, path: str) -> tuple[bool, Any]:
    """按点号与下标路径（如 'algebraic_patterns_detected[0].formula_verified'）在 JSON 对象中取值。"""
    if not path or not isinstance(path, str):
        return False, None
    cur: Any = data
    for match in _PATH_TOKEN_RE.finditer(path):
        key, idx_str = match.group(1), match.group(2)
        if key is not None:
            if not isinstance(cur, dict) or key not in cur:
                return False, None
            cur = cur[key]
        elif idx_str is not None:
            idx = int(idx_str)
            if not isinstance(cur, (list, tuple)) or idx < 0 or idx >= len(cur):
                return False, None
            cur = cur[idx]
    return True, cur


def _fmt_price(val: float | int | None, p_dec: int) -> str:
    if val is None:
        return "—"
    return f"{float(val):.{p_dec}f}"


# 键名含这些片段的字段不是价格（频率、百分比、样本数、根数、比值、乘数、相似度等），不进入价格白名单
_NON_PRICE_KEY_TOKENS = (
    "freq",
    "pct",
    "ratio",
    "sample",
    "count",
    "bars",
    "hits",
    "_ci",
    "score",
    "size",
    "coverage",
    "multiplier",
    "sigma",
    "weight",
    "similarity",
    "idx",
    "distance",
    "atr",
    "return",
    "slope",
    "days",
    "horizon",
    "_r",
)


def collect_price_whitelist(report: dict[str, Any]) -> dict[str, Any]:
    """从 result.json 的价格相关字段提取所有合法价格，构建白名单。"""
    meta = report.get("meta") or {}
    p_dec = int(meta.get("price_decimals") or 2)
    prices: set[float] = set()

    def _add(v: Any) -> None:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            fv = float(v)
            if fv > 0:
                prices.add(round(fv, p_dec))
                prices.add(round(fv, 4))

    def _walk_prices(obj: Any, parent_key: str = "") -> None:
        if isinstance(obj, dict):
            for k, val in obj.items():
                kl = k.lower()
                if any(tok in kl for tok in _NON_PRICE_KEY_TOKENS):
                    continue  # 频率、百分比、样本数、根数、比值等不是价格，也不深入其子字段（如 bars_to_target.q25）
                if any(
                    tok in kl
                    for tok in (
                        "price",
                        "open",
                        "high",
                        "low",
                        "close",
                        "ma20",
                        "ma60",
                        "ma120",
                        "ma250",
                        "sma",
                        "ema",
                        "vwap",
                        "poc",
                        "vah",
                        "val",
                        "upper",
                        "lower",
                        "mid",
                        "barrier",
                        "take_profit",
                        "stop_loss",
                        "entry_zone",
                        "support",
                        "resistance",
                        "level",
                        "target",
                        "q10",
                        "q25",
                        "q50",
                        "q75",
                        "q90",
                        "band",
                        "cap",
                        "retest",
                        "rebound",
                        "range",
                        "zone",
                    )
                ):
                    if isinstance(val, (int, float)):
                        _add(val)
                    elif isinstance(val, list):
                        for item in val:
                            if isinstance(item, (int, float)):
                                _add(item)
                            elif isinstance(item, list):
                                for sub in item:
                                    _add(sub)
                            elif isinstance(item, dict):
                                _walk_prices(item, k)
                    elif isinstance(val, dict):
                        _walk_prices(val, k)
                else:
                    _walk_prices(val, k)
        elif isinstance(obj, list):
            for item in obj:
                _walk_prices(item, parent_key)

    for section_key in (
        "mathematical_indicators",
        "smc_structures",
        "volume_profile",
        "triple_barrier_plan",
        "rule_scenarios",
        "future_projection_15d",
    ):
        if section_key in report:
            _walk_prices(report[section_key], section_key)

    bars = (report.get("series_export") or {}).get("bars") or []
    for b in bars[-20:]:
        for k in ("open", "high", "low", "close", "ma20", "ma60", "ma120", "ma250", "kernel_smooth"):
            _add(b.get(k))

    sorted_prices = sorted({round(p, p_dec) for p in prices if p > 0})
    formatted = sorted({f"{p:.{p_dec}f}" for p in sorted_prices} | {f"{p:g}" for p in sorted_prices})
    tolerance = max(10.0 ** (-p_dec), 1e-3)
    return {
        "price_decimals": p_dec,
        "tolerance": round(tolerance, 6),
        "count": len(sorted_prices),
        "prices": sorted_prices,
        "formatted_strings": formatted,
    }


def build_judgment_pack(report: dict[str, Any]) -> dict[str, Any]:
    """从 AnalysisReport 构建结构化研判证据包 (*_judgment_pack.json)。"""
    meta = report["meta"]
    p_dec = int(meta.get("price_decimals") or 2)
    quality = report["quality"]
    bench = report["market_benchmark"]
    regime = report["market_regime"]
    ind = report["mathematical_indicators"]
    trend = ind["trend"]
    mom = ind["momentum"]
    vol = ind["volatility"]
    levels = ind["structure_levels"]
    wedge = levels["wedge_structure"]
    vp = report["volume_profile"]
    mtf = report["multi_timeframe_score"]
    decision = report["meta_labeling_decision"]
    tb = report["triple_barrier_plan"]
    debate = report["debate_and_veto"]
    sc = report["rule_scenarios"]

    bars = (report.get("series_export") or {}).get("bars") or []
    last_close = float(bars[-1]["close"]) if bars else float(vp["poc_price"])

    evidence_items: list[dict[str, Any]] = []

    def _add_ev(category: str, json_path: str, direction: str, summary_cn: str) -> str:
        ok, val = resolve_json_path(report, json_path)
        if not ok:
            raise ValueError(f"Evidence path not found in report: {json_path}")
        eid = f"E{len(evidence_items) + 1}"
        evidence_items.append(
            {
                "id": eid,
                "category": category,
                "json_path": json_path,
                "value": val,
                "direction": direction,
                "summary_cn": summary_cn,
            }
        )
        return eid

    # E1: 大盘基准状态分
    b_score = float(bench["regime_score"])
    b_dir = "BULLISH" if b_score >= 55.0 else ("BEARISH" if b_score < 45.0 else "NEUTRAL")
    _add_ev(
        "BENCHMARK",
        "market_benchmark.regime_score",
        b_dir,
        f"大盘基准 {bench['index_name']}（{bench['index_symbol']}）状态为「{bench['regime_label_cn']}」，状态分 {b_score:.1f}/100",
    )

    # E2: 近20日相对强弱
    rs = float(bench["relative_strength_vs_index"])
    rs_dir = "BULLISH" if rs > 1.01 else ("BEARISH" if rs < 0.99 else "NEUTRAL")
    _add_ev(
        "BENCHMARK",
        "market_benchmark.relative_strength_vs_index",
        rs_dir,
        f"标的近 20 日相对基准强弱比值为 {rs:.4f}（{'跑赢基准' if rs > 1 else ('跑输基准' if rs < 1 else '与基准持平')}）",
    )

    # E3: 标的自身市场状态
    t_score = float(regime["regime_score"])
    t_dir = "BULLISH" if t_score >= 55.0 else ("BEARISH" if t_score < 45.0 else "NEUTRAL")
    _add_ev(
        "REGIME",
        "market_regime.regime_score",
        t_dir,
        f"标的自身市场状态为「{regime['label_cn']}」（{regime['regime']}），状态分 {t_score:.1f}/100",
    )

    # E4: 近120日轨道结构
    w_pat = str(wedge.get("pattern") or "")
    w_st = str(wedge.get("status") or "inside")
    w_dir = (
        "BULLISH"
        if w_st == "above" or "上升通道" in w_pat or "下降楔形" in w_pat
        else ("BEARISH" if w_st == "below" or "下降通道" in w_pat or "上升楔形" in w_pat else "NEUTRAL")
    )
    _add_ev(
        "STRUCTURE",
        "mathematical_indicators.structure_levels.wedge_structure.pattern",
        w_dir,
        f"近 120 日结构分类为「{w_pat}」，当前状态：{wedge['status_text']}；"
        f"今日上轨 {_fmt_price(wedge.get('upper_today'), p_dec)}，今日下轨 {_fmt_price(wedge.get('lower_today'), p_dec)}",
    )

    # E5: 多周期规则综合分
    r_score = float(mtf["rule_score"])
    r_dir = "BULLISH" if r_score >= 60.0 else ("BEARISH" if r_score <= 40.0 else "NEUTRAL")
    _add_ev(
        "SCORE",
        "multi_timeframe_score.rule_score",
        r_dir,
        f"多周期规则综合分为 {r_score:.1f}/100（{mtf.get('rating_cn')}），二阶段动作分类为「{decision.get('action_cn') or decision.get('action')}」"
        + (f"；{(mtf.get('regime_adjustment') or {}).get('note_cn')}" if (mtf.get("regime_adjustment") or {}).get("applied") else ""),
    )

    # E6: Supertrend 与均线趋势
    st_dir_val = int(trend.get("supertrend_dir") or 0)
    st_dir_raw = "BULLISH" if st_dir_val > 0 else ("BEARISH" if st_dir_val < 0 else "NEUTRAL")
    st_dir = st_dir_raw
    st_line = trend.get("supertrend_line")
    _add_ev(
        "TREND",
        "mathematical_indicators.trend.supertrend_dir",
        st_dir,
        f"Supertrend(14,3) 方向为 {st_dir_raw}（轨道位 {_fmt_price(st_line, p_dec)}），"
        f"SMA20={_fmt_price(trend.get('sma20'), p_dec)}，SMA60={_fmt_price(trend.get('sma60'), p_dec)}",
    )

    # E7: 动量 RSI14 与 MACD 柱
    rsi = float(mom.get("rsi14") or 50.0)
    macd_hist = float(mom.get("macd_hist") or 0.0)
    mom_dir = "BULLISH" if (macd_hist > 0 and 45.0 <= rsi <= 70.0) or rsi < 30.0 else ("BEARISH" if macd_hist < 0 or rsi > 75.0 else "NEUTRAL")
    _add_ev(
        "MOMENTUM",
        "mathematical_indicators.momentum.rsi14",
        mom_dir,
        f"RSI(14) = {rsi:.2f}，MACD(12,26,9) 柱 = {macd_hist:.3f}",
    )

    # E8: 量比 RVOL20 与 ATR14
    rvol = float(mom.get("rvol20") or 1.0)
    atr_val = float(vol.get("atr14") or 0.0)
    atr_pct = float(vol.get("atr20_pct") or 0.0)
    vol_dir = "BULLISH" if rvol >= 1.2 and macd_hist > 0 else ("BEARISH" if rvol >= 1.2 and macd_hist < 0 else "NEUTRAL")
    _add_ev(
        "VOLATILITY",
        "mathematical_indicators.momentum.rvol20",
        vol_dir,
        f"20 日相对量比 RVOL20 = {rvol:.2f}，ATR(14) = {_fmt_price(atr_val, p_dec)}（20日ATR占现价 {atr_pct:.2f}%）",
    )

    # E9: 成交量分布 POC 与价值区
    poc = float(vp["poc_price"])
    vah = float(vp["vah_price"])
    val_p = float(vp["val_price"])
    vp_dir = "BULLISH" if last_close >= poc else "BEARISH"
    _add_ev(
        "VOLUME_PROFILE",
        "volume_profile.poc_price",
        vp_dir,
        f"近 120 日成交量密集区 POC = {_fmt_price(poc, p_dec)}，价值区 VAL~VAH = {_fmt_price(val_p, p_dec)} ~ {_fmt_price(vah, p_dec)}，"
        f"现价 {_fmt_price(last_close, p_dec)} 位于 POC {'上方' if last_close >= poc else '下方'}",
    )

    # E10: 三重屏障盈亏比与风控审查
    rr = float(tb["risk_reward_ratio"])
    veto = bool(debate.get("risk_officer_veto"))
    veto_reasons = list(debate.get("veto_reasons") or [])
    risk_dir = "BEARISH" if veto else ("BULLISH" if rr >= 1.5 else "NEUTRAL")
    _add_ev(
        "RISK",
        "triple_barrier_plan.risk_reward_ratio",
        risk_dir,
        f"三重屏障扣费后净盈亏比 = {rr:.2f}（入场区 {_fmt_price(tb['entry_zone'][0], p_dec)} ~ {_fmt_price(tb['entry_zone'][1], p_dec)}，"
        f"止损 {_fmt_price(tb['stop_loss_barrier'], p_dec)}，TP1 {_fmt_price(tb['take_profit_1'], p_dec)}），"
        f"风控否决 = {'是（' + '；'.join(veto_reasons) + '）' if veto else '否'}",
    )

    # E11+: 主浪型计数（如有）
    wave_counts = levels.get("wave_counts") or []
    if wave_counts:
        wc0 = wave_counts[0]
        wc_dir = "BULLISH" if "上涨" in wc0.get("label", "") or "上升" in wc0.get("label", "") else ("BEARISH" if "下跌" in wc0.get("label", "") or "下降" in wc0.get("label", "") else "NEUTRAL")
        _add_ev(
            "WAVE",
            "mathematical_indicators.structure_levels.wave_counts[0].label",
            wc_dir,
            f"{wc0['role']}为「{wc0['label']}」（进度：{wc0['c_progress']['phase']}，否定条件：{wc0['count_invalidation']['text']}）",
        )

    # 代数形态前 2 条
    alg_pats = report.get("algebraic_patterns_detected") or []
    for idx, pat in enumerate(alg_pats[:2]):
        p_dir = str(pat.get("direction") or "NEUTRAL")
        if p_dir not in ("BULLISH", "BEARISH", "NEUTRAL"):
            p_dir = "NEUTRAL"
        _add_ev(
            "PATTERN",
            f"algebraic_patterns_detected[{idx}].formula_verified",
            p_dir,
            f"形态「{pat['name_cn']}」（{p_dir}，确认日 {pat.get('confirmed_at') or pat.get('observed_at')}）：{pat['formula_verified']}",
        )

    # 最近一次 SMC 结构破位
    breaks = (report.get("smc_structures") or {}).get("structure_breaks") or []
    if breaks:
        last_idx = len(breaks) - 1
        lb = breaks[last_idx]
        lb_dir = "BULLISH" if "BULL" in str(lb.get("type", "")) else ("BEARISH" if "BEAR" in str(lb.get("type", "")) else "NEUTRAL")
        _add_ev(
            "SMC",
            f"smc_structures.structure_breaks[{last_idx}].type",
            lb_dir,
            f"最近一次结构破位为 {lb['type']}（{lb['day']}，破位价 {_fmt_price(lb['broken_level'], p_dec)}）",
        )

    # 识别相互冲突的信号 C1…Cm
    bull_ids = [e["id"] for e in evidence_items if e["direction"] == "BULLISH"]
    bear_ids = [e["id"] for e in evidence_items if e["direction"] == "BEARISH"]
    neut_ids = [e["id"] for e in evidence_items if e["direction"] == "NEUTRAL"]
    conflicting_signals: list[dict[str, Any]] = []

    if (b_dir == "BEARISH" and rs > 1.0) or (b_dir == "BULLISH" and rs < 1.0):
        conflicting_signals.append(
            {
                "id": f"C{len(conflicting_signals) + 1}",
                "bullish_evidence_ids": ["E2"] if rs > 1.0 else ["E1"],
                "bearish_evidence_ids": ["E1"] if rs > 1.0 else ["E2"],
                "summary_cn": f"大盘状态（E1，{b_score:.1f}分）与个股相对强弱（E2，RS={rs:.4f}）方向不一致",
            }
        )
    if st_dir != mom_dir and st_dir in ("BULLISH", "BEARISH") and mom_dir in ("BULLISH", "BEARISH"):
        conflicting_signals.append(
            {
                "id": f"C{len(conflicting_signals) + 1}",
                "bullish_evidence_ids": ["E6"] if st_dir == "BULLISH" else ["E7"],
                "bearish_evidence_ids": ["E7"] if st_dir == "BULLISH" else ["E6"],
                "summary_cn": f"趋势方向（E6，Supertrend={st_dir_raw}）与动量指标（E7，RSI={rsi:.1f}/MACD柱={macd_hist:.3f}）存在分歧",
            }
        )
    if bull_ids and bear_ids:
        conflicting_signals.append(
            {
                "id": f"C{len(conflicting_signals) + 1}",
                "bullish_evidence_ids": bull_ids,
                "bearish_evidence_ids": bear_ids,
                "summary_cn": f"当前截面同时存在偏多证据（{', '.join(bull_ids)}）与偏空/风控约束证据（{', '.join(bear_ids)}），不可单边外推",
            }
        )
    elif not conflicting_signals:
        conflicting_signals.append(
            {
                "id": "C1",
                "bullish_evidence_ids": bull_ids or neut_ids[:1],
                "bearish_evidence_ids": bear_ids or neut_ids[-1:],
                "summary_cn": "多数指标方向趋同，但仍受三重屏障止损与历史条件频率波动区间约束",
            }
        )

    # 组装情景锚点（L2 历史条件频率 + 归一化互斥基准概率）
    raw_scenarios = sc.get("scenarios") or []
    eff_weights: list[float] = []
    for s in raw_scenarios:
        hf = s.get("historical_frequency") or {}
        eff = hf.get("target_freq") if hf.get("target_freq") is not None else hf.get("shrunk_target_freq")
        w = float(eff) if eff is not None and float(eff) > 0 else 0.05
        eff_weights.append(w)
    weight_sum = sum(eff_weights) or float(max(1, len(raw_scenarios)))
    raw_pcts = [round(w / weight_sum * 100.0, 2) for w in eff_weights]
    if raw_pcts:
        diff = round(100.0 - sum(raw_pcts), 2)
        raw_pcts[0] = round(raw_pcts[0] + diff, 2)

    scenario_anchors: list[dict[str, Any]] = []
    for idx, s in enumerate(raw_scenarios):
        hf = s.get("historical_frequency") or {}
        n = int(hf.get("sample_size") if hf.get("sample_size") is not None else (hf.get("sample_size_n") or 0))
        low_sample = bool(hf.get("low_sample", n < 30))
        max_adj = 10.0 if n < 30 else 15.0
        base_pct = raw_pcts[idx]
        eff_freq = hf.get("target_freq") if hf.get("target_freq") is not None else hf.get("shrunk_target_freq")
        raw_freq = hf.get("raw_target_freq") if hf.get("raw_target_freq") is not None else hf.get("hit_target_freq")
        jpath = f"rule_scenarios.scenarios[{idx}].historical_frequency"
        ok, _ = resolve_json_path(report, jpath)
        if not ok:
            jpath = f"rule_scenarios.scenarios[{idx}]"
        scenario_anchors.append(
            {
                "scenario_id": s["id"],
                "scenario_name": s["name"],
                "direction": s["direction"],
                "status": s.get("status", "PENDING_TRIGGER"),
                "status_cn": s.get("status_cn", ""),
                "trigger": s["trigger"],
                "trigger_price": s.get("trigger_price"),
                "target_price": s["target_price"],
                "target_source": s["target_source"],
                "invalidation": s["invalidation"],
                "invalidation_price": s.get("invalidation_price"),
                "sample_size_n": n,
                "low_sample": low_sample,
                "l2_hit_target_freq": raw_freq,
                "l2_effective_hit_target_freq": round(float(eff_freq), 4) if eff_freq is not None else None,
                "l2_effective_pct": round(float(eff_freq) * 100.0, 2) if eff_freq is not None else None,
                "l2_wilson_95ci": hf.get("target_ci_95") or hf.get("hit_target_95ci") or [0.0, 1.0],
                "l2_display_cn": hf.get("display_cn") or "—",
                "baseline_probability_pct": base_pct,
                "max_adjustment_pct": max_adj,
                "allowed_probability_range_pct": [
                    round(max(0.0, base_pct - max_adj), 2),
                    round(min(100.0, base_pct + max_adj), 2),
                ],
                "json_path": jpath,
            }
        )

    whitelist = collect_price_whitelist(report)
    return {
        "schema_version": report.get("schema_version", "5.3.0"),
        "symbol": meta["symbol"],
        "name": meta["name"],
        "market": meta["market"],
        "asset_type": meta["asset_type"],
        "currency": meta["currency"],
        "as_of": meta["as_of"],
        "price_decimals": p_dec,
        "horizon_bars": int((report.get("future_projection_15d") or {}).get("horizon_bars") or 15),
        "quality_status": quality["status"],
        "quality_warnings": list(quality.get("warnings") or []),
        "quality_notes": list(quality.get("notes") or []),
        "risk_officer_veto": veto,
        "veto_reasons": veto_reasons,
        "evidence_items": evidence_items,
        "conflicting_signals": conflicting_signals,
        "scenario_anchors": scenario_anchors,
        "constraints": {
            "label_required": "主观研判",
            "sum_probabilities_pct": 100.0,
            "max_adjustment_pct_standard": 15.0,
            "max_adjustment_pct_low_sample": 10.0,
            "low_sample_threshold_n": 30,
            "forbid_invented_prices": True,
            "require_evidence_ids": True,
        },
        "price_whitelist": whitelist,
    }


def build_falsification_text(
    primary: dict[str, Any],
    alternate: dict[str, Any],
    stop_loss: float,
    p_dec: int,
    breakout: float | None = None,
    breakdown: float | None = None,
) -> str:
    """按主情景方向写证伪条件：做多情景以失效位或三重屏障止损被收盘跌破为证伪；
    做空/假突破情景以收盘反向收回失效位为证伪（跌破做多止损反而是确认）；震荡与双向情景以越出区间或始终未变盘为证伪。"""
    name = primary["scenario_name"]
    direction = str(primary.get("direction") or "")
    inv_text = str(primary.get("invalidation") or "").strip()
    inv_price = primary.get("invalidation_price")
    if direction == "BULLISH":
        inv_part = (
            f"日线收盘跌破主情景「{name}」失效位 {_fmt_price(inv_price, p_dec)}"
            if inv_price is not None
            else f"出现主情景「{name}」的失效条件（{inv_text}）"
        )
        cond = f"{inv_part}，或盘中跌破三重屏障止损位 {_fmt_price(stop_loss, p_dec)}（见 E10）"
    elif inv_text and inv_text != "—":
        cond = f"出现主情景「{name}」的失效条件：{inv_text}"
    elif breakout is not None and breakdown is not None:
        cond = (
            f"预测期内日线收盘既未站上 {_fmt_price(breakout, p_dec)} 也未跌破 {_fmt_price(breakdown, p_dec)}"
            f"（主情景「{name}」所需的变盘没有发生）"
        )
    else:
        cond = f"主情景「{name}」的目标位 {_fmt_price(primary.get('target_price'), p_dec)} 在预测期内未被触及"
    trigger = str(primary.get("trigger") or "").strip()
    if primary.get("status") == "PENDING_TRIGGER" and trigger:
        # 待触发情景的失效条件只在触发之后才有意义（例如破位下行的「收回破位位上方」在触发前可能已经成立）
        cond = f"预测期内始终未出现主情景「{name}」的触发条件（{trigger}），或触发后{cond}"
    return (
        f"若{cond}，则主情景判断证伪，应切换至备选情景「{alternate['scenario_name']}」"
        f"（目标位 {_fmt_price(alternate.get('target_price'), p_dec)}）。"
    )


def generate_constrained_judgment(report: dict[str, Any]) -> dict[str, Any]:
    """按 references/llm-judgment-protocol.md 的硬约束生成预填模板（validate_judgment --auto-init 使用）。

    模板只按偏多/偏空证据条数做 ±4 个百分点以内的机械零和调整，不是 Agent 的主观研判：
    输出带 generator = AUTO_INIT_TEMPLATE，标题、综述与理由都写明「脚本预填模板」；
    Agent 必须复核证据并改写概率后，才可称为主观研判。
    """
    pack = report.get("judgment_pack") or build_judgment_pack(report)
    p_dec = int(pack.get("price_decimals") or 2)
    evidence_items = pack["evidence_items"]
    anchors = pack["scenario_anchors"]

    bull_evs = [e["id"] for e in evidence_items if e["direction"] == "BULLISH"]
    bear_evs = [e["id"] for e in evidence_items if e["direction"] == "BEARISH"]
    neut_evs = [e["id"] for e in evidence_items if e["direction"] == "NEUTRAL"]
    all_eids = [e["id"] for e in evidence_items]

    # 计算多空证据净倾向（控制在 ±4.0 个百分点以内，远低于 ±10%/±15% 上限，且零和调整保证总和 = 100%）
    net_score = len(bull_evs) - len(bear_evs)
    if pack.get("risk_officer_veto"):
        net_score = min(net_score, -1)
    raw_shift = max(-4.0, min(4.0, float(net_score) * 1.2))

    deltas = [0.0 for _ in anchors]
    bull_idxs = [i for i, a in enumerate(anchors) if a["direction"] == "BULLISH"]
    bear_idxs = [i for i, a in enumerate(anchors) if a["direction"] == "BEARISH"]
    neut_idxs = [i for i, a in enumerate(anchors) if a["direction"] not in ("BULLISH", "BEARISH")]

    if bull_idxs and (bear_idxs or neut_idxs) and abs(raw_shift) >= 0.5:
        counter_idxs = bear_idxs if bear_idxs else neut_idxs
        max_up = min(anchors[i]["max_adjustment_pct"] * 0.5 for i in bull_idxs)
        max_dn = min(anchors[i]["max_adjustment_pct"] * 0.5 for i in counter_idxs)
        max_shift_allowed = min(max_up, max_dn, 4.0)
        for i in bull_idxs:
            max_shift_allowed = min(max_shift_allowed, anchors[i]["baseline_probability_pct"] * 0.5)
        for i in counter_idxs:
            max_shift_allowed = min(max_shift_allowed, anchors[i]["baseline_probability_pct"] * 0.5)
        total_shift = round(max(-max_shift_allowed, min(max_shift_allowed, raw_shift)), 2)
        per_bull = round(total_shift / len(bull_idxs), 2)
        applied = 0.0
        for i in bull_idxs:
            deltas[i] = per_bull
            applied = round(applied + per_bull, 2)
        per_counter = round(-applied / len(counter_idxs), 2)
        counter_sum = 0.0
        for idx_pos, i in enumerate(counter_idxs):
            if idx_pos == len(counter_idxs) - 1:
                deltas[i] = round(-applied - counter_sum, 2)
            else:
                deltas[i] = per_counter
                counter_sum = round(counter_sum + per_counter, 2)

    scenario_judgments: list[dict[str, Any]] = []
    for i, a in enumerate(anchors):
        base_p = float(a["baseline_probability_pct"])
        delta = float(deltas[i])
        subj_p = round(base_p + delta, 2)
        adj_p = round(subj_p - base_p, 2)
        if a["direction"] == "BULLISH":
            sup = (bull_evs or neut_evs or all_eids)[:3]
            opp = (bear_evs or neut_evs or all_eids)[:3]
        elif a["direction"] == "BEARISH":
            sup = (bear_evs or neut_evs or all_eids)[:3]
            opp = (bull_evs or neut_evs or all_eids)[:3]
        else:
            sup = (neut_evs or bull_evs or all_eids)[:2]
            opp = (bear_evs or bull_evs or all_eids)[:2]
        cited = ", ".join(dict.fromkeys(sup + opp))
        l2_pct_txt = f"{a['l2_effective_pct']:.1f}%" if a.get("l2_effective_pct") is not None else "—"
        rationale = (
            f"脚本预填（按偏多/偏空证据条数机械调整，尚未经 Agent 研判）：L2 历史条件频率（含低样本收缩）为 {l2_pct_txt}（样本 n={a['sample_size_n']}），"
            f"归一化基准概率为 {base_p:.2f}%；支持证据 {', '.join(sup)}，反向约束 {', '.join(opp)}（引用 {cited}），"
            f"预填调整 {adj_p:+.2f} 个百分点至 {subj_p:.2f}%（未超 ±{a['max_adjustment_pct']:.0f}% 上限）。"
        )
        scenario_judgments.append(
            {
                "scenario_id": a["scenario_id"],
                "scenario_name": a["scenario_name"],
                "direction": a["direction"],
                "sample_size_n": a["sample_size_n"],
                "low_sample": a["low_sample"],
                "l2_historical_frequency_pct": a.get("l2_effective_pct"),
                "l2_display_cn": a["l2_display_cn"],
                "baseline_probability_pct": base_p,
                "subjective_probability_pct": subj_p,
                "adjustment_pct": adj_p,
                "max_adjustment_pct": a["max_adjustment_pct"],
                "supporting_evidence_ids": sup,
                "opposing_evidence_ids": opp,
                "rationale_cn": rationale,
                "target_price": a["target_price"],
                "invalidation_price": a.get("invalidation_price"),
            }
        )

    ranked = sorted(scenario_judgments, key=lambda x: x["subjective_probability_pct"], reverse=True)
    primary = ranked[0]
    alternate = ranked[1] if len(ranked) > 1 else ranked[0]

    tb = report["triple_barrier_plan"]
    vp = report["volume_profile"]
    key_levels = [
        {"label": "三重屏障止损位", "price": float(tb["stop_loss_barrier"]), "role": "STOP_LOSS"},
        {"label": "三重屏障第一目标位 TP1", "price": float(tb["take_profit_1"]), "role": "TARGET_1"},
        {"label": "成交量密集区 POC", "price": float(vp["poc_price"]), "role": "PIVOT_POC"},
    ]
    if primary.get("target_price") is not None:
        key_levels.append({"label": f"主情景（{primary['scenario_name']}）目标位", "price": float(primary["target_price"]), "role": "PRIMARY_TARGET"})

    anchor_by_id = {a["scenario_id"]: a for a in anchors}
    rs = report.get("rule_scenarios") or {}
    falsification_text = build_falsification_text(
        {
            **primary,
            **{k: anchor_by_id.get(primary["scenario_id"], {}).get(k) for k in ("invalidation", "status", "trigger")},
        },
        alternate,
        float(tb["stop_loss_barrier"]),
        p_dec,
        breakout=(rs.get("breakout_level") or {}).get("price"),
        breakdown=(rs.get("breakdown_level") or {}).get("price"),
    )

    veto_prefix = "【风控已否决做多，以下仅作结构观察】" if pack.get("risk_officer_veto") else ""
    summary_cn = (
        f"{veto_prefix}【脚本预填模板，尚未经 Agent 主观研判】主观研判模板（基于 L2 锚点与证据 {', '.join(all_eids[:6])} 的条数机械调整）："
        f"主情景为「{primary['scenario_name']}」（基准 {primary['baseline_probability_pct']:.2f}% → 主观研判 {primary['subjective_probability_pct']:.2f}%，"
        f"目标位 {_fmt_price(primary['target_price'], p_dec)}），"
        f"备选情景为「{alternate['scenario_name']}」（基准 {alternate['baseline_probability_pct']:.2f}% → 主观研判 {alternate['subjective_probability_pct']:.2f}%，"
        f"目标位 {_fmt_price(alternate['target_price'], p_dec)}）。"
    )

    return {
        "schema_version": pack.get("schema_version", "5.3.0"),
        "symbol": pack["symbol"],
        "name": pack["name"],
        "market": pack["market"],
        "as_of": pack["as_of"],
        "horizon_bars": pack["horizon_bars"],
        "judgment_type": "主观研判模板（脚本预填，未经 Agent 调整）",
        "generator": AUTO_INIT_GENERATOR,
        "summary_cn": summary_cn,
        "primary_scenario_id": primary["scenario_id"],
        "alternate_scenario_id": alternate["scenario_id"],
        "scenario_judgments": scenario_judgments,
        "conflicting_signals_reviewed": [c["id"] for c in pack.get("conflicting_signals") or []],
        "key_levels_to_watch": key_levels,
        "falsification_condition": falsification_text,
        "disclaimer_cn": "本节为脚本按证据条数预填的研判模板（非 Agent 主观研判），记入日志时单独标注来源、不计入 LLM 技能分，不构成投资建议。",
    }


def judgment_origin(judgment: dict[str, Any], report: dict[str, Any]) -> str:
    """判定研判来源：各情景主观概率与 --auto-init 模板完全一致即视为模板（无论是否改过文字），否则为 Agent 研判。"""
    if str(judgment.get("judgment_type") or "") == "主观研判（脚本预填模板）":
        return AUTO_INIT_GENERATOR
    try:
        template = generate_constrained_judgment(report)
    except (KeyError, ValueError, IndexError, TypeError):
        return AGENT_ORIGIN
    tmpl = {sj["scenario_id"]: float(sj["subjective_probability_pct"]) for sj in template["scenario_judgments"]}
    got: dict[str, float] = {}
    for sj in judgment.get("scenario_judgments") or []:
        try:
            got[str(sj["scenario_id"])] = float(sj["subjective_probability_pct"])
        except (KeyError, TypeError, ValueError):
            return AGENT_ORIGIN
    if set(got) == set(tmpl) and all(abs(got[k] - tmpl[k]) <= 0.005 for k in tmpl):
        return AUTO_INIT_GENERATOR
    return AGENT_ORIGIN
