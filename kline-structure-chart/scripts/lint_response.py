#!/usr/bin/env python3
"""Agent 最终回复文本校验器：检查回复是否遵守 SKILL.md 回复契约与数据真实性铁律。

校验项：
1. quality.status == UNAVAILABLE 时，禁止输出任何价格点位或交易计划。
2. quality.status == PARTIAL 时，必须在回复前部披露数据告警。
3. debate_and_veto.risk_officer_veto == true 时，必须出现「风控否决」提示。
4. 严禁把规则分（rule_score）称为「胜率」「上涨概率」或「置信度」。
5. 回复中出现的具体价格数字必须能在 result.json 白名单中找到，严禁捏造价格。
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

    # 4. 禁用术语检查
    for pat in _FORBIDDEN_PROB_PHRASES:
        m = pat.search(text)
        if m:
            errors.append(f"检测到违规表述: {m.group(0)!r}（禁止将规则分称为胜率/概率/置信度，禁止将成交分布称为真实持仓筹码）")

    # 5. 价格白名单检查（针对带价格语境的数值）
    pack = report.get("judgment_pack") or build_judgment_pack(report)
    whitelist = pack["price_whitelist"]
    wl_prices: list[float] = [float(p) for p in whitelist.get("prices") or []]
    wl_tol = float(whitelist.get("tolerance") or 0.01)

    cleaned = text
    for pat in _NON_PRICE_PATTERNS:
        cleaned = pat.sub(" ", cleaned)
    for m in _CONTEXT_PRICE_RE.finditer(cleaned):
        cand = float(m.group(1))
        if not _price_in_whitelist(cand, wl_prices, wl_tol):
            errors.append(f"回复中出现了不在 result.json 白名单中的价格: {m.group(0)!r} ({cand})")

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
