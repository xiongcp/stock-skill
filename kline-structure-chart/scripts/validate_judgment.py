#!/usr/bin/env python3
"""校验 L3 受约束的 LLM 研判产物 (*_judgment.json)，并在通过后记入 judgment_log.jsonl 及合入报告。

校验规则（见 OPTIMIZATION_PLAN.md 第 3.3 节与 O3-32）：
1. JSON 结构完整，symbol / as_of 与 result.json 一致。
2. 必须包含「主观研判」标注（judgment_type == "主观研判" 且 summary_cn 含「主观研判」）。
3. 互斥情景覆盖与 judgment_pack.scenario_anchors 一致，且 subjective_probability_pct 之和等于 100%。
4. 每个情景相对 baseline_probability_pct 的调整幅度不超过上限（n >= 30 最多 ±15 个百分点；n < 30 最多 ±10 个百分点）。
5. 引用的所有证据编号（E1…En）与冲突编号（C1…Cm）必须在 judgment_pack 中真实存在。
6. 关键观察价位及正文中出现的每个价格数字都必须落在 result.json 的价格白名单内，严禁捏造价格。
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contracts import compute_canonical_hash
from judgment import build_judgment_pack, generate_constrained_judgment, judgment_origin

_EVIDENCE_ID_RE = re.compile(r"\bE\d+\b")
_CONFLICT_ID_RE = re.compile(r"\bC\d+\b")

# 剔除非价格数字的模式：日期、证据编号、指标参数名、百分比、百分点、样本量、天数/根数/手数/股数/倍数/ATR/分数/基点
_NON_PRICE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b(?:RSI|ATR|ADX|RVOL|BBW|HV|IC|BSS|Brier)\d*\s*(?:\(\s*\d+\s*\))?\s*(?:[=:：约为达]|处于|位于)\s*[+-]?\d+(?:\.\d+)?", re.I),
    re.compile(r"\b(?:E|C|MA|EMA|SMA|RSI|ATR|ADX|RVOL|TP|Wave|W)\d+\b", re.I),
    re.compile(r"\b(?:Supertrend|MACD)\s*\(\s*\d+(?:\s*,\s*\d+)*\s*\)", re.I),
    re.compile(r"\b(?:RSI|ATR|ADX|RVOL|MA|EMA|SMA)\s*\(\s*\d+\s*\)", re.I),
    re.compile(r"[+-]?\d+(?:\.\d+)?\s*%"),
    re.compile(r"[+-]?\d+(?:\.\d+)?\s*个?百分点"),
    re.compile(r"\bn\s*=\s*\d+\b", re.I),
    # 中文单位后面通常紧跟汉字（\b 在两个汉字之间不成立），用前后断言代替 \b
    re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*(?:个?交易日|日|天|根|手|股|笔|次|浪|分|bps|倍)(?![A-Za-z0-9])"),
    re.compile(r"\b\d+(?:\.\d+)?\s*个\s*ATR\b", re.I),
    re.compile(r"\b\d+(?:\.\d+)?\s*[×xX]\b"),
    re.compile(r"[+-]?\d+(?:\.\d+)?\s*R\b"),
    re.compile(r"±\s*\d+(?:\.\d+)?"),
)
_DECIMAL_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.-])\d+\.\d+(?![A-Za-z0-9_.-])")
_CONTEXT_PRICE_RE = re.compile(
    r"(?:目标位|失效位|止损位|止损|触发位|破位价|支撑位?|压力位?|现价|收盘价?|POC|VAH|VAL|TP1|TP2|入场区)\s*[为=:：约至]?\s*(\d+(?:\.\d+)?)"
)


def _extract_candidate_prices_from_text(text: str) -> list[float]:
    """从中文研判文本中提取代表价格的数值（先剥离日期、百分比、指标参数、样本量等非价格数字）。"""
    cleaned = text
    for pat in _NON_PRICE_PATTERNS:
        cleaned = pat.sub(" ", cleaned)
    candidates: list[float] = []
    for m in _CONTEXT_PRICE_RE.finditer(cleaned):
        candidates.append(float(m.group(1)))
    for m in _DECIMAL_NUMBER_RE.finditer(cleaned):
        candidates.append(float(m.group(0)))
    return candidates


def _price_in_whitelist(val: float, whitelist_prices: list[float], tolerance: float) -> bool:
    return any(abs(val - p) <= tolerance + 1e-6 for p in whitelist_prices)


def validate_judgment_data(
    judgment: dict[str, Any],
    report: dict[str, Any],
    pack: dict[str, Any] | None = None,
) -> list[str]:
    """校验 judgment 字典，返回错误列表（空列表表示校验通过）。"""
    errors: list[str] = []
    if not isinstance(judgment, dict):
        return ["judgment 必须是 JSON 对象"]

    jpack = pack or report.get("judgment_pack") or build_judgment_pack(report)
    required_top = (
        "symbol",
        "as_of",
        "judgment_type",
        "summary_cn",
        "primary_scenario_id",
        "alternate_scenario_id",
        "scenario_judgments",
        "key_levels_to_watch",
        "falsification_condition",
    )
    for key in required_top:
        if key not in judgment:
            errors.append(f"缺少顶层必填字段: {key}")
    if errors:
        return errors

    if judgment.get("symbol") != jpack["symbol"]:
        errors.append(f"symbol 不匹配: judgment={judgment.get('symbol')!r} vs report={jpack['symbol']!r}")
    if judgment.get("as_of") != jpack["as_of"]:
        errors.append(f"as_of 不匹配: judgment={judgment.get('as_of')!r} vs report={jpack['as_of']!r}")

    # 1. 必须包含「主观研判」标注
    j_type = str(judgment.get("judgment_type") or "")
    summary_cn = str(judgment.get("summary_cn") or "").strip()
    if "主观研判" not in j_type or "主观研判" not in summary_cn:
        errors.append("缺少「主观研判」明确标注（judgment_type 与 summary_cn 均须包含「主观研判」）")

    valid_eids = {e["id"] for e in jpack.get("evidence_items") or []}
    valid_cids = {c["id"] for c in jpack.get("conflicting_signals") or []}
    anchors = {a["scenario_id"]: a for a in jpack.get("scenario_anchors") or []}
    whitelist = jpack["price_whitelist"]
    wl_prices: list[float] = [float(p) for p in whitelist.get("prices") or []]
    wl_tol = float(whitelist.get("tolerance") or 0.01)

    # 2. 校验 scenario_judgments
    scen_list = judgment.get("scenario_judgments")
    if not isinstance(scen_list, list) or not scen_list:
        errors.append("scenario_judgments 必须是非空数组")
        return errors

    seen_sids: list[str] = []
    prob_sum = 0.0
    for idx, item in enumerate(scen_list):
        if not isinstance(item, dict):
            errors.append(f"scenario_judgments[{idx}] 必须是对象")
            continue
        sid = str(item.get("scenario_id") or "")
        if sid not in anchors:
            errors.append(f"scenario_judgments[{idx}].scenario_id={sid!r} 不在 judgment_pack.scenario_anchors 中")
            continue
        if sid in seen_sids:
            errors.append(f"scenario_judgments 中存在重复的 scenario_id={sid!r}")
        seen_sids.append(sid)
        anchor = anchors[sid]

        try:
            base_p = float(item["baseline_probability_pct"])
            subj_p = float(item["subjective_probability_pct"])
            adj_p = float(item["adjustment_pct"])
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"scenario_judgments[{idx}] ({sid}) 缺少概率数值字段: {exc}")
            continue

        if not (0.0 <= subj_p <= 100.0):
            errors.append(f"情景 {sid} 的 subjective_probability_pct={subj_p} 超出 [0, 100]")
        prob_sum += subj_p

        expected_base = float(anchor["baseline_probability_pct"])
        if abs(base_p - expected_base) > 0.05:
            errors.append(
                f"情景 {sid} 的 baseline_probability_pct={base_p:.2f}% 与锚点 {expected_base:.2f}% 不一致"
            )
        expected_adj = subj_p - expected_base
        if abs(adj_p - expected_adj) > 0.05:
            errors.append(
                f"情景 {sid} 的 adjustment_pct={adj_p:+.2f}% 与 subjective - baseline ({expected_adj:+.2f}%) 不一致"
            )

        max_adj = float(anchor["max_adjustment_pct"])
        if abs(expected_adj) > max_adj + 1e-6:
            errors.append(
                f"情景 {sid} 调整幅度 {expected_adj:+.2f} 个百分点超过上限 ±{max_adj:.0f} 个百分点（样本 n={anchor['sample_size_n']}）"
            )

        sup_ids = item.get("supporting_evidence_ids") or []
        opp_ids = item.get("opposing_evidence_ids") or []
        if not isinstance(sup_ids, list) or not isinstance(opp_ids, list):
            errors.append(f"情景 {sid} 的 supporting_evidence_ids / opposing_evidence_ids 必须是列表")
            sup_ids, opp_ids = [], []
        if not sup_ids and not opp_ids:
            errors.append(f"情景 {sid} 未列出任何支持或反对证据编号")
        for eid in [*sup_ids, *opp_ids]:
            if eid not in valid_eids:
                errors.append(f"情景 {sid} 引用了不存在的证据编号: {eid!r}")

        rationale = str(item.get("rationale_cn") or "").strip()
        if not rationale:
            errors.append(f"情景 {sid} 的 rationale_cn 不能为空")
        else:
            cited_in_text = _EVIDENCE_ID_RE.findall(rationale)
            if not cited_in_text:
                errors.append(f"情景 {sid} 的 rationale_cn 未引用任何证据编号（如 E1）")
            for eid in cited_in_text:
                if eid not in valid_eids:
                    errors.append(f"情景 {sid} 的 rationale_cn 引用了不存在的证据编号: {eid!r}")
            for cand_price in _extract_candidate_prices_from_text(rationale):
                if not _price_in_whitelist(cand_price, wl_prices, wl_tol):
                    errors.append(f"情景 {sid} 的 rationale_cn 出现了不在 result.json 白名单中的价格: {cand_price}")

        for pk in ("target_price", "invalidation_price"):
            pval = item.get(pk)
            if pval is not None:
                try:
                    pf = float(pval)
                    if not _price_in_whitelist(pf, wl_prices, wl_tol):
                        errors.append(f"情景 {sid} 的 {pk}={pf} 不在 result.json 价格白名单中")
                except (TypeError, ValueError):
                    errors.append(f"情景 {sid} 的 {pk}={pval!r} 不是合法价格数值")

    if set(seen_sids) != set(anchors.keys()):
        missing = sorted(set(anchors.keys()) - set(seen_sids))
        if missing:
            errors.append(f"scenario_judgments 遗漏了锚点情景: {missing}")

    if abs(prob_sum - 100.0) > 0.05:
        errors.append(f"所有互斥情景的 subjective_probability_pct 之和为 {prob_sum:.2f}%，必须等于 100.0%")

    # 3. 校验主情景与备选情景
    prim_id = str(judgment.get("primary_scenario_id") or "")
    alt_id = str(judgment.get("alternate_scenario_id") or "")
    if prim_id not in seen_sids:
        errors.append(f"primary_scenario_id={prim_id!r} 不在 scenario_judgments 中")
    if alt_id not in seen_sids:
        errors.append(f"alternate_scenario_id={alt_id!r} 不在 scenario_judgments 中")
    if len(seen_sids) > 1 and prim_id == alt_id:
        errors.append("当存在多个情景时，primary_scenario_id 与 alternate_scenario_id 不得相同")

    # 4. 校验冲突编号引用
    for cid in judgment.get("conflicting_signals_reviewed") or []:
        if cid not in valid_cids:
            errors.append(f"conflicting_signals_reviewed 引用了不存在的冲突编号: {cid!r}")

    # 5. 校验关键观察价位与全文价格白名单
    key_levels = judgment.get("key_levels_to_watch")
    if not isinstance(key_levels, list) or not key_levels:
        errors.append("key_levels_to_watch 必须是非空列表")
    else:
        for idx, kl in enumerate(key_levels):
            if not isinstance(kl, dict) or "price" not in kl or "label" not in kl:
                errors.append(f"key_levels_to_watch[{idx}] 必须包含 label 与 price")
                continue
            try:
                p_val = float(kl["price"])
            except (TypeError, ValueError):
                errors.append(f"key_levels_to_watch[{idx}].price={kl.get('price')!r} 不是合法数字")
                continue
            if not _price_in_whitelist(p_val, wl_prices, wl_tol):
                errors.append(f"key_levels_to_watch[{idx}] ({kl['label']}) 的价格 {p_val} 不在 result.json 价格白名单中")

    falsification = str(judgment.get("falsification_condition") or "").strip()
    if not falsification:
        errors.append("falsification_condition（什么情况说明判断错了）不能为空")

    for field_name, text_val in (("summary_cn", summary_cn), ("falsification_condition", falsification)):
        for eid in _EVIDENCE_ID_RE.findall(text_val):
            if eid not in valid_eids:
                errors.append(f"{field_name} 引用了不存在的证据编号: {eid!r}")
        for cid in _CONFLICT_ID_RE.findall(text_val):
            if cid not in valid_cids:
                errors.append(f"{field_name} 引用了不存在的冲突编号: {cid!r}")
        for cand_price in _extract_candidate_prices_from_text(text_val):
            if not _price_in_whitelist(cand_price, wl_prices, wl_tol):
                errors.append(f"{field_name} 出现了不在 result.json 白名单中的价格: {cand_price}")

    return errors


def build_judgment_log_entry(judgment: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """构造写入 judgment_log.jsonl 的单条日志记录（供到期后 score_judgments.py 打分）。"""
    pack = report.get("judgment_pack") or build_judgment_pack(report)
    anchors_map = {a["scenario_id"]: a for a in pack.get("scenario_anchors") or []}
    bars = (report.get("series_export") or {}).get("bars") or []
    base_close = float(bars[-1]["close"]) if bars else float(report["volume_profile"]["poc_price"])

    scenarios_logged: list[dict[str, Any]] = []
    for sj in judgment.get("scenario_judgments") or []:
        sid = sj["scenario_id"]
        anc = anchors_map.get(sid, {})
        scenarios_logged.append(
            {
                "scenario_id": sid,
                "scenario_name": sj["scenario_name"],
                "direction": sj.get("direction") or anc.get("direction"),
                "sample_size_n": anc.get("sample_size_n", sj.get("sample_size_n", 0)),
                "l2_effective_hit_target_freq": anc.get("l2_effective_hit_target_freq"),
                "baseline_probability_pct": sj["baseline_probability_pct"],
                "subjective_probability_pct": sj["subjective_probability_pct"],
                "adjustment_pct": sj["adjustment_pct"],
                "target_price": anc.get("target_price", sj.get("target_price")),
                "invalidation_price": anc.get("invalidation_price", sj.get("invalidation_price")),
                "supporting_evidence_ids": sj.get("supporting_evidence_ids") or [],
                "opposing_evidence_ids": sj.get("opposing_evidence_ids") or [],
            }
        )
    entry_core = {
        "symbol": judgment["symbol"],
        "market": report["meta"]["market"],
        "as_of": judgment["as_of"],
        "horizon_bars": int(judgment.get("horizon_bars") or pack.get("horizon_bars") or 15),
        "base_close": base_close,
        "primary_scenario_id": judgment["primary_scenario_id"],
        "alternate_scenario_id": judgment["alternate_scenario_id"],
        "scenarios": scenarios_logged,
    }
    j_hash = compute_canonical_hash(entry_core)
    return {
        "judgment_id": f"{judgment['symbol']}_{judgment['as_of']}_{j_hash}",
        "logged_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # AUTO_INIT_TEMPLATE = 未经改写的 --auto-init 预填模板，score_judgments 不把它计入 LLM 技能分
        "origin": judgment_origin(judgment, report),
        **entry_core,
    }


def append_judgment_log(judgment: dict[str, Any], report: dict[str, Any], log_path: Path) -> dict[str, Any]:
    """将通过校验的研判追加写入 judgment_log.jsonl（按 judgment_id 幂等去重）。"""
    entry = build_judgment_log_entry(judgment, report)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    existing_ids: set[str] = set()
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("judgment_id"):
                    existing_ids.add(str(obj["judgment_id"]))
            except json.JSONDecodeError:
                continue
    if entry["judgment_id"] not in existing_ids:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate L3 LLM scenario judgment JSON against result.json")
    parser.add_argument("--result", required=True, help="Path to *_result.json")
    parser.add_argument("--judgment", required=True, help="Path to *_judgment.json")
    parser.add_argument("--pack", default=None, help="Optional path to *_judgment_pack.json")
    parser.add_argument("--log", default=None, help="Optional path to judgment_log.jsonl to append validated entry")
    parser.add_argument("--report-md", default=None, help="Optional path to *_report.md to re-render with judgment section")
    parser.add_argument(
        "--auto-init",
        action="store_true",
        help="If *_judgment.json does not exist yet, initialize it with a compliant evidence-anchored template before validating",
    )
    args = parser.parse_args()

    res_path = Path(args.result)
    jud_path = Path(args.judgment)
    if not res_path.exists():
        raise SystemExit(f"[INVALID_ARGUMENT] result file not found: {res_path}")
    if args.auto_init and not jud_path.exists():
        rep_tmp = json.loads(res_path.read_text(encoding="utf-8"))
        jud_init = generate_constrained_judgment(rep_tmp)
        jud_path.parent.mkdir(parents=True, exist_ok=True)
        jud_path.write_text(json.dumps(jud_init, ensure_ascii=False, indent=2), encoding="utf-8")
    if not jud_path.exists():
        raise SystemExit(f"[INVALID_ARGUMENT] judgment file not found: {jud_path}")

    try:
        report = json.loads(res_path.read_text(encoding="utf-8"))
        judgment = json.loads(jud_path.read_text(encoding="utf-8"))
        pack = json.loads(Path(args.pack).read_text(encoding="utf-8")) if args.pack else None
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"[INVALID_JUDGMENT] JSON parse error: {exc}") from exc

    errors = validate_judgment_data(judgment, report, pack=pack)
    if errors:
        joined = "；".join(errors)
        raise SystemExit(f"[INVALID_JUDGMENT] 研判校验未通过（共 {len(errors)} 项）：{joined}")

    log_path = Path(args.log) if args.log else (jud_path.parent / "judgment_log.jsonl")
    log_entry = append_judgment_log(judgment, report, log_path)
    if log_entry["origin"] == "AGENT":
        judgment.pop("generator", None)
        if "脚本预填" in str(judgment.get("disclaimer_cn") or ""):
            judgment["disclaimer_cn"] = (
                "本节为锚定 L2 历史条件频率的 Agent 受约束主观研判，已记入日志待到期按 Brier 分数与技能分检验，不构成投资建议。"
            )
        jud_path.write_text(json.dumps(judgment, ensure_ascii=False, indent=2), encoding="utf-8")

    # 将通过校验的研判写回 result.json 并同步更新 report.md
    report["llm_judgment"] = judgment
    if isinstance(report.get("rendered_paths"), dict):
        report["rendered_paths"]["judgment_json"] = str(jud_path.resolve())
    res_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 若存在同目录或指定的 *_report.md，则将通过校验的主观研判章节合入报告
    md_path = Path(args.report_md) if args.report_md else res_path.with_name(res_path.name.replace("_result.json", "_report.md"))
    if md_path.exists() or args.report_md:
        from render_report_md import render_report_markdown

        bt_path = res_path.with_name(res_path.name.replace("_result.json", "_backtest.json"))
        bt_payload = json.loads(bt_path.read_text(encoding="utf-8")) if bt_path.exists() else None
        md_path.write_text(render_report_markdown(report, bt_payload, judgment=judgment), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "VALID",
                "symbol": judgment["symbol"],
                "as_of": judgment["as_of"],
                "judgment_id": log_entry["judgment_id"],
                "origin": log_entry["origin"],
                "log_path": str(log_path.resolve()),
                "report_md_updated": str(md_path.resolve()) if md_path.exists() else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
