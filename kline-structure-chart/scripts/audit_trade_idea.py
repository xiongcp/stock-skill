#!/usr/bin/env python3
"""交易想法 If-Then 机械化双向风控审计器 (Sub-project 3)。

输入 `*_result.json` 与用户的交易计划（入场价、止损价、目标价、方向、股数），
输出 6 维客观量化风控审计（大盘/否决冲突、追高延伸度、整数关口扫损陷阱、
全口径双边摩擦净盈亏比、15日保形区间可达性、If-Then 机械化执行剧本），
并支持将审计记录追加落盘至 JSONL 账本供后续客观对账。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from market_rules import compute_round_trip_cost_detail, round_to_tick  # noqa: E402


def _is_round_number_price(price: float, market: str) -> bool:
    """判断止损价是否落在散户密集挂单的整数关口或半整数关口。"""
    if price <= 0:
        return False
    if price >= 500.0:
        return abs(price - round(price / 10.0) * 10.0) < 1e-6 or abs(price - round(price / 5.0) * 5.0) < 1e-6
    if price >= 50.0:
        return abs(price - round(price)) < 1e-6
    if price >= 5.0:
        return abs(price * 2.0 - round(price * 2.0)) < 1e-6
    return abs(price * 10.0 - round(price * 10.0)) < 1e-6


def audit_trade_idea(
    result: dict[str, Any],
    *,
    direction: str = "LONG",
    entry: float,
    stop: float,
    target: float,
    shares: int | None = None,
    note: str = "",
) -> dict[str, Any]:
    """对用户主观交易想法执行 6 维机械化双向审计。"""
    direction_u = str(direction or "LONG").upper()
    if direction_u not in ("LONG", "SHORT"):
        raise ValueError(f"direction 必须为 LONG 或 SHORT，收到: {direction!r}")
    entry_f, stop_f, target_f = float(entry), float(stop), float(target)
    if direction_u == "LONG" and not (stop_f < entry_f < target_f):
        raise ValueError(f"LONG 方向须满足 stop({stop_f}) < entry({entry_f}) < target({target_f})")
    if direction_u == "SHORT" and not (target_f < entry_f < stop_f):
        raise ValueError(f"SHORT 方向须满足 target({target_f}) < entry({entry_f}) < stop({stop_f})")

    meta = result.get("meta") or {}
    verdict = result.get("verdict") or {}
    plan = result.get("plan") or {}
    ind = result.get("indicators") or {}
    structure = result.get("structure") or {}
    forecast = result.get("forecast") or {}

    symbol = str(meta.get("symbol") or "")
    market = str(meta.get("market") or "CN")
    asset_type = str(meta.get("asset_type") or "equity")
    as_of = str(meta.get("as_of") or "")
    last_close = float(structure.get("last_close") or meta.get("last_close") or entry_f)
    p_dec = int(meta.get("price_decimals") or 2)

    trend_ind = ind.get("trend") or {}
    vol_ind = ind.get("volatility") or {}
    ema20 = float(trend_ind.get("ema20") or last_close)
    atr14 = float(vol_ind.get("atr14") or max(0.01, abs(entry_f - stop_f)))

    rules = plan.get("market_microstructure") or {"market": market, "commission_bps": 2.5, "stamp_duty_buy_bps": 0.0, "stamp_duty_sell_bps": 5.0, "lot_size": 100}
    lot_size = int(rules.get("lot_size") or 100)
    eff_shares = int(shares) if (shares and int(shares) > 0) else lot_size

    # 1. 大盘环境与风控否决门禁检查
    risk_veto = bool(verdict.get("risk_veto", False))
    veto_reasons = list(verdict.get("veto_reasons") or [])
    if direction_u == "LONG" and risk_veto:
        regime_check = {
            "status": "FAIL",
            "risk_veto": True,
            "veto_reasons": veto_reasons,
            "note_cn": f"系统风控官已否决做多（{'；'.join(veto_reasons)}），强行逆势开仓违背大盘先行与风控铁律",
        }
    else:
        regime_check = {
            "status": "PASS",
            "risk_veto": risk_veto,
            "veto_reasons": veto_reasons,
            "note_cn": "大盘与个股风控门禁未触发否决冲突",
        }

    # 2. 追高 / 延伸度审计
    ext_atr = (entry_f - ema20) / max(1e-6, atr14) if direction_u == "LONG" else (ema20 - entry_f) / max(1e-6, atr14)
    sys_entry_zone = list(plan.get("entry_zone") or [round(last_close - 0.5 * atr14, p_dec), round(last_close, p_dec)])
    if ext_atr > 1.80:
        anti_chase = {
            "status": "FAIL" if ext_atr > 2.20 else "WARN",
            "extension_from_ema20_atr": round(ext_atr, 2),
            "system_entry_zone": sys_entry_zone,
            "note_cn": (
                f"入场价 {entry_f:.{p_dec}f} 偏离 EMA20({ema20:.{p_dec}f}) 达 {ext_atr:.2f} 倍 ATR14，"
                f"属于高位追涨区间；建议等待缩量回踩 {sys_entry_zone[0]:.{p_dec}f}~{sys_entry_zone[1]:.{p_dec}f} 企稳或放量突破回踩确认后再执行"
            ),
        }
    else:
        anti_chase = {
            "status": "PASS",
            "extension_from_ema20_atr": round(ext_atr, 2),
            "system_entry_zone": sys_entry_zone,
            "note_cn": f"入场价偏离 EMA20 约 {ext_atr:.2f} ATR，未触发追高过热阈值",
        }

    # 3. 整数关口扫损陷阱审计
    is_round = _is_round_number_price(stop_f, market)
    sys_stop = float(plan.get("stop_loss") or round_to_tick(entry_f - 1.5 * atr14, market, asset_type, as_of))
    supports = [float(s["price"]) for s in ((structure.get("levels") or {}).get("supports") or []) if "price" in s]
    nearest_sup = min(supports, key=lambda p: abs(p - stop_f)) if supports else sys_stop
    too_tight_to_support = direction_u == "LONG" and abs(stop_f - nearest_sup) < 0.25 * atr14
    rec_stop = (
        sys_stop
        if (direction_u == "LONG" and sys_stop < entry_f)
        else round_to_tick(stop_f - 0.35 * atr14, market, asset_type, as_of)
    )
    if _is_round_number_price(rec_stop, market):
        rec_stop = round_to_tick(rec_stop - 2.0 * float(rules.get("tick_size") or 0.01), market, asset_type, as_of)

    if is_round or too_tight_to_support:
        stop_trap = {
            "status": "WARN",
            "is_round_number": is_round,
            "too_close_to_support": too_tight_to_support,
            "user_stop": stop_f,
            "recommended_stop": rec_stop,
            "note_cn": (
                f"用户止损位 {stop_f:.{p_dec}f} 位于明显整数关口/紧贴支撑位（极易被日内假跌破流动性扫损打穿）；"
                f"建议将硬止损下移至结构极值下方非整数位 {rec_stop:.{p_dec}f}，并以「5/15分钟放量跌破且反抽失败」作为执行确认"
            ),
        }
    else:
        stop_trap = {
            "status": "PASS",
            "is_round_number": False,
            "too_close_to_support": False,
            "user_stop": stop_f,
            "recommended_stop": stop_f,
            "note_cn": f"止损位 {stop_f:.{p_dec}f} 已避开整数关口与密集扫损带",
        }

    # 4. 扣除全口径双边摩擦后的净盈亏比审计
    notional = eff_shares * entry_f
    cost_detail = compute_round_trip_cost_detail(notional, rules, shares=eff_shares)
    fee_per_share = float(cost_detail["breakeven_price_delta"])
    gross_reward = abs(target_f - entry_f)
    gross_risk = abs(entry_f - stop_f)
    gross_rr = gross_reward / max(1e-6, gross_risk)
    net_reward = max(0.0, gross_reward - fee_per_share)
    net_risk = gross_risk + fee_per_share
    net_rr = net_reward / max(1e-6, net_risk)

    hk_t_violation = market == "HK" and asset_type == "equity" and gross_reward < 0.60
    if net_rr < 1.50 or hk_t_violation:
        hk_msg = (
            f"；港股双边印花税+规费下价差仅 {gross_reward:.2f} HKD（低于 0.60 HKD 保本底线及 1.50 HKD 推荐做T空间）"
            if hk_t_violation
            else ""
        )
        rr_check = {
            "status": "FAIL",
            "gross_rr": round(gross_rr, 2),
            "net_rr_after_cost": round(net_rr, 2),
            "round_trip_cost_amount": cost_detail["total_cost_amount"],
            "effective_cost_rate": cost_detail["effective_cost_rate"],
            "breakeven_price": round(entry_f + fee_per_share if direction_u == "LONG" else entry_f - fee_per_share, p_dec),
            "note_cn": (
                f"扣除双边佣金、印花税与规费（共 {cost_detail['total_cost_amount']:.2f} {meta.get('currency', '')}）后净盈亏比仅 {net_rr:.2f}（低于 1.50 门禁）{hk_msg}"
            ),
        }
    else:
        rr_check = {
            "status": "PASS",
            "gross_rr": round(gross_rr, 2),
            "net_rr_after_cost": round(net_rr, 2),
            "round_trip_cost_amount": cost_detail["total_cost_amount"],
            "effective_cost_rate": cost_detail["effective_cost_rate"],
            "breakeven_price": round(entry_f + fee_per_share if direction_u == "LONG" else entry_f - fee_per_share, p_dec),
            "note_cn": f"扣除全口径摩擦后净盈亏比 {net_rr:.2f} ≥ 1.50，满足正期望门槛",
        }

    # 5. 未来 15 日 Conformal 波动包络可达性审计
    h15 = (forecast.get("horizons") or {}).get("15") or {}
    q25 = float(h15.get("q25") or (entry_f - atr14))
    q75 = float(h15.get("q75") or (entry_f + atr14))
    q90 = float(h15.get("q90") or (entry_f + 2.0 * atr14))
    q10 = float(h15.get("q10") or (entry_f - 2.0 * atr14))
    if direction_u == "LONG" and target_f > q90:
        reach_check = {
            "status": "WARN",
            "target_vs_q90": round(target_f - q90, p_dec),
            "q75_price": q75,
            "q90_price": q90,
            "note_cn": f"目标价 {target_f:.{p_dec}f} 超出未来 15 日 90% 保形区间上沿（{q90:.{p_dec}f}），建议在 q75（{q75:.{p_dec}f}）附近先分批锁定 TP1",
        }
    elif direction_u == "SHORT" and target_f < q10:
        reach_check = {
            "status": "WARN",
            "target_vs_q10": round(target_f - q10, p_dec),
            "q25_price": q25,
            "q10_price": q10,
            "note_cn": f"目标价 {target_f:.{p_dec}f} 低于未来 15 日 10% 保形区间下沿（{q10:.{p_dec}f}），建议在 q25（{q25:.{p_dec}f}）分批止盈",
        }
    else:
        reach_check = {
            "status": "PASS",
            "q25_price": q25,
            "q75_price": q75,
            "q90_price": q90,
            "note_cn": f"目标价位于未来 15 日保形波动包络（{q10:.{p_dec}f}~{q90:.{p_dec}f}）可达范围内",
        }

    # 6. 汇总裁决与 If-Then 剧本
    statuses = [regime_check["status"], anti_chase["status"], stop_trap["status"], rr_check["status"], reach_check["status"]]
    if regime_check["status"] == "FAIL" or rr_check["status"] == "FAIL" or anti_chase["status"] == "FAIL":
        overall = "REJECTED_BY_RISK_GATE"
    elif "WARN" in statuses:
        overall = "APPROVED_WITH_ADJUSTMENTS"
    else:
        overall = "APPROVED"

    rec_entry = round(min(entry_f, float(sys_entry_zone[1])), p_dec) if (direction_u == "LONG" and anti_chase["status"] != "PASS") else entry_f
    rec_target = round(max(target_f, rec_entry + 1.8 * abs(rec_entry - rec_stop)), p_dec) if (direction_u == "LONG" and rr_check["status"] == "FAIL") else target_f
    be_trigger = round(rec_entry + 0.90 * abs(rec_entry - rec_stop), p_dec)
    be_stop = round(rec_entry + max(fee_per_share, 0.08 * abs(rec_entry - rec_stop)), p_dec)

    playbook = {
        "if_trigger_cn": (
            f"IF 价格回踩至 {rec_entry:.{p_dec}f} 附近缩量企稳（或 15 分钟放量突破确认且不破 VWAP）"
            if direction_u == "LONG"
            else f"IF 价格反弹受阻于 {rec_entry:.{p_dec}f} 且放量跌破支撑"
        ),
        "then_action_cn": f"THEN 按计划分批建仓/加仓 {eff_shares} 股，初始防扫损止损设于 {rec_stop:.{p_dec}f}，第一目标 {rec_target:.{p_dec}f}",
        "invalidation_cn": f"INVALIDATION 若 5/15 分钟放量跌破 {rec_stop:.{p_dec}f} 且反抽失败（或日线收盘跌破），无条件执行风控离场，严禁死扛或向下摊平",
        "ratchet_cn": f"RATCHET 当盘中触及 {be_trigger:.{p_dec}f}（+0.9R）且收盘企稳后，自次日起将防守止损上移至真保本位 {be_stop:.{p_dec}f}，让利润奔跑",
    }

    digest_payload = json.dumps(
        {"symbol": symbol, "as_of": as_of, "direction": direction_u, "entry": entry_f, "stop": stop_f, "target": target_f},
        sort_keys=True,
    )
    idea_id = "idea_" + hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()[:12]

    return {
        "idea_id": idea_id,
        "symbol": symbol,
        "market": market,
        "asset_type": asset_type,
        "as_of": as_of,
        "direction": direction_u,
        "overall_verdict": overall,
        "note": note,
        "user_idea": {
            "entry": entry_f,
            "stop": stop_f,
            "target": target_f,
            "shares": eff_shares,
        },
        "recommended_adjustments": {
            "entry": rec_entry,
            "stop": rec_stop,
            "target": rec_target,
            "shares": eff_shares,
        },
        "checks": {
            "regime_and_veto": regime_check,
            "anti_chase": anti_chase,
            "round_number_stop_trap": stop_trap,
            "net_rr_and_friction": rr_check,
            "conformal_reachability": reach_check,
        },
        "if_then_playbook": playbook,
    }


def append_idea_to_ledger(audit_doc: dict[str, Any], ledger_path: Path) -> None:
    """将交易想法审计记录按幂等追加方式写入 JSONL 账本。"""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    existing_ids: set[str] = set()
    if ledger_path.exists():
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    existing_ids.add(str(json.loads(line).get("idea_id")))
                except Exception:  # noqa: BLE001
                    continue
    if audit_doc["idea_id"] not in existing_ids:
        with ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(audit_doc, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="交易想法 If-Then 机械化双向风控审计器")
    parser.add_argument("--result", type=Path, required=True, help="*_result.json 文件路径")
    parser.add_argument("--direction", default="LONG", choices=["LONG", "SHORT"], help="交易方向")
    parser.add_argument("--entry", type=float, required=True, help="计划入场价")
    parser.add_argument("--stop", type=float, required=True, help="计划止损价")
    parser.add_argument("--target", type=float, required=True, help="计划目标价")
    parser.add_argument("--shares", type=int, default=None, help="计划股数（可选）")
    parser.add_argument("--note", default="", help="交易想法备注")
    parser.add_argument("--ledger", type=Path, default=None, help="可选：追加写入的交易想法账本 JSONL 路径")
    args = parser.parse_args(argv)

    result_doc = json.loads(args.result.read_text(encoding="utf-8"))
    audit = audit_trade_idea(
        result_doc,
        direction=args.direction,
        entry=args.entry,
        stop=args.stop,
        target=args.target,
        shares=args.shares,
        note=args.note,
    )
    if args.ledger is not None:
        append_idea_to_ledger(audit, args.ledger)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
