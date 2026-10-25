from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from analyze_and_forecast import run_pipeline


def test_audit_trade_idea_detects_integer_stop_chase_and_veto(replay_recorded, tmp_path: Path) -> None:
    """验证 If-Then 交易想法审计器能识别风控否决冲突、整数关口扫损陷阱、追高偏离与净盈亏比不足，并落盘账本。"""
    from audit_trade_idea import audit_trade_idea, append_idea_to_ledger

    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    run_pipeline(req, output_dir=tmp_path)
    result_doc = json.loads((tmp_path / "600519.SH_result.json").read_text(encoding="utf-8"))

    # 构造一个典型的散户冲动追高 + 整数关口裸止损 + 窄止盈想法
    last_close = float(result_doc["structure"]["last_close"])
    integer_stop = float(int(round(last_close / 100.0)) * 100 - 100)  # 如 1300.00 整数关口
    chase_entry = round(last_close * 1.06, 2)
    narrow_target = round(chase_entry + (chase_entry - integer_stop) * 0.8, 2)

    audit = audit_trade_idea(
        result_doc,
        direction="LONG",
        entry=chase_entry,
        stop=integer_stop,
        target=narrow_target,
        shares=100,
        note="测试冲动追高与整数位止损",
    )
    assert audit["symbol"] == "600519.SH"
    assert audit["overall_verdict"] in ("REJECTED_BY_RISK_GATE", "APPROVED_WITH_ADJUSTMENTS")
    checks = audit["checks"]
    assert checks["round_number_stop_trap"]["is_round_number"] is True
    assert checks["round_number_stop_trap"]["status"] == "WARN"
    assert checks["anti_chase"]["status"] in ("WARN", "FAIL")
    assert checks["net_rr_and_friction"]["net_rr_after_cost"] < 1.5
    assert checks["net_rr_and_friction"]["status"] == "FAIL"
    assert "if_trigger_cn" in audit["if_then_playbook"]
    assert "invalidation_cn" in audit["if_then_playbook"]

    ledger_path = tmp_path / "ideas_ledger.jsonl"
    append_idea_to_ledger(audit, ledger_path)
    lines = ledger_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["idea_id"] == audit["idea_id"]


def test_audit_trade_idea_hk_connect_t_trade_cost_floor(replay_recorded, tmp_path: Path) -> None:
    """港股做 T / 波段审计：单笔价差过窄时被摩擦成本拦截，提示港股双边印花税+规费保本约束。"""
    from audit_trade_idea import audit_trade_idea

    req = {"symbol": "00700.HK", "market": "HK", "asset_type": "equity", "display_bars": 120, "horizon_bars": 15}
    run_pipeline(req, output_dir=tmp_path)
    result_doc = json.loads((tmp_path / "00700.HK_result.json").read_text(encoding="utf-8"))

    last_close = float(result_doc["structure"]["last_close"])
    audit = audit_trade_idea(
        result_doc,
        direction="LONG",
        entry=last_close,
        stop=round(last_close * 0.98, 2),
        target=round(last_close + 0.40, 2),  # 仅 0.40 港元微利空间，低于港股 0.60 HKD 保本门槛
        shares=100,
    )
    hk_check = audit["checks"]["net_rr_and_friction"]
    assert hk_check["status"] == "FAIL"
    assert "港股" in hk_check["note_cn"]


def test_score_trade_ideas_reconciles_swept_integer_stop_vs_audited_stop(tmp_path: Path) -> None:
    """复盘对账器：验证用户整数止损被盘中假跌破扫损后拉升触达目标（STOP_SWEPT_THEN_TARGET）与审计后防扫损止损的对比。"""
    from score_trade_ideas import reconcile_trade_ideas

    ledger_path = tmp_path / "ledger.jsonl"
    idea_record = {
        "idea_id": "idea_test_001",
        "symbol": "002281.SZ",
        "market": "CN",
        "as_of": "2026-10-15",
        "direction": "LONG",
        "user_idea": {
            "entry": 132.00,
            "stop": 130.00,
            "target": 145.00,
            "shares": 200,
        },
        "recommended_adjustments": {
            "entry": 132.00,
            "stop": 128.60,
            "target": 145.00,
        },
    }
    ledger_path.write_text(json.dumps(idea_record, ensure_ascii=False) + "\n", encoding="utf-8")

    # 真实走势模拟测试数据：次日回踩 131.50 触发入场，第3日盘中下探 129.80（打穿 130.00 整数位但未破 128.60 且收盘 132.50），第6日拉升至 146.20 触及止盈
    future_bars = [
        {"day": "2026-10-15", "open": 134.0, "high": 135.0, "low": 132.5, "close": 132.96, "volume": 100000},
        {"day": "2026-10-16", "open": 132.5, "high": 133.2, "low": 131.5, "close": 132.80, "volume": 90000},
        {"day": "2026-10-19", "open": 132.2, "high": 133.0, "low": 129.8, "close": 132.50, "volume": 120000},
        {"day": "2026-10-20", "open": 133.0, "high": 138.5, "low": 132.6, "close": 137.90, "volume": 150000},
        {"day": "2026-10-21", "open": 138.0, "high": 146.2, "low": 137.5, "close": 145.50, "volume": 180000},
    ]
    summary = reconcile_trade_ideas(
        [idea_record],
        bars_by_symbol={"002281.SZ": future_bars},
        horizon_bars=15,
    )
    assert summary["total_ideas"] == 1
    assert summary["triggered_count"] == 1
    assert summary["swept_stop_saved_count"] == 1
    item = summary["items"][0]
    assert item["user_outcome"]["status"] == "STOP_HIT"
    assert item["audited_outcome"]["status"] == "TP_HIT"
    assert item["attribution"] == "STOP_SWEPT_THEN_TARGET"
