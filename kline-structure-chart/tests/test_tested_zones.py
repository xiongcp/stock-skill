"""多次测试价区（tested_zones）：固定样例、性质与边界（只用录制的真实行情，测试断网）。"""
from __future__ import annotations

import hashlib
import json

import pytest

from conftest import RECORDED_DIR, load_recorded

CASE = "sina_daily_cases/sh000001_2026-11-17.json"
SH = "sina_daily/sh000001.json"
MOUTAI = "sina_daily/sh600519.json"
BYD = "sina_daily/sz002594.json"


def _bars(rel: str, upto: str = "9999-12-31") -> list[dict]:
    """录制行情截至 upto（含），只保留结构层用到的字段。"""
    return [{k: r[k] for k in ("day", "open", "high", "low", "close")} for r in load_recorded(rel) if r["day"] <= upto]


# ---------------------------------------------------------------------------
# 任务 1：固定截止日的真实行情录制
# ---------------------------------------------------------------------------


def test_case_fixture_is_recorded_real_data() -> None:
    manifest = json.loads((RECORDED_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    entry = manifest["files"][CASE]
    text = (RECORDED_DIR / CASE).read_text(encoding="utf-8")
    assert entry["source"].startswith("SINA getKLineData symbol=sh000001")
    assert entry["last_day"] == "2026-11-17" and entry["rows"] >= 500
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == entry["sha256"]
    lows = {b["day"]: b["low"] for b in json.loads(text)[-3:]}
    assert lows == {"2026-11-13": 3623.841, "2026-11-16": 3586.568, "2026-11-17": 3565.59}
