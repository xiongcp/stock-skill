"""把 AnalysisReport（及可选回测结果）确定性地渲染为中文 Markdown 分析报告 `*_report.md`。

报告按 SKILL.md「回复契约」的顺序组织，agent 可直接据此回答用户：
- 所有数字原样取自 report / backtest，不做任何再计算；价格按品种最小价位位数格式化。
- 数据质量为 UNAVAILABLE 时只输出告警，不输出任何价位与结论。
- 规则分、区间、回测统计都附带口径说明，避免被误读为胜率或方向预测。
"""
from __future__ import annotations

from typing import Any

from market_rules import price_decimals

MARKET_CN = {"CN": "A股", "HK": "港股", "US": "美股"}
ASSET_CN = {"index": "指数", "equity": "股票", "etf": "ETF"}
ADJUSTMENT_CN = {"raw": "不复权", "split": "仅拆股复权", "total_return": "前复权（含分红送转）"}
DIRECTION_CN = {"BULLISH": "看多", "BEARISH": "看空", "NEUTRAL": "中性"}
SETTLEMENT_CN = {
    "T+1": "T+1 交易（当日买入、次日才能卖出）",
    "T+0_TRADING_T+2_CLEARING": "可当日回转交易、T+2 交收",
    "T+1_SEC_SETTLEMENT": "可当日回转交易、T+1 交收",
    "INDEX_NON_TRADABLE": "指数本身不可交易",
}
DISCLAIMER = "以上为规则化技术分析，仅供研究参考，不构成投资建议。"


def _cell(text: Any) -> str:
    """表格单元格转义：竖线会破坏 Markdown 表格。"""
    return str(text).replace("|", "\\|").replace("\n", " ")


def _pct(value: Any, digits: int = 1) -> str:
    return "—" if value is None else f"{float(value) * 100:.{digits}f}%"


