"""留出集 H1 的运行守卫：未授权不得进入 H1 模式、预注册被改动时拒绝运行、只能运行一次。均不评估任何 H1 数据。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))

import run_dev_region_v2 as v2  # noqa: E402
import run_h1_holdout as h1  # noqa: E402
from common import PIT_UNIVERSE  # noqa: E402

pytestmark = pytest.mark.skipif(not PIT_UNIVERSE.exists(), reason="需要 artifacts/long_backtest 真实行情缓存")


def test_h1_mode_requires_authorization(monkeypatch) -> None:
    monkeypatch.setattr(v2, "H1_AUTHORIZED", False)
    with pytest.raises(PermissionError):
        v2._mode_config("h1", "CN")
    with pytest.raises(PermissionError):
        v2._init("CN", mode="h1")
    assert v2._mode_config("dev", "CN") == (v2.DEV_REGION, v2.PIT_UNIVERSE)


def test_verify_preregistration_detects_tampering(tmp_path) -> None:
    import write_preregistration_v2 as wp

    prereg = wp.build(["P1", "P2b", "P3"])
    path = tmp_path / "prereg.json"
    path.write_text(json.dumps(prereg), encoding="utf-8")
    _, problems = h1.verify_preregistration(path, check_git=False)
    assert problems == []
    first = next(iter(prereg["frozen_files_sha256"]))
    prereg["frozen_files_sha256"][first] = "0" * 64
    prereg["frozen_data_sha256"]["artifacts/long_backtest/pit_universe_h1.json"] = "0" * 64
    path.write_text(json.dumps(prereg), encoding="utf-8")
    _, problems = h1.verify_preregistration(path, check_git=False)
    assert any(first in p for p in problems) and any("pit_universe_h1.json" in p for p in problems)


def test_runner_refuses_second_run_and_unconfirmed_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(h1, "verify_preregistration", lambda *a, **k: ({}, []))
    result, lock = tmp_path / "h1_result.json", tmp_path / "h1_started.json"
    monkeypatch.setattr(h1, "RESULT", result)
    monkeypatch.setattr(h1, "LOCK", lock)
    monkeypatch.setattr(sys, "argv", ["run_h1_holdout.py"])
    result.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        h1.main()
    assert e.value.code == 3
    result.unlink()
    lock.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        h1.main()
    assert e.value.code == 4


def test_judge_uses_only_registered_gates() -> None:
    seg = {"criteria_diagnostic": {"P1_mean_net_positive": True, "P2a_rate_excess": False, "P2b_mean_excess": True,
                                   "P3_sample": True, "T_rate_ge_70": False}}
    assert h1.judge(seg, ["P1", "P2b", "P3"])["pass"] is True
    assert h1.judge(seg, ["P1", "P2a", "P2b", "P3"])["pass"] is False
    assert h1.judge(seg, ["P1", "P2b", "P3"])["T_rate_ge_70_report_only"] is False
