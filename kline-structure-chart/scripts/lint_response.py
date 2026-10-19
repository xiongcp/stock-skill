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

from judgment import build_judgment_pack
from validate_judgment import _CONTEXT_PRICE_RE, _NON_PRICE_PATTERNS, _price_in_whitelist

_FORBIDDEN_PROB_PHRASES = (
    re.compile(r"规则分[^\n。；]{0,16}(?:胜率|上涨概率|置信度)"),
    re.compile(r"(?:胜率|上涨概率|置信度)[^\n。；]{0,12}规则分"),
    re.compile(r"真实持仓筹码|获利盘比例"),
)

# 未经校验的概率/确定性措辞：只有历史条件频率、通过校验的主观研判、回测成交胜率（带 95% 区间）与区间覆盖率可以带概率数字
_UNVALIDATED_PROB_PHRASES = (
    re.compile(r"大概率|小概率|胜算|稳赚|必涨|必跌|十拿九稳|板上钉钉"),
    re.compile(r"(?:上涨|下跌|突破|反弹|上攻|下探|成功|盈利|翻倍|见顶|见底|涨|跌)(?:的)?(?:概率|可能性|几率)"),
    re.compile(r"(?:概率|可能性|几率|胜率|把握)\s*(?:约|大约|为|是|达|高达|超过|接近|有|在)?\s*\d+(?:\.\d+)?\s*%"),
    re.compile(r"[一二三四五六七八九]成(?:把握|胜算|概率|机会|以上)?(?=[^\d]|$)"),
)
_NEGATION_RE = re.compile(r"不是|不叫|不代表|并非|不得|不能|严禁|禁止|不构成|不等于|不要|不应|不宜|而非|不含|没有")
_PROB_ALLOWED_CONTEXT_RE = re.compile(r"历史条件频率|主观研判|脚本预填|研判模板|覆盖率|95% 区间|95%区间|Wilson|回测")
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]")

# 价格语境（比 validate_judgment 更宽）：交易动作或价位词之后 4 个字符内的数字，以及紧跟交易动作的数字
_LINT_PRICE_AFTER_RE = re.compile(
    r"(?:目标位?|失效位?|止损位?|止盈位?|触发位?|破位价|支撑位?|压力位?|阻力位?|现价|收盘价?|POC|VAH|VAL|TP1|TP2|入场区?|买入|卖出|"
    r"建仓|加仓|减仓|挂单|低吸|抄底|回踩|突破|跌破|站上|站稳|站回|收回|上破|下破|看至|看到|上看|下看|关注|观察位?|参考位?|价位)"
    r"[^\d\n。；，,、（）()|]{0,4}?(\d+(?:\.\d+)?)"
)
_LINT_PRICE_BEFORE_RE = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:元|港元|美元|港币)?\s*(?:附近|一线|上方|下方|左右|处|之上|之下|上下)?\s*"
    r"(?:买入|卖出|建仓|加仓|减仓|止损|止盈|挂单|入场|抄底|离场|低吸)"
)
VETO_HEAD_CHARS = 400


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
        price_hits = list(_CONTEXT_PRICE_RE.finditer(text))
        if price_hits:
            errors.append("quality.status == UNAVAILABLE 时，严禁在回复中输出任何具体价格点位或交易计划")
        return errors

    # 2. PARTIAL 告警置顶检查
    if q_status == "PARTIAL":
        head = text[:500]
        if not any(tok in head for tok in ("PARTIAL", "数据说明", "告警", "缺少大盘", "注意")):
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

    # 4. 禁用术语检查
    for pat in _FORBIDDEN_PROB_PHRASES:
        # 「规则分不是胜率或上涨概率」是合规的否定说明，不算违规
        m = next((hit for hit in pat.finditer(text) if not _NEGATION_RE.search(hit.group(0))), None)
        if m:
            errors.append(f"检测到违规表述: {m.group(0)!r}（禁止将规则分称为胜率/概率/置信度，禁止将成交分布称为真实持仓筹码）")
    for hit in dict.fromkeys(_unvalidated_probability_hits(text)):
        errors.append(
            f"检测到未经校验的概率/确定性表述: {hit!r}（只允许引用历史条件频率、通过校验的主观研判、带 95% 区间的回测胜率与区间覆盖率）"
        )

    # 5. 价格白名单检查（针对带价格语境的数值）
    pack = report.get("judgment_pack") or build_judgment_pack(report)
    whitelist = pack["price_whitelist"]
    wl_prices: list[float] = [float(p) for p in whitelist.get("prices") or []]
    wl_tol = float(whitelist.get("tolerance") or 0.01)

    cleaned = text
    for pat in _NON_PRICE_PATTERNS:
        cleaned = pat.sub(" ", cleaned)
    seen_spans: set[tuple[int, int]] = set()
    for pat in (_CONTEXT_PRICE_RE, _LINT_PRICE_AFTER_RE, _LINT_PRICE_BEFORE_RE):
        for m in pat.finditer(cleaned):
            span = m.span(1)
            if span in seen_spans:
                continue
            seen_spans.add(span)
            cand = float(m.group(1))
            msg = f"回复中出现了不在 result.json 白名单中的价格: {m.group(0)!r} ({cand})"
            if not _price_in_whitelist(cand, wl_prices, wl_tol) and msg not in errors:
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