def _num(value: Any, digits: int = 2) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def render_report_markdown(
    report: dict[str, Any],
    backtest: dict[str, Any] | None = None,
    judgment: dict[str, Any] | None = None,
    judgment_score_summary: dict[str, Any] | None = None,
) -> str:
    meta = report["meta"]
    quality = report["quality"]
    market = meta["market"]
    asset_type = meta["asset_type"]
    bars = report.get("series_export", {}).get("bars") or []
    last_close = float(bars[-1]["close"]) if bars else 0.0
    decimals = price_decimals(last_close, market, asset_type, meta["as_of"])

    def price(value: Any) -> str:
        return "—" if value is None else f"{float(value):.{decimals}f}"

    lines: list[str] = [
        f"# {meta['name']}（{meta['symbol']}）K 线结构分析报告",
        "",
        (
            f"> 数据截至 **{meta['as_of']}**（{MARKET_CN.get(market, market)} · {ASSET_CN.get(asset_type, asset_type)}）"
            f"｜数据源 `{meta['data_source']}`｜复权 {ADJUSTMENT_CN.get(meta.get('adjustment'), meta.get('adjustment'))}"
            f"｜数据质量 **{quality['status']}**"
        ),
        "",
    ]

    if quality.get("warnings"):
        lines += ["**数据告警**", ""] + [f"- {w}" for w in quality["warnings"]] + [""]
    if quality["status"] == "UNAVAILABLE":
        lines += [
            "> **数据不可用：本报告不提供任何价位、区间与结论。** 请换用真实行情数据源后重新运行。",
            "",
            "---",
            f"*{DISCLAIMER}*",
            "",
        ]
        return "\n".join(lines)
    if quality.get("notes"):
        lines += ["**数据备注**", ""] + [f"- {n}" for n in quality["notes"]] + [""]

    debate = report["debate_and_veto"]
    decision = report["meta_labeling_decision"]
    mtf = report["multi_timeframe_score"]
    if debate.get("risk_officer_veto"):
        lines += ["> **风控否决**：以下条件触发否决，本报告不构成任何做多建议。", ">"]
        lines += [f"> - {r}" for r in debate.get("veto_reasons", [])]
        lines += [""]

    # 1. 结论速览
    bench = report["market_benchmark"]
    plan = report["triple_barrier_plan"]
    fp = report["future_projection_15d"]
    veto_prefix = (
        f"【风控否决：{'；'.join(debate.get('veto_reasons') or [])}，以下点位仅用于观察，不构成做多建议】"
        if debate.get("risk_officer_veto")
        else ""
    )
    mr_adj = mtf.get("regime_adjustment") or {}
    mr_note = f"{mr_adj['note_cn']}。" if mr_adj.get("applied") else ""
    flash_digest = (
        f"{veto_prefix}数据截至 {meta['as_of']}（数据源 {meta['data_source']}，{ADJUSTMENT_CN.get(meta.get('adjustment'), meta.get('adjustment'))}，质量 {quality['status']}）。"
        f"基准 {bench['index_name']}（{bench['index_symbol']}）处于{bench['regime_label_cn']}（状态分 {bench['regime_score']:.1f}/100）。"
        f"本标的规则分 {mtf['rule_score']:.1f}/100（{mtf['rating_cn']}，确定性规则加权分，不是胜率或上涨概率），{mr_note}"
        f"当前评级为「{decision['action_cn']}」（{decision['setup_label_cn']}），最新收盘 {price(last_close)}，"
        f"三重屏障计划入场区 {price(plan['entry_zone'][0])} ~ {price(plan['entry_zone'][1])}、止损 {price(plan['stop_loss_barrier'])}、"
        f"TP1 {price(plan['take_profit_1'])}（扣费后净盈亏比 {plan['risk_reward_ratio']:.2f}）；"
        f"未来 {fp['horizon_bars']} 日 80% 保形区间为 {price(fp['q10'][-1])} ~ {price(fp['q90'][-1])}（中枢 q50 = {price(fp['base_median_q50'][-1])}，只描述波动幅度，不含方向预测）。"
    )
    lines += [
        "## 1. 结论速览",
        "",
        f"- 直出摘要（供 Agent / Flash 模型首段原样引用）：{flash_digest}",
        f"- 结论：{debate['final_verdict_cn']}",
        f"- 规则分：**{mtf['rule_score']:.1f} / 100**（{mtf['rating_cn']}）。这是确定性规则加权分，不是胜率，也不是上涨概率。"
        + (f"{mr_note}" if mr_note else ""),
        f"- 最新收盘：{price(last_close)}；计划类型：{decision['setup_label_cn']}。",
        "",
    ]

    # 2. 大盘环境
    lines += ["## 2. 大盘环境（先大盘、后个股）", ""]
    if bench.get("benchmark_missing"):
        lines += [
            "- **缺少大盘基准行情**：无法评估大盘状态与相对强弱，风控已据此否决做多确认。",
            f"- 以下仅为标的自身状态，不代表大盘：{bench['regime_label_cn']}，状态分 {bench['regime_score']:.1f} / 100。",
            "",
        ]
    elif bench["index_symbol"].upper() == meta["symbol"].upper():
        lines += [
            f"- 标的本身即市场基准：{bench['regime_label_cn']}，状态分 {bench['regime_score']:.1f} / 100；不计算相对强弱。",
            "",
        ]
    else:
        rs = float(bench["relative_strength_vs_index"])
        rs_text = "跑赢基准" if rs > 1 else ("跑输基准" if rs < 1 else "与基准持平")
        lines += [
            "| 基准 | 市场状态 | 状态分 | 近 20 日相对强弱 |",
            "| --- | --- | --- | --- |",
            f"| {_cell(bench['index_name'])}（{bench['index_symbol']}） | {bench['regime_label_cn']} "
            f"| {bench['regime_score']:.1f} / 100 | {rs:.4f}（{rs_text}） |",
            "",
            "- 状态分低于 35 时风控会自动否决做多。",
            "",
        ]

    # 3. 结构与条件推演
    levels = report["mathematical_indicators"]["structure_levels"]
    wedge = levels["wedge_structure"]
    sc = report["rule_scenarios"]
    bull_hf = (sc["bull"].get("historical_frequency") or {}).get("display_cn") or "—"
    neut_hf = (sc["neutral"].get("historical_frequency") or {}).get("display_cn") or "—"
    bear_hf = (sc["bear"].get("historical_frequency") or {}).get("display_cn") or "—"
    lines += [
        "## 3. 结构与条件推演（触发 → 目标 → 失效）",
        "",
        f"- 当前结构（近 120 日）：{wedge['pattern']}，{wedge['status_text']}。",
        "",
        "| 路径 | 触发条件 | 目标位（来源） | 失效条件 | 历史条件频率 |",
        "| --- | --- | --- | --- | --- |",
        f"| 上攻 | {_cell(sc['bull']['trigger'])} | {price(sc['bull']['target_price'])}（{_cell(sc['bull']['target_source'])}） "
        f"| {_cell(sc['bull']['invalidation'])} | {_cell(bull_hf)} |",
        f"| 震荡 | {_cell(sc['neutral']['condition'])} | 区间 {price(sc['neutral']['range'][0])} ~ {price(sc['neutral']['range'][1])} | — | {_cell(neut_hf)} |",
        f"| 下探 | {_cell(sc['bear']['trigger'])} | {price(sc['bear']['target_price'])}（{_cell(sc['bear']['target_source'])}） "
        f"| {_cell(sc['bear']['invalidation'])} | {_cell(bear_hf)} |",
        "",
    ]
    multi_scenarios = sc.get("scenarios") or []
    if multi_scenarios:
        lines += [
            "**多情景推演与无前视历史条件频率**",
            "",
            "| 情景 | 状态 | 触发条件 | 目标位（来源） | 失效条件 | 预计用时（中位数） | 历史条件频率（先到目标） |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for item in multi_scenarios:
            tw = item.get("time_window_bars") or {}
            hf_item = item.get("historical_frequency") or {}
            tw_txt = f"{tw.get('median', '—')} 日（四分位 {tw.get('q25', '—')}~{tw.get('q75', '—')} 日）"
            lines += [
                f"| {_cell(item['name'])} | {_cell(item.get('status_cn', item.get('status')))} "
                f"| {_cell(item['trigger'])} | {price(item['target_price'])}（{_cell(item['target_source'])}） "
                f"| {_cell(item['invalidation'])} | {tw_txt} | {_cell(hf_item.get('display_cn', '—'))} |"
            ]
        lines += [
            "",
            "- 口径说明：历史条件频率是在本标的自身历史上逐日无前视统计的实际观测频率（只消费当时已成熟的样本，与括号内 Wilson 95% 区间同口径）。"
            "它统计的是以触发日 ATR 为单位的**标准化屏障事件**（如「15 根K线内先触及 +1.55×ATR 而非 −1.05×ATR」，各情景定义见 `historical_frequency.event_definition_cn`），"
            "**不是表中目标价的到达频率**；同一情景相邻触发日只间隔 2 根K线，结果期互相重叠，n 高估了独立样本数。"
            "括号内的「体制加权估计」「收缩后估计」是按趋势体制、近期样本或市场基准修正后的模型估计（风控守门与研判锚点使用该值），不是观测频率。"
            "该数字不是交易胜率，也不是未来上涨概率。",
            "",
        ]
    imp = sc.get("impulse_wave")
    if imp:
        lines += [f"- 推动浪识别：{imp['label']}（{imp['invalidation_text']}；1.618×浪1 测算位 {price(imp['wave3_1618_target'])}）。"]
    horizon_sc = sc.get("horizon_bars")
    for key, label in (("bull", "上攻"), ("bear", "下探")):
        eta = sc[key].get("eta_bars")
        if eta and horizon_sc:
            reach = f"在 {horizon_sc} 日预测期内" if sc[key].get("target_within_horizon") else f"超出 {horizon_sc} 日预测期"
            lines += [
                f"- {label}路径时间尺度：按近 60 日日波动 {sc['sigma_1d_pct']:.2f}% 的随机游走扩散尺度估算，走到触发位约 {max(1.0, eta['trigger']):.0f} 个交易日、"
                f"走到目标位约 {max(1.0, eta['target']):.0f} 个交易日，{reach}（只是时间量级，不是方向预测）。"
            ]
    contested = sc.get("contested_levels") or []
    if contested:
        items = "；".join(f"{price(c['price'])}（{'/'.join(c['sources'])}）" for c in contested)
        lines += [
            f"- 现价 {sc['min_trigger_distance_atr']} 个 ATR 以内正在争夺的价位：{items}。这些价位落在日常波动范围内，不作触发条件。"
        ]
    counts = levels.get("wave_counts") or []
    if counts:
        lines += ["", "| 浪型计数 | A 浪 | B 浪终点 | C 浪进度 | 1.0 倍测算位 | 否定条件 |", "| --- | --- | --- | --- | --- | --- |"]
        for wc in counts:
            lvl_1 = next((lv for lv in wc["levels"] if lv["ratio"] == 1.0), None)
            lines += [
                f"| {wc['role']}：{wc['label']} | {wc['a_start']['day']} {price(wc['a_start']['price'])} → "
                f"{wc['a_end']['day']} {price(wc['a_end']['price'])}（{wc['a_range_atr']:.1f} 个 ATR） "
                f"| {wc['b_end']['day']} {price(wc['b_end']['price'])} | {_cell(wc['c_progress']['phase'])} "
                f"| {price(lvl_1['price']) if lvl_1 else '—'}（{lvl_1['state'] if lvl_1 else '—'}） | {_cell(wc['count_invalidation']['text'])} |"
            ]
        lines += ["", "- 浪型计数是对同一段走势的不同数法，备选计数用于提示主计数可能错在哪里，不代表可能性大小。"]
    lines += [""]
    ladder = levels.get("key_levels") or {}
    res = [f"{price(x['price'])}（{'/'.join(x['sources'])}）" for x in (ladder.get("resistances") or [])[:3]]
    sup = [f"{price(x['price'])}（{'/'.join(x['sources'])}）" for x in (ladder.get("supports") or [])[:3]]
    lines += [
        f"- 上方压力（由近到远）：{'；'.join(res) if res else '无'}",
        f"- 下方支撑（由近到远）：{'；'.join(sup) if sup else '无'}",
        "",
    ]

    # 4. 风控计划
    plan = report["triple_barrier_plan"]
    rules = plan["market_microstructure"]
    sizing = plan["position_sizing"]
    lines += [
        "## 4. 风控计划（三重屏障）",
        "",
        "| 计划类型 | 入场区 | 止损 | TP1 | TP2 | 时间屏障 | 扣费后盈亏比 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        f"| {decision['setup_label_cn']} | {price(plan['entry_zone'][0])} ~ {price(plan['entry_zone'][1])} "
        f"| {price(plan['stop_loss_barrier'])} | {price(plan['take_profit_1'])} | {price(plan['take_profit_2'])} "
        f"| {plan['time_barrier_bars']} 根 K 线 | {plan['risk_reward_ratio']:.2f} |",
        "",
    ]
    limit = rules.get("daily_price_limit_pct")
    rule_bits = [
        SETTLEMENT_CN.get(rules["settlement"], f"交收 {rules['settlement']}"),
        f"涨跌幅限制 {'无' if limit is None else f'±{limit * 100:.0f}%'}",
        f"每手 {rules['lot_size']} 股（最小申报 {rules.get('min_order_shares', rules['lot_size'])} 股，步长 {rules.get('lot_step', rules['lot_size'])} 股）",
        f"印花税 买 {rules.get('stamp_duty_buy_bps', 0.0):g} / 卖 {rules['stamp_duty_sell_bps']:g} bps",
    ]
    lines += [f"- 交易制度：{'；'.join(rule_bits)}。"]
    if rules.get("lot_size_source") == "ASSUMED_DEFAULT":
        lines += ["- 港股每手股数为假设值（100 股），下单前请向券商核对。"]
    if rules.get("settlement_note"):
        lines += [f"- 注意：{rules['settlement_note']}。"]
    if rules.get("day_trading_rule"):
        lines += [f"- 日内交易：{rules['day_trading_rule']}。"]
    if sizing.get("recommended_shares") == 0:
        lines += [
            f"- 仓位：账户 {sizing['account_equity']:,.2f}、单笔风险 {sizing['max_risk_pct']:.2f}% 时，"
            f"风险预算 {sizing['risk_budget_amount']:,.2f} 按止损距离折算不足最小申报 {sizing['min_order_shares']} 股，"
            "**该计划在此账户与风险比例下没有可执行的股数**。"
        ]
    elif sizing.get("recommended_shares") is not None:
        lines += [
            f"- 仓位：账户 {sizing['account_equity']:,.2f}、单笔风险 {sizing['max_risk_pct']:.2f}% 时，"
            f"按止损距离计算为 **{sizing['recommended_shares']} 股**（约 {sizing['estimated_notional']:,.2f} {meta['currency']}）。"
        ]
    elif sizing.get("reason"):
        lines += [f"- 仓位：{sizing['reason']}"]
    if debate.get("risk_officer_veto"):
        lines += ["- 风控已否决：以上点位只用于观察与复核，不构成做多建议。"]
    defense = plan.get("holder_defense_plan") or {}
    if defense.get("available"):
        lines += ["", "**持仓防守计划**（已持有该标的时参考）", ""] + [f"- {r}" for r in defense["rules"]]
        lines += [f"- 下方参考位：{price(defense['downside_reference']['price'])}（{_cell(defense['downside_reference']['source'])}）。"]
        if defense.get("note"):
            lines += [f"- {defense['note']}。"]
    short = plan.get("short_plan") or {}
    if short.get("available"):
        lines += [
            "",
            "**破位做空计划**（需融券，仅港股/美股）",
            "",
            "| 触发 | 入场区 | 止损 | TP1 | TP2 | 时间屏障 | 扣费后盈亏比（未计融券费） |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            f"| {_cell(short['trigger'])} | {price(short['entry_zone'][0])} ~ {price(short['entry_zone'][1])} "
            f"| {price(short['stop_loss_barrier'])} | {price(short['take_profit_1'])} | {price(short['take_profit_2'])} "
            f"| {short['time_barrier_bars']} 根 K 线 | {short['risk_reward_ratio']:.2f} |",
            "",
            f"- 失效：{short['invalidation']}。",
            f"- 交易限制：{short['constraints']}。",
        ]
        lines += [f"- 注意：{w}。" for w in short.get("warnings") or []]
    elif short.get("reason") and asset_type != "index":
        lines += ["", f"- 做空：{short['reason']}。"]
    lines += [""]

    # 5. 形态与信号证据
    lines += ["## 5. 形态与信号证据（公式可复核）", ""]
    patterns = report.get("algebraic_patterns_detected") or []
    for p in patterns[:6]:
        lines += [
            f"- {p['name_cn']}（{DIRECTION_CN.get(p['direction'], p['direction'])}，{p.get('confirmed_at') or p.get('observed_at')}）："
            f"`{p['formula_verified']}`"
        ]
    for s in report.get("strategy_signals") or []:
        if s.get("direction") != "NEUTRAL":
            lines += [f"- 策略 {s['name_cn']} → {s['state']}：`{s['formula_verified']}`"]
    breaks = (report.get("smc_structures") or {}).get("structure_breaks") or []
    if breaks:
        last_break = breaks[-1]
        lines += [f"- 最近一次结构破位：{last_break['type']}（{last_break['day']}，破位价 {price(last_break['broken_level'])}）"]
    if not patterns and not breaks and not any(s.get("direction") != "NEUTRAL" for s in report.get("strategy_signals") or []):
        lines += ["- 当前窗口未触发蜡烛形态、背离或策略信号。"]
    lines += [""]

    # 6. 未来区间
    fp = report["future_projection_15d"]
    audit = fp["calibration_audit"]
    horizon = int(fp["horizon_bars"])
    steps = sorted({h for h in (1, 5, 10, horizon) if 1 <= h <= horizon})
    lines += [
        f"## 6. 未来 {horizon} 个交易日价格区间（只描述波动幅度，不含方向判断）",
        "",
        "| 日期 | 第几日 | 80% 下沿 q10 | 50% 下沿 q25 | 中枢 q50 | 50% 上沿 q75 | 80% 上沿 q90 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for h in steps:
        i = h - 1
        lines += [
            f"| {fp['horizon_dates'][i]} | {h} | {price(fp['q10'][i])} | {price(fp['q25'][i])} | {price(fp['base_median_q50'][i])} "
            f"| {price(fp['optimistic_q75'][i])} | {price(fp['q90'][i])} |"
        ]
    promoted = "已达标" if fp["promotion_status"] == "PROMOTED" else "校准观察期"
    lines += [
        "",
        "- 中枢 q50 就是最新收盘价（不加任何漂移），区间宽度来自历史波动的分期限校准。",
        (
            f"- 历史样本外实测覆盖率：50% 区间 {_pct(audit['nominal_50pct_empirical_coverage'])}，"
            f"80% 区间 {_pct(audit['nominal_80pct_empirical_coverage'])}（评估样本 {audit['evaluation_samples']} 个，"
            f"相邻样本结果期重叠，约等于 {audit['effective_independent_samples']} 个独立样本；状态：{promoted}）。"
        ),
        (
            f"- 「已达标」要求约 {audit['min_effective_independent_samples']} 个以上独立样本，且两档覆盖率与名义值相差都不超过 10 个百分点；"
            "否则为「校准观察期」，覆盖率仅供参考。"
        ),
    ]
    if audit.get("uncalibrated_overlays"):
        lines += [f"- 未经历史校准的放大项：{'；'.join(audit['uncalibrated_overlays'])}。"]
    cal = fp.get("calendar_coverage") or {}
    if cal and not cal.get("covered", True):
        lines += [f"- {len(cal['estimated_dates'])} 个未来日期超出休市表覆盖范围（至 {cal['coverage_end']}），按工作日估算。"]
    scf = fp.get("scenario_conditional_fan") or {}
    if scf and scf.get("center_q50"):
        lines += [
            f"- 附：{scf.get('legend_label_cn', '情景混合（非校准区间）')}（仅供展示，不替代上方默认保形区间）："
            f"按各情景历史条件频率加权混合，第 {horizon} 日混合中枢 {price(scf['center_q50'][-1])}，"
            f"80% 混合带 {price(scf['q10'][-1])} ~ {price(scf['q90'][-1])}。"
        ]
    lines += ["- 这是逐日的边际区间，不代表整条价格路径的联合概率。", ""]

    # 7. 回测
    if backtest:
        bt = backtest.get("backtest") or {}
        audit_bt = backtest.get("causal_replay_audit") or {}
        window = bt.get("decision_window") or {}
        lines += [
            "## 7. 历史计划回放回测",
            "",
            f"- 回放区间 {window.get('start_day')} ~ {window.get('end_day')}，共 {window.get('decisions')} 个决策日；"
            f"规则：{bt.get('entry_rule')}。",
            "",
            "| 口径 | 成交笔数 | 胜率（95% 区间） | 累计收益 | 最大回撤 | 盈亏比 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for label, key in (("全部做多信号", "all_long_signals"), ("确认做多", "long_confirmed"), ("观察做多", "watch_long_setup")):
            row = bt.get(key) or {}
            if not row.get("trades"):
                lines += [f"| {label} | 0 | — | — | — | — |"]
                continue
            ci = row.get("win_rate_95ci_pct")
            ci_text = f"{row['win_rate_pct']:.1f}%" + (f"（{ci[0]:.1f}% ~ {ci[1]:.1f}%）" if ci else "")
            lines += [
                f"| {label} | {row['trades']} | {ci_text} | {row['cumulative_return_pct']:.2f}% "
                f"| {row['max_drawdown_pct']:.2f}% | {row['profit_factor']:.2f} |"
            ]
        lines += ["", f"- 同期买入持有收益：{bt.get('buy_and_hold_return_pct', 0):.2f}%。"]
        if bt.get("sample_size_warning"):
            lines += [f"- 样本提示：{bt['sample_size_warning']}。"]
        ic = bt.get("rule_score_ic") or {}
        if ic:
            ic_text = "—" if ic.get("spearman") is None else f"{ic['spearman']:.3f}"
            lines += [
                f"- 规则分与未来 {ic['forward_horizon_bars']} 日收益的秩相关 IC = {ic_text}"
                f"（{ic['decisions']} 个决策日，约 {ic['effective_independent_samples']} 个独立样本）：{ic['verdict']}。"
            ]
        afr = bt.get("action_forward_returns") or {}
        if any(row.get("decisions") for row in afr.values()):
            lines += [
                "",
                f"| 决策日动作 | 次数 | 未来 {ic.get('forward_horizon_bars', horizon)} 日平均收益 | 中位数 | 上涨占比 |",
                "| --- | --- | --- | --- | --- |",
            ]
            action_cn = {
                "LONG_CONFIRMED": "确认做多",
                "WATCH_LONG_SETUP": "观察做多",
                "NEUTRAL_HOLD_WAIT": "观望",
                "DEFENSIVE_AVOID": "防守回避",
            }
            for action, row in afr.items():
                if not row.get("decisions"):
                    continue
                lines += [
                    f"| {action_cn.get(action, action)} | {row['decisions']} | {row['mean_forward_return_pct']:.2f}% "
                    f"| {row['median_forward_return_pct']:.2f}% | {row['up_share_pct']:.1f}% |"
                ]
            lines += ["", "- 上表是每个决策日之后的实际涨跌，用于检验动作标签是否与后续走势一致，不是交易收益。"]
        veto_counts = bt.get("veto_reason_counts") or {}
        if veto_counts:
            veto_cn = {
                "BENCHMARK_MISSING": "缺少大盘基准",
                "BENCHMARK_WEAK": "大盘偏弱",
                "RR_INSUFFICIENT": "盈亏比不足",
                "BELOW_LOWER_RAIL": "收盘在下轨下方",
                "BREAKOUT_LOW_VOLUME": "突破缺量",
            }
            lines += ["- 风控否决原因统计：" + "；".join(f"{veto_cn.get(k, k)} {v} 次" for k, v in veto_counts.items()) + "。"]
        sfb = bt.get("scenario_frequency_brier") or {}
        sfb_n = sfb.get("samples") or sfb.get("evaluated_events") or 0
        if sfb and sfb_n > 0:
            bss = sfb.get("brier_skill_score")
            bss_txt = "—" if bss is None else f"{bss:+.4f}"
            lines += [
                f"- 历史条件频率样本外检验（共 {sfb_n} 次情景事件）："
                f"Brier 分数 = {sfb['brier_score']:.4f}，全样本基准率（climatology = {_pct(sfb.get('climatology_base_rate'))}）"
                f"Brier 分数 = {sfb['climatology_brier_score']:.4f}，技能分 BSS = {bss_txt}（{sfb.get('verdict')}）。"
            ]
            non_empty_buckets = [b for b in (sfb.get("reliability_buckets") or []) if b.get("count", 0) > 0]
            if non_empty_buckets:
                lines += [
                    "",
                    "| 预测历史频率分桶 | 事件数 | 平均预测频率 | 实际先到目标占比 |",
                    "| --- | --- | --- | --- |",
                ]
                for b in non_empty_buckets:
                    b_label = b.get("bucket_pct") or b["bucket"]
                    lines += [
                        f"| {b_label} | {b['count']} | {_pct(b['mean_predicted_freq'])} | {_pct(b['empirical_hit_rate'])} |"
                    ]
                lines += [""]
        if audit_bt:
            lines += [
                f"- 无前视审计：{'通过' if audit_bt.get('passed') else '未通过'}"
                f"（检查 {audit_bt.get('checked_cutoffs')} 个截面，差异 {audit_bt.get('mismatches_count')} 处）。"
            ]
        lines += [""]

    # 8. LLM 情景研判（主观判断，已记录待检验）— 与上方确定性计算部分明确分开
    active_judgment = judgment or report.get("llm_judgment")
    jpack = report.get("judgment_pack") or {}
    if active_judgment:
        from judgment import AUTO_INIT_GENERATOR, judgment_origin

        if judgment_origin(active_judgment, report) == AUTO_INIT_GENERATOR:
            lines += [
                "## 8. 情景研判模板（脚本预填，未经 Agent 主观研判）",
                "",
                "> **模板声明**：本节由 `validate_judgment.py --auto-init` 按偏多/偏空证据条数机械预填（每情景调整不超过 ±4 个百分点），"
                "不是 Agent 的主观研判；对外转述时只能称「脚本预填模板」，不得称为主观研判概率。日志中单独标注来源，不计入 LLM 技能分。",
                "",
            ]
        else:
            lines += [
                "## 8. LLM 情景研判（主观判断，已记录待检验）",
                "",
                "> **主观研判声明**：本节为锚定 L2 历史条件频率的受约束主观研判（非确定性公式计算），调整幅度受样本量上限约束，且已记入日志待到期按 Brier 分数与技能分 BSS 检验。",
                "",
            ]
        lines += [
            f"- 研判综述：{active_judgment.get('summary_cn', '')}",
            "",
            "| 情景 | L2 历史条件频率 | 基准概率（调整前） | 主观研判概率（调整后） | 调整幅度（上限） | 支持证据 | 反对/约束证据 | 调整理由 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for sj in active_judgment.get("scenario_judgments") or []:
            sup_txt = ", ".join(sj.get("supporting_evidence_ids") or []) or "—"
            opp_txt = ", ".join(sj.get("opposing_evidence_ids") or []) or "—"
            l2_disp = sj.get("l2_display_cn") or (
                f"{sj['l2_historical_frequency_pct']:.1f}%" if sj.get("l2_historical_frequency_pct") is not None else "—"
            )
            max_adj = sj.get("max_adjustment_pct", 10.0 if sj.get("low_sample") else 15.0)
            lines += [
                f"| {_cell(sj['scenario_name'])} | {_cell(l2_disp)} | {sj['baseline_probability_pct']:.2f}% "
                f"| **{sj['subjective_probability_pct']:.2f}%** | {sj['adjustment_pct']:+.2f}%（±{max_adj:.0f}%） "
                f"| {_cell(sup_txt)} | {_cell(opp_txt)} | {_cell(sj.get('rationale_cn', ''))} |"
            ]
        lines += [""]
        kls = active_judgment.get("key_levels_to_watch") or []
        if kls:
            kl_bits = [f"{_cell(k['label'])} {price(k['price'])}" for k in kls]
            lines += [f"- 关键观察价位（均取自 JSON 白名单）：{'；'.join(kl_bits)}。"]
        if active_judgment.get("falsification_condition"):
            lines += [f"- 证伪条件（什么情况说明判断错了）：{active_judgment['falsification_condition']}"]
        score_sum = judgment_score_summary or active_judgment.get("historical_score_summary")
        if isinstance(score_sum, dict) and score_sum.get("matured_judgments", 0) >= 30:
            bss_val = float(score_sum.get("brier_skill_score") or 0.0)
            n_mat = int(score_sum["matured_judgments"])
            if bss_val > 0.0:
                lines += [f"- 历史研判评估：LLM 调整相对历史频率的技能分为 {bss_val:+.4f}（n = {n_mat}）。"]
            else:
                lines += [f"- 历史研判评估：LLM 调整尚未显示增益（技能分 BSS = {bss_val:+.4f} ≤ 0，n = {n_mat}）。"]
        lines += [""]
    elif jpack.get("evidence_items"):
        evs = jpack["evidence_items"]
        confs = jpack.get("conflicting_signals") or []
        lines += [
            "## 8. LLM 情景研判证据包（供受约束主观研判使用）",
            "",
            f"- 已生成结构化证据包（共 {len(evs)} 条可溯源证据 `E1..E{len(evs)}`、{len(confs)} 组冲突信号），"
            "Agent 按 `references/llm-judgment-protocol.md` 完成 `*_judgment.json` 并通过 `scripts/validate_judgment.py` 校验后，将在此展示调整前后的主观研判概率。",
            "",
        ]

    # 9. 文件
    paths = report.get("rendered_paths") or {}
    file_labels = (
        ("report_md", "本报告"),
        ("result_json", "完整计算结果 JSON"),
        ("judgment_pack_json", "L3 研判证据包 JSON"),
        ("judgment_json", "L3 主观研判 JSON"),
        ("dashboard_html", "离线交互看板 HTML"),
        ("preview_png", "结构推演图 PNG"),
        ("backtest_json", "回测明细 JSON"),
    )
    listed = [(label, paths[key]) for key, label in file_labels if paths.get(key)]
    if listed:
        sec_hdr = "## 9. 产物文件" if (active_judgment or jpack.get("evidence_items")) else "## 8. 产物文件"
        lines += [sec_hdr, ""] + [f"- {label}：`{path}`" for label, path in listed] + [""]

    lines += ["---", f"*{DISCLAIMER}*", ""]
    return "\n".join(lines)
