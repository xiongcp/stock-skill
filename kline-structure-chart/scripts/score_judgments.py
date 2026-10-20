#!/usr/bin/env python3
"""对 judgment_log.jsonl 中已到期的 L3 主观研判进行真实行情回放打分（O3-34）。

输出指标（见 OPTIMIZATION_PLAN.md 第 3.3 节与 O3-34）：
- 已到期研判数 matured_judgments 与未到期研判数 pending_judgments（严格零前视：未满 horizon_bars 不参与打分）
- LLM 主观研判概率的 Brier 分数（llm_brier_score）与对数损失（llm_log_loss）
- L2 基准概率的 Brier 分数（baseline_brier_score）与对数损失（baseline_log_loss）
- LLM 相对 L2 基准的 Brier 技能分 BSS = 1 - Brier_LLM / Brier_L2
- 5 档可靠性分桶表（reliability_buckets）与评估结论（verdict_cn）
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from base_rates import SCENARIO_BARRIER_ATR, _get_or_build_causal_features, label_scenario_event
from judgment import AUTO_INIT_GENERATOR

MIN_MATURED_FOR_VERDICT = 30
_EPS = 1e-6


def _clip_prob(p: float) -> float:
    return max(_EPS, min(1.0 - _EPS, float(p)))


def _binary_log_loss(p: float, y: int) -> float:
    pc = _clip_prob(p)
    return -(y * math.log(pc) + (1.0 - y) * math.log(1.0 - pc))


def _determine_realized_outcomes(
    bars: list[dict[str, Any]],
    cut_idx: int,
    horizon_bars: int,
    scenarios: list[dict[str, Any]],
) -> tuple[str, dict[str, int]]:
    """在真实未来窗口 [cut_idx+1 .. cut_idx+horizon_bars] 上判定各情景是否触及目标及胜出主情景。"""
    feat = _get_or_build_causal_features(bars[: cut_idx + 1 + horizon_bars])
    event_hits: dict[str, int] = {}
    outcomes: dict[str, str] = {}
    best_sid: str | None = None
    best_key: tuple[float, int] = (-1.0, -(horizon_bars + 999))

    for sc in scenarios:
        sid = str(sc["scenario_id"])
        direction = str(sc.get("direction") or "BULLISH")
        res = label_scenario_event(feat, cut_idx, sid, horizon_bars=horizon_bars, direction=direction)
        outcomes[sid] = str(res["outcome"])
        hit = 1 if res["outcome"] == "HIT_TARGET" else 0
        event_hits[sid] = hit
        if hit == 1:
            tgt_mult = float(SCENARIO_BARRIER_ATR.get(sid, (1.35, 1.10))[0])
            cand_key = (tgt_mult, -int(res["bars_used"]))
            if cand_key > best_key:
                best_key = cand_key
                best_sid = sid

    if best_sid is None and scenarios:
        c0 = float(bars[cut_idx]["close"])
        c_end = float(bars[cut_idx + horizon_bars]["close"])
        ret = (c_end - c0) / max(c0, 1e-9)
        atr_p = feat["atr_pct"][cut_idx]
        if abs(ret) <= 0.8 * atr_p:
            target_dir = "NEUTRAL"
        elif ret > 0:
            target_dir = "BULLISH"
        else:
            target_dir = "BEARISH"
        matched = [
            str(s["scenario_id"])
            for s in scenarios
            if s.get("direction") == target_dir and outcomes.get(str(s["scenario_id"])) != "HIT_INVALIDATION"
        ]
        best_sid = matched[0] if matched else "NONE"

    winner_id = best_sid or "NONE"
    return winner_id, event_hits


def score_judgment_entries(
    entries: list[dict[str, Any]],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """对研判日志条目列表按已收盘真实 K 线打分。"""
    matured_details: list[dict[str, Any]] = []
    pending_ids: list[str] = []

    llm_sq_errors: list[float] = []
    base_sq_errors: list[float] = []
    llm_log_losses: list[float] = []
    base_log_losses: list[float] = []

    # (p_llm, y_win) 对，用于可靠性分桶
    pred_outcome_pairs: list[tuple[float, int]] = []

    template_excluded = 0
    for entry in entries:
        if entry.get("origin") == AUTO_INIT_GENERATOR:
            # 未经 Agent 改写的 --auto-init 预填模板不是 LLM 研判，不计入 LLM 技能分
            template_excluded += 1
            continue
        sym = str(entry.get("symbol") or "")
        as_of = str(entry.get("as_of") or "")
        horizon = int(entry.get("horizon_bars") or 15)
        jid = str(entry.get("judgment_id") or f"{sym}_{as_of}")
        bars = bars_by_symbol.get(sym) or []
        if not bars:
            pending_ids.append(jid)
            continue
        day_to_idx = {str(b["day"]): i for i, b in enumerate(bars)}
        if as_of not in day_to_idx:
            pending_ids.append(jid)
            continue
        cut_idx = day_to_idx[as_of]
        if cut_idx + horizon >= len(bars):
            # 未来窗口尚未完全收盘成熟，严格零前视跳过
            pending_ids.append(jid)
            continue

        scenarios = entry.get("scenarios") or []
        if not scenarios:
            continue
        winner_id, event_hits = _determine_realized_outcomes(bars, cut_idx, horizon, scenarios)

        entry_llm_brier = 0.0
        entry_base_brier = 0.0
        for sc in scenarios:
            sid = str(sc["scenario_id"])
            p_llm = float(sc["subjective_probability_pct"]) / 100.0
            p_base = float(sc["baseline_probability_pct"]) / 100.0
            # 与互斥概率空间 (sum p_k = 1.0) 严格对齐：单次截面最多一个 realized winner_id 记 y_win = 1
            y_win = 1 if sid == winner_id else 0

            sq_llm = (p_llm - y_win) ** 2
            sq_base = (p_base - y_win) ** 2
            llm_sq_errors.append(sq_llm)
            base_sq_errors.append(sq_base)
            entry_llm_brier += sq_llm
            entry_base_brier += sq_base

            llm_log_losses.append(_binary_log_loss(p_llm, y_win))
            base_log_losses.append(_binary_log_loss(p_base, y_win))
            pred_outcome_pairs.append((p_llm, y_win))

        k_cnt = max(1, len(scenarios))
        matured_details.append(
            {
                "judgment_id": jid,
                "symbol": sym,
                "as_of": as_of,
                "matured_on": str(bars[cut_idx + horizon]["day"]),
                "primary_scenario_id": entry.get("primary_scenario_id"),
                "realized_winner_scenario_id": winner_id,
                "primary_hit": bool(
                    entry.get("primary_scenario_id") == winner_id
                    or event_hits.get(str(entry.get("primary_scenario_id") or ""), 0) == 1
                ),
                "event_hits": event_hits,
                "llm_brier": round(entry_llm_brier / k_cnt, 6),
                "baseline_brier": round(entry_base_brier / k_cnt, 6),
            }
        )

    matured_n = len(matured_details)
    eval_pairs_n = len(llm_sq_errors)
    if eval_pairs_n == 0:
        return {
            "matured_judgments": 0,
            "pending_judgments": len(pending_ids),
            "template_judgments_excluded": template_excluded,
            "evaluated_scenario_pairs": 0,
            "llm_brier_score": None,
            "baseline_brier_score": None,
            "brier_skill_score": None,
            "llm_log_loss": None,
            "baseline_log_loss": None,
            "reliability_buckets": [],
            "tighten_cap_recommended": False,
            "verdict_cn": "尚无已到期的研判记录（matured = 0）",
            "details": [],
        }

    llm_brier = sum(llm_sq_errors) / eval_pairs_n
    base_brier = sum(base_sq_errors) / eval_pairs_n
    bss = 1.0 - (llm_brier / base_brier) if base_brier > 1e-12 else 0.0
    llm_ll = sum(llm_log_losses) / eval_pairs_n
    base_ll = sum(base_log_losses) / eval_pairs_n

    bucket_edges = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    bucket_labels = ["0-20%", "20-40%", "40-60%", "60-80%", "80-100%"]
    buckets: list[dict[str, Any]] = []
    for (lo, hi), label in zip(bucket_edges, bucket_labels):
        in_b = [(p, y) for p, y in pred_outcome_pairs if lo <= p < hi]
        cnt = len(in_b)
        buckets.append(
            {
                "bucket": label,
                "count": cnt,
                "mean_predicted_prob": round(sum(p for p, _ in in_b) / cnt, 4) if cnt else None,
                "empirical_hit_rate": round(sum(y for _, y in in_b) / cnt, 4) if cnt else None,
            }
        )

    tighten = bool(matured_n >= MIN_MATURED_FOR_VERDICT and bss <= 0.0)
    if matured_n < MIN_MATURED_FOR_VERDICT:
        verdict = (
            f"样本积累期（n = {matured_n} < {MIN_MATURED_FOR_VERDICT}）："
            f"当前 LLM Brier = {llm_brier:.4f}，L2 基准 Brier = {base_brier:.4f}，技能分 BSS = {bss:+.4f}"
        )
    elif bss > 0.0:
        verdict = f"LLM 调整相对历史频率的技能分为 {bss:+.4f}（n = {matured_n}）"
    else:
        verdict = f"LLM 调整尚未显示增益（BSS = {bss:+.4f} ≤ 0，n = {matured_n}，协议建议收紧调整上限）"

    return {
        "matured_judgments": matured_n,
        "pending_judgments": len(pending_ids),
        "template_judgments_excluded": template_excluded,
        "evaluated_scenario_pairs": eval_pairs_n,
        "llm_brier_score": round(llm_brier, 6),
        "baseline_brier_score": round(base_brier, 6),
        "brier_skill_score": round(bss, 6),
        "llm_log_loss": round(llm_ll, 6),
        "baseline_log_loss": round(base_ll, 6),
        "reliability_buckets": buckets,
        "tighten_cap_recommended": tighten,
        "verdict_cn": verdict,
        "details": matured_details,
    }


def load_judgment_log(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def _read_json_any(path: Path) -> Any:
    if path.suffix == ".gz":
        import gzip

        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(path.read_text(encoding="utf-8"))


def _bars_map_from_payload(
    raw: Any,
    entries: list[dict[str, Any]],
    symbol_hint: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """解析 --bars-file：支持 bundle、单标的缓存字典、symbol->bars 映射与裸 K 线数组。"""
    if isinstance(raw, list):
        sym = symbol_hint or (entries[0]["symbol"] if entries else "UNKNOWN")
        return {str(sym): raw}
    if not isinstance(raw, dict):
        return {}
    if "target" in raw and isinstance(raw["target"], dict) and "bars" in raw["target"]:
        sym = symbol_hint or raw["target"].get("instrument", {}).get("symbol") or "UNKNOWN"
        return {str(sym): raw["target"]["bars"]}
    if isinstance(raw.get("bars"), list):
        # 单标的缓存字典：{"symbol": "601288.SH", "bars": [...], "events": [...]}
        sym = symbol_hint or raw.get("symbol") or (entries[0]["symbol"] if entries else "UNKNOWN")
        return {str(sym): raw["bars"]}
    return {
        str(k): v for k, v in raw.items()
        if isinstance(v, list) and (not v or (isinstance(v[0], dict) and "day" in v[0]))
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score matured L3 LLM judgments in judgment_log.jsonl")
    parser.add_argument("--log", required=True, help="Path to judgment_log.jsonl")
    parser.add_argument("--bars-file", default=None, help="Optional .json/.json.gz with bars for offline scoring")
    parser.add_argument("--cache-dir", default=None, help="Optional real-bar cache dir: <dir>/<market>/<symbol>.json.gz")
    parser.add_argument("--symbol", default=None, help="Symbol when --bars-file is a single bar array")
    parser.add_argument("--output", default=None, help="Optional path to write score summary JSON")
    args = parser.parse_args()

    log_path = Path(args.log)
    if not log_path.exists():
        raise SystemExit(f"[INVALID_ARGUMENT] log file not found: {log_path}")
    entries = load_judgment_log(log_path)

    bars_by_symbol: dict[str, list[dict[str, Any]]] = {}
    if args.bars_file:
        bars_by_symbol = _bars_map_from_payload(_read_json_any(Path(args.bars_file)), entries, args.symbol)
    elif args.cache_dir:
        cache_dir = Path(args.cache_dir)
        for entry in entries:
            sym = str(entry.get("symbol") or "")
            market = str(entry.get("market") or "")
            if not sym:
                continue
            candidates = [cache_dir / market / f"{sym}.json.gz", cache_dir / f"{sym}.json.gz"]
            found = next((p for p in candidates if p.exists()), None)
            if found:
                bars_by_symbol[sym] = _bars_map_from_payload(_read_json_any(found), [entry], sym).get(sym, [])
    else:
        from data_gateway import load_bundle

        symbols = sorted({str(e["symbol"]) for e in entries if e.get("symbol")})
        for sym in symbols:
            market = next((str(e.get("market")) for e in entries if e.get("symbol") == sym and e.get("market")), None)
            req: dict[str, Any] = {"symbol": sym}
            if market:
                req["market"] = market
            bundle = load_bundle(req, allow_network=True)  # type: ignore[arg-type]
            bars_by_symbol[sym] = bundle["target"]["bars"]

    result = score_judgment_entries(entries, bars_by_symbol)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
