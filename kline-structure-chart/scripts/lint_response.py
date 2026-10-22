#!/usr/bin/env python3
"""Agent 最终回复文本校验器：检查回复是否遵守 SKILL.md 回复契约与数据真实性铁律。

校验项：
1. quality.status == UNAVAILABLE 时，禁止输出任何价格点位或交易计划。
2. quality.status == PARTIAL 时，必须在回复前部披露数据告警。
3. debate_and_veto.risk_officer_veto == true 时，必须在回复开头（前 400 个字符内）出现「风控否决」提示。
4. 严禁把规则分（rule_score）称为「胜率」「上涨概率」或「置信度」；严禁「上涨概率 70%」「大概率」「胜算」等未经校验的概率措辞。
5. 价位词或交易动作（目标、止损、买入、突破、站上……）前后出现的价格数字必须能在 result.json 白名单中找到，严禁捏造价格。
6. 必须包含免责声明（「不构成投资建议」）。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from price_text import CONTEXT_PRICE_RE, NON_PRICE_PATTERNS, collect_price_whitelist, price_in_whitelist

_FORBIDDEN_PROB_PHRASES = (
    re.compile(r"(?:多周期)?(?:规则(?:综合)?(?:评分|分)|过滤分|状态分)[^\n。；]{0,16}(?:胜率|上涨概率|置信度|确定性)"),
    re.compile(r"(?:胜率|上涨概率|置信度|确定性)[^\n。；]{0,12}(?:多周期)?(?:规则(?:综合)?(?:评分|分)|过滤分|状态分)"),
    re.compile(r"(?:主力|真实)?持仓筹码|主力(?:真实)?筹码|获利盘(?:比例|占比)"),
)

# 未经校验的概率/确定性措辞：只有历史条件频率、通过校验的主观研判、回测成交胜率（带 95% 区间）与区间覆盖率可以带概率数字
_UNVALIDATED_PROB_PHRASES = (
    re.compile(r"大概率|小概率|胜算|稳赚|必涨|必跌|十拿九稳|板上钉钉"),
    re.compile(r"(?:上涨|下跌|突破|反弹|上攻|下探|成功|盈利|翻倍|见顶|见底|涨|跌)(?:的)?(?:概率|可能性|几率)"),
    re.compile(
        r"(?:概率|可能性|几率|胜率|成功率|命中率|命中概率|准确率|把握)\s*"
        r"(?:约|大约|为|是|达|达到|高达|超|超过|高于|接近|有|在|>=?|:|：|=)?\s*\d+(?:\.\d+)?\s*%"
    ),
    re.compile(r"[一二三四五六七八九]成(?:把握|胜算|概率|机会|以上)?(?=[^\d]|$)"),
)
_NEGATION_RE = re.compile(r"不是|不叫|不代表|并非|不得|不能|严禁|禁止|不构成|不等于|不要|不应|不宜|而非|不含|没有")
_PROB_ALLOWED_CONTEXT_RE = re.compile(r"历史条件频率|主观研判|脚本预填|研判模板|覆盖率|95% 区间|95%区间|Wilson")
# 回测胜率专用：必须与 backtest.json 的成交样本一致；小样本不得被包装为 70% 目标证据
_BACKTEST_RATE_RE = re.compile(
    r"(?:胜率|成功率|命中率|命中概率|准确率)\s*(?:为|是|约|达|达到|高达|超|超过|高于|接近|在|>=?|:|：|=|攀升至)?\s*(\d+(?:\.\d+)?)\s*%"
    r"|(\d+(?:\.\d+)?)\s*%\s*的(?:胜率|成功率|命中率|命中概率|准确率)"
)
_SAMPLE_HINT_RE = re.compile(r"(?:n\s*=\s*\d+|\d+\s*笔(?:成交|交易)?|\d+\s*个?样本)")
_SMALL_SAMPLE_CLAIM_RE = re.compile(
    r"(?:证明|充分证据|足以说明|可作为)[^。；\n]{0,24}(?:70\s*%|目标)"
    r"|(?<!未)(?<!尚未)(?:达到|超过|高于|实现)\s*70\s*%"
    r"|(?:70\s*%|目标)[^。；\n]{0,8}(?:已达成|已实现)"
)
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]")

# 价格语境（比 CONTEXT_PRICE_RE 更宽）：交易动作或价位词之后 4 个字符内的数字，以及紧跟交易动作的数字
_LINT_PRICE_AFTER_RE = re.compile(
    r"(?:目标位?|失效位?|止损位?|止盈位?|触发位?|破位价|支撑位?|压力位?|阻力位?|现价|收盘价?|POC|VAH|VAL|TP1|TP2|入场区?|买入|卖出|"
    r"建仓|加仓|减仓|挂单|低吸|抄底|回踩|突破|跌破|站上|站稳|站回|收回|上破|下破|看至|看到|上看|下看|关注|观察位?|参考位?|价位)"
    r"[^\d\n。；，,、（）()|]{0,4}?(\d+(?:\.\d+)?)"
)
_LINT_PRICE_BEFORE_RE = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:元|港元|美元|港币)?\s*(?:附近|一线|上方|下方|左右|处|之上|之下|上下)?\s*"
    r"(?:买入|卖出|建仓|加仓|减仓|止损|止盈|挂单|入场|抄底|离场|低吸)"
)
VETO_HEAD_CHARS = 250
# 买入/建仓语境下的股数（「每手 100 股」「最小申报 200 股」等制度描述不在此列）
_BUY_SHARES_RE = re.compile(r"(?:买入|买进|建仓|加仓|开仓|仓位|可买|买|低吸|挂单)[^。；\n股手]{0,8}?\d[\d,]*\s*股")
# 子句级匹配：允许价格/介词插在建议与动作之间，覆盖「可于…建仓」「逢低吸纳」等话术。
_VETO_POSITIVE_BUY_RE = re.compile(
    r"(?:建议|可以?|应当|不妨|推荐|宜|考虑|适合|值得|投资者可|逢低)"
    r"[^。；\n]{0,30}?(?:买入|买进|建仓|加仓|做多|抄底|低吸|进场|入场|布局|吸纳|吸筹)"
)
_TARGET_ROLE_PRICE_RE = re.compile(r"(?:目标位|目标价|目标看至|上看目标)\s*[为=:：约至]?\s*(\d+(?:\.\d+)?)")


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max((m.end() for m in _SENTENCE_SPLIT_RE.finditer(text, 0, start)), default=0)
    right_m = _SENTENCE_SPLIT_RE.search(text, end)
    return text[left : right_m.start() if right_m else len(text)]


def _unvalidated_probability_hits(text: str) -> list[str]:
    hits: list[str] = []
    numeric_pat = _UNVALIDATED_PROB_PHRASES[2]
    for pat in _UNVALIDATED_PROB_PHRASES:
        for m in pat.finditer(text):
            sentence_start = max((sm.end() for sm in _SENTENCE_SPLIT_RE.finditer(text, 0, m.start())), default=0)
            before = text[sentence_start : m.start()]
            if _NEGATION_RE.search(before):
                continue  # 「不是胜率或上涨概率」「不得出现大概率突破」之类的否定说明
            if pat is numeric_pat and _PROB_ALLOWED_CONTEXT_RE.search(_sentence_around(text, m.start(), m.end())):
                continue
            hits.append(m.group(0))
    return hits


def lint_agent_response(
    response_text: str,
    report: dict[str, Any],
    backtest: dict[str, Any] | None = None,
) -> list[str]:
    """校验 Agent 回复文本，返回违规列表（空列表表示合规）。"""
    errors: list[str] = []
    text = (response_text or "").strip()
    if not text:
        return ["回复文本为空"]

    quality = report.get("quality") or {}
    q_status = str(quality.get("status") or "OK")
    debate = report.get("debate_and_veto") or {}
    veto = bool(debate.get("risk_officer_veto"))

    # 1. UNAVAILABLE 检查
    if q_status == "UNAVAILABLE":
        if any(pat.search(text) for pat in (CONTEXT_PRICE_RE, _LINT_PRICE_AFTER_RE, _LINT_PRICE_BEFORE_RE)):
            errors.append("quality.status == UNAVAILABLE 时，严禁在回复中输出任何具体价格点位或交易计划")
        return errors

    # 2. PARTIAL 告警置顶检查
    if q_status == "PARTIAL":
        head = text[:500]
        if not any(tok in head for tok in ("PARTIAL", "数据说明", "数据质量", "缺少大盘", "来源待核验", "本地文件")):
            errors.append("quality.status == PARTIAL 时，必须在回复前部披露数据质量告警")

    # 3. 风控否决置顶检查
    if veto:
        if "风控否决" not in text and "风控已否决" not in text:
            errors.append("risk_officer_veto == true 时，回复必须明确写出「风控否决」")
        first_veto = min(
            (pos for pos in (text.find("风控否决"), text.find("风控已否决")) if pos >= 0),
            default=-1,
        )
        first_plan = text.find("风控计划")
        if first_veto >= 0 and first_plan >= 0 and first_veto > first_plan:
            errors.append("risk_officer_veto == true 时，「风控否决」提示必须出现在「风控计划」之前")
        if first_veto > VETO_HEAD_CHARS:
            errors.append(f"risk_officer_veto == true 时，「风控否决」必须置顶（出现在回复前 {VETO_HEAD_CHARS} 个字符内）")
        for m_buy in _VETO_POSITIVE_BUY_RE.finditer(text):
            sent_start = max((sm.end() for sm in _SENTENCE_SPLIT_RE.finditer(text, 0, m_buy.start())), default=0)
            before_buy = text[sent_start : m_buy.start()]
            if not _NEGATION_RE.search(before_buy) and not _NEGATION_RE.search(m_buy.group(0)):
                errors.append(f"risk_officer_veto == true 时，严禁给出做多/买入建议: {m_buy.group(0)!r}")

    # 3b. 报告没有给出建议股数（风控否决、评级非做多或未给账户净值）时，回复不得自行换算买入股数
    sizing = (report.get("triple_barrier_plan") or {}).get("position_sizing") or {}
    if sizing.get("recommended_shares") is None:
        for m_sh in _BUY_SHARES_RE.finditer(text):
            sent_sh = _sentence_around(text, m_sh.start(), m_sh.end())
            # 「每手 100 股」「按 100 股/手假设」「最小申报 200 股」是制度说明，不是建议买入股数。
            if not any(tok in sent_sh for tok in ("假设", "每手", "最小申报", "步长", "交易制度", "股/手")):
                errors.append(f"报告未给出建议股数（{sizing.get('reason') or '无账户净值'}），回复不得出现买入股数: {m_sh.group(0)!r}")
                break

    # 4. 禁用术语检查
    for pat in _FORBIDDEN_PROB_PHRASES:
        # 「规则分不是胜率或上涨概率」「不是真实持仓筹码」是合规的否定说明，不算违规
        for hit in pat.finditer(text):
            sent_start = max((sm.end() for sm in _SENTENCE_SPLIT_RE.finditer(text, 0, hit.start())), default=0)
            before_hit = text[sent_start : hit.start()]
            if not _NEGATION_RE.search(hit.group(0)) and not _NEGATION_RE.search(before_hit):
                errors.append(f"检测到违规表述: {hit.group(0)!r}（禁止将规则分称为胜率/概率/置信度，禁止将成交分布称为真实持仓筹码）")
                break
    for hit in dict.fromkeys(_unvalidated_probability_hits(text)):
        errors.append(
            f"检测到未经校验的概率/确定性表述: {hit!r}（只允许引用历史条件频率、通过校验的主观研判、带 95% 区间的回测胜率与区间覆盖率）"
        )
    allowed_hf_pcts: list[float] = []
    rs_obj = report.get("rule_scenarios") or {}
    for sc_item in list(rs_obj.get("scenarios") or []) + [rs_obj.get(k) for k in ("bull", "neutral", "bear") if isinstance(rs_obj.get(k), dict)]:
        hf = (sc_item or {}).get("historical_frequency") or {}
        for fk in ("raw_target_freq_pct", "shrunk_target_freq_pct", "target_freq_pct"):
            if hf.get(fk) is not None:
                allowed_hf_pcts.append(float(hf[fk]))
    if allowed_hf_pcts:
        for m_hf in re.finditer(r"历史条件频率\s*(?:为|是|约)?\s*(\d+(?:\.\d+)?)\s*%", text):
            val_hf = float(m_hf.group(1))
            if not any(abs(val_hf - p) <= 0.25 for p in allowed_hf_pcts):
                errors.append(f"回复中的历史条件频率 {val_hf}% 与 result.json 不一致")

    if backtest is not None:
        bt_node = backtest.get("backtest") if isinstance(backtest.get("backtest"), dict) else backtest
        allowed_wr: list[float] = []
        sample_sizes: list[int] = []
        for bk in ("all_long_signals", "long_confirmed", "watch_long_setup"):
            row = bt_node.get(bk) if isinstance(bt_node, dict) else None
            if isinstance(row, dict):
                if row.get("win_rate_pct") is not None:
                    allowed_wr.append(float(row["win_rate_pct"]))
                if row.get("trades") is not None:
                    sample_sizes.append(int(row["trades"]))
        small_sample = bool(sample_sizes) and min(sample_sizes) < 30
        for m_wr in _BACKTEST_RATE_RE.finditer(text):
            raw = m_wr.group(1) or m_wr.group(2)
            val_wr = float(raw)
            if allowed_wr and not any(abs(val_wr - w) <= 0.25 for w in allowed_wr):
                errors.append(f"回复中的回测胜率 {val_wr}% 与 backtest.json ({allowed_wr}) 不一致")
            if small_sample:
                sent = _sentence_around(text, m_wr.start(), m_wr.end())
                if not _SAMPLE_HINT_RE.search(sent):
                    errors.append("小样本回测胜率必须同时给出样本量 n（禁止只报百分比，n<30 不构成统计证据）")
                if _SMALL_SAMPLE_CLAIM_RE.search(sent) or _SMALL_SAMPLE_CLAIM_RE.search(text[m_wr.start() : min(len(text), m_wr.end() + 120)]):
                    errors.append("小样本回测（n<30）不得被表述为达到/证明 70% 目标或作为充分证据")

    # 5. 价格白名单检查（针对带价格语境的数值）
    meta = report.get("meta") or {}
    whitelist = collect_price_whitelist(
        {k: report[k] for k in ("mathematical_indicators", "smc_structures", "volume_profile",
                                "triple_barrier_plan", "rule_scenarios", "future_projection_15d") if k in report},
        int(meta.get("price_decimals") or 2),
        (report.get("series_export") or {}).get("bars") or [],
    )
    wl_prices: list[float] = [float(p) for p in whitelist.get("prices") or []]
    wl_tol = float(whitelist.get("tolerance") or 0.01)
    wave_only_prices: list[float] = []
    levels_node = (report.get("mathematical_indicators") or {}).get("structure_levels") or {}
    for wc in list(levels_node.get("wave_counts") or []) + ([levels_node["abc_scenario"]] if levels_node.get("abc_scenario") else []):
        for lv in (wc or {}).get("levels") or []:
            if lv.get("price") is not None:
                wave_only_prices.append(float(lv["price"]))
    valid_target_prices: list[float] = []
    tb_node = report.get("triple_barrier_plan") or {}
    for tk in ("take_profit_1", "take_profit_2"):
        if tb_node.get(tk) is not None:
            valid_target_prices.append(float(tb_node[tk]))
    sp_node = tb_node.get("short_plan") or {}
    for tk in ("take_profit_1", "take_profit_2"):
        if sp_node.get(tk) is not None:
            valid_target_prices.append(float(sp_node[tk]))
    for sc_item in list(rs_obj.get("scenarios") or []) + [rs_obj.get(k) for k in ("bull", "neutral", "bear") if isinstance(rs_obj.get(k), dict)]:
        if isinstance(sc_item, dict) and sc_item.get("target_price") is not None:
            valid_target_prices.append(float(sc_item["target_price"]))
    if isinstance(rs_obj.get("impulse_wave"), dict) and rs_obj["impulse_wave"].get("wave3_1618_target") is not None:
        valid_target_prices.append(float(rs_obj["impulse_wave"]["wave3_1618_target"]))

    cleaned = text
    for pat in NON_PRICE_PATTERNS:
        cleaned = pat.sub(" ", cleaned)
    for m_tp in _TARGET_ROLE_PRICE_RE.finditer(cleaned):
        sent = _sentence_around(cleaned, m_tp.start(), m_tp.end())
        if any(w in sent for w in ("测算", "ABC", "浪")):
            continue
        cand_tp = float(m_tp.group(1))
        if (
            price_in_whitelist(cand_tp, wave_only_prices, wl_tol)
            and not price_in_whitelist(cand_tp, valid_target_prices, wl_tol)
        ):
            errors.append(f"不得把浪型测算位 {cand_tp} 当作目标位/目标价: {m_tp.group(0)!r}")
    seen_spans: set[tuple[int, int]] = set()
    for pat in (CONTEXT_PRICE_RE, _LINT_PRICE_AFTER_RE, _LINT_PRICE_BEFORE_RE):
        for m in pat.finditer(cleaned):
            span = m.span(1)
            if span in seen_spans:
                continue
            seen_spans.add(span)
            cand = float(m.group(1))
            msg = f"回复中出现了不在 result.json 白名单中的价格: {m.group(0)!r} ({cand})"
            if not price_in_whitelist(cand, wl_prices, wl_tol) and msg not in errors:
                errors.append(msg)

    # 6. 免责声明检查
    if "不构成投资建议" not in text:
        errors.append("回复缺少免责声明（「不构成投资建议」）")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Lint agent response markdown against result.json contract")
    parser.add_argument("--result", required=True, help="Path to *_result.json")
    parser.add_argument("--response", required=True, help="Path to markdown/text response file")
    parser.add_argument("--backtest", default=None, help="Optional path to *_backtest.json")
    args = parser.parse_args()

    res_path = Path(args.result)
    resp_path = Path(args.response)
    if not res_path.exists():
        raise SystemExit(f"[INVALID_ARGUMENT] result file not found: {res_path}")
    if not resp_path.exists():
        raise SystemExit(f"[INVALID_ARGUMENT] response file not found: {resp_path}")

    report = json.loads(res_path.read_text(encoding="utf-8"))
    bt = json.loads(Path(args.backtest).read_text(encoding="utf-8")) if args.backtest else None
    text = resp_path.read_text(encoding="utf-8")

    errors = lint_agent_response(text, report, backtest=bt)
    if errors:
        raise SystemExit(f"[RESPONSE_CONTRACT_VIOLATION] 回复校验未通过（共 {len(errors)} 项）：{'；'.join(errors)}")
    print(json.dumps({"status": "OK", "symbol": report["meta"]["symbol"], "response": str(resp_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
