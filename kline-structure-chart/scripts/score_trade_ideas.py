#!/usr/bin/env python3
"""交易想法到期复盘与扫损归因对账器 (Sub-project 3)。

读取 `audit_trade_idea.py` 生成的交易想法账本（JSONL）与后续真实日线行情，
客观对比「用户原始计划（如整数关口止损）」与「系统审计后防扫损计划」在未来 N 个交易日内的真实触发、
盈亏结果与「整数位被假跌破扫损后反包触达目标（STOP_SWEPT_THEN_TARGET）」归因。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _simulate_plan_on_bars(
    future_bars: list[dict[str, Any]],
    *,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    market: str = "CN",
) -> dict[str, Any]:
    """在 `as_of` 之后的真实 K 线序列上因果回放单笔 If-Then 计划（A 股遵循 T+1 次日方可平仓）。"""
    if not future_bars:
        return {"triggered": False, "status": "NO_FUTURE_BARS", "realized_r": 0.0}
    risk = max(1e-6, abs(entry - stop))
    entry_idx: int | None = None
    for idx, b in enumerate(future_bars):
        lo, hi = float(b["low"]), float(b["high"])
        if direction == "LONG" and lo <= entry <= hi:
            entry_idx = idx
            break
        if direction == "LONG" and float(b["open"]) <= entry:
            entry_idx = idx
            break
        if direction == "SHORT" and lo <= entry <= hi:
            entry_idx = idx
            break
    if entry_idx is None:
        return {"triggered": False, "status": "NEVER_TRIGGERED", "realized_r": 0.0}

    entry_day = str(future_bars[entry_idx]["day"])
    # A 股 T+1：入场当日不可卖出止损或止盈，从 entry_idx + 1 起检测离场屏障
    start_exit_idx = entry_idx + 1 if market == "CN" else entry_idx
    for idx in range(start_exit_idx, len(future_bars)):
        b = future_bars[idx]
        lo, hi = float(b["low"]), float(b["high"])
        if direction == "LONG":
            # 同日既触及止损又触及止盈时按保守不利原则先算 STOP_HIT
            if lo <= stop:
                return {
                    "triggered": True,
                    "entry_day": entry_day,
                    "exit_day": str(b["day"]),
                    "status": "STOP_HIT",
                    "realized_r": -1.0,
                }
            if hi >= target:
                return {
                    "triggered": True,
                    "entry_day": entry_day,
                    "exit_day": str(b["day"]),
                    "status": "TP_HIT",
                    "realized_r": round((target - entry) / risk, 2),
                }
        else:
            if hi >= stop:
                return {
                    "triggered": True,
                    "entry_day": entry_day,
                    "exit_day": str(b["day"]),
                    "status": "STOP_HIT",
                    "realized_r": -1.0,
                }
            if lo <= target:
                return {
                    "triggered": True,
                    "entry_day": entry_day,
                    "exit_day": str(b["day"]),
                    "status": "TP_HIT",
                    "realized_r": round((entry - target) / risk, 2),
                }

    last_c = float(future_bars[-1]["close"])
    r_val = (last_c - entry) / risk if direction == "LONG" else (entry - last_c) / risk
    return {
        "triggered": True,
        "entry_day": entry_day,
        "exit_day": str(future_bars[-1]["day"]),
        "status": "TIME_BARRIER_EXPIRED",
        "realized_r": round(r_val, 2),
    }


def reconcile_trade_ideas(
    ideas: list[dict[str, Any]],
    *,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    horizon_bars: int = 15,
) -> dict[str, Any]:
    """对账交易想法账本与实际行情走势，识别假跌破扫损后反包等归因。"""
    items: list[dict[str, Any]] = []
    triggered_count = 0
    swept_saved_count = 0
    user_wins = 0
    audited_wins = 0

    for idea in ideas:
        sym = str(idea.get("symbol") or "")
        market = str(idea.get("market") or "CN")
        as_of = str(idea.get("as_of") or "")
        direction = str(idea.get("direction") or "LONG").upper()
        all_bars = bars_by_symbol.get(sym) or []
        future_bars = [b for b in all_bars if str(b["day"]) > as_of][:horizon_bars]

        u_plan = idea.get("user_idea") or {}
        a_plan = idea.get("recommended_adjustments") or u_plan

        u_out = _simulate_plan_on_bars(
            future_bars,
            direction=direction,
            entry=float(u_plan["entry"]),
            stop=float(u_plan["stop"]),
            target=float(u_plan["target"]),
            market=market,
        )
        a_out = _simulate_plan_on_bars(
            future_bars,
            direction=direction,
            entry=float(a_plan["entry"]),
            stop=float(a_plan["stop"]),
            target=float(a_plan["target"]),
            market=market,
        )
        if u_out["triggered"] or a_out["triggered"]:
            triggered_count += 1
        if u_out["status"] == "TP_HIT":
            user_wins += 1
        if a_out["status"] == "TP_HIT":
            audited_wins += 1

        if u_out["status"] == "STOP_HIT" and a_out["status"] == "TP_HIT":
            attribution = "STOP_SWEPT_THEN_TARGET"
            swept_saved_count += 1
        elif u_out["status"] == "STOP_HIT" and a_out["status"] == "STOP_HIT":
            attribution = " GENUINE_BREAKDOWN_STOP".strip()
        elif a_out["status"] == "TP_HIT":
            attribution = "TARGET_ACHIEVED"
        elif not u_out["triggered"] and not a_out["triggered"]:
            attribution = "UNTRIGGERED"
        else:
            attribution = "TIME_EXPIRED"

        items.append(
            {
                "idea_id": idea.get("idea_id"),
                "symbol": sym,
                "as_of": as_of,
                "direction": direction,
                "user_outcome": u_out,
                "audited_outcome": a_out,
                "attribution": attribution,
            }
        )

    return {
        "total_ideas": len(ideas),
        "triggered_count": triggered_count,
        "swept_stop_saved_count": swept_saved_count,
        "user_tp_hit_count": user_wins,
        "audited_tp_hit_count": audited_wins,
        "items": items,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="交易想法到期复盘与扫损归因对账器")
    parser.add_argument("--ledger", type=Path, required=True, help="交易想法 JSONL 账本路径")
    parser.add_argument("--bars-json", type=Path, required=True, help="symbol -> bars 映射或单标的 bars JSON 路径")
    parser.add_argument("--symbol", default=None, help="当 --bars-json 为单标的数组时指定 symbol")
    parser.add_argument("--horizon-bars", type=int, default=15, help="对账观察期交易日数")
    args = parser.parse_args(argv)

    ideas = [
        json.loads(line)
        for line in args.ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    raw_bars = json.loads(args.bars_json.read_text(encoding="utf-8"))
    if isinstance(raw_bars, list):
        sym = args.symbol or (ideas[0]["symbol"] if ideas else "UNKNOWN")
        bars_by_sym = {sym: raw_bars}
    else:
        bars_by_sym = raw_bars

    summary = reconcile_trade_ideas(ideas, bars_by_symbol=bars_by_sym, horizon_bars=args.horizon_bars)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
