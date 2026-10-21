"""主做多计划 H1 守卫与预注册 v3：篡改检测、缓存与清单逐个核对、只能运行一次、判定只用登记的门槛。均不评估任何 H1 数据。"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))

import main_plan_eval as ev  # noqa: E402
import run_h1_holdout as h1  # noqa: E402
import run_h1_main_plan as g3  # noqa: E402
from common import CACHE, LONG_ACTIONS, PIT_UNIVERSE  # noqa: E402

pytestmark = pytest.mark.skipif(not PIT_UNIVERSE.exists(), reason="需要 artifacts/long_backtest 真实行情缓存")


def test_prereg_v3_detects_tampering(tmp_path) -> None:
    import write_preregistration_v3_main_plan as wp3

    prereg = wp3.build()
    assert prereg["acceptance"]["gates"] == ["P1", "P2b", "P3"]
    assert prereg["acceptance"]["acceptance_markets"] == ["CN", "US", "HK"]
    assert "research/main_plan_scan.py" in prereg["frozen_files_sha256"]
    assert prereg["baseline"]["seed"] == ev.SEED
    path = tmp_path / "prereg.json"
    path.write_text(json.dumps(prereg), encoding="utf-8")
    _, problems = h1.verify_preregistration(path, check_git=False)
    assert problems == []
    prereg["frozen_files_sha256"]["research/main_plan_eval.py"] = "0" * 64
    path.write_text(json.dumps(prereg), encoding="utf-8")
    _, problems = h1.verify_preregistration(path, check_git=False)
    assert any("main_plan_eval.py" in p for p in problems)


def test_cache_files_checked_against_manifest(tmp_path) -> None:
    """复制两个真实缓存文件到临时目录：清单一致时通过；文件被改动或清单缺项时拒绝。"""
    cache = tmp_path / "cache"
    for rel in ("US/SPY.US.json.gz", "US/_VIX.json.gz"):
        (cache / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CACHE / rel, cache / rel)
    entries = [{"file": rel, "sha256": hashlib.sha256((cache / rel).read_bytes()).hexdigest()}
               for rel in ("US/SPY.US.json.gz", "US/_VIX.json.gz")]
    (cache / "MANIFEST.json").write_text(json.dumps({"files": entries}), encoding="utf-8")
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"markets": {"US": {"periods": [{"symbols": []}]}}}), encoding="utf-8")
    assert g3.verify_cache_files(["US"], universe, cache) == []
    with (cache / "US/_VIX.json.gz").open("ab") as fh:
        fh.write(b"\0")
    assert any("_VIX" in p for p in g3.verify_cache_files(["US"], universe, cache))
    (cache / "MANIFEST.json").write_text(json.dumps({"files": entries[1:]}), encoding="utf-8")
    assert any("清单缺少" in p and "SPY.US" in p for p in g3.verify_cache_files(["US"], universe, cache))


def test_runner_refuses_second_run_and_unconfirmed_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(g3, "verify_all", lambda *a, **k: ({}, []))
    result, lock = tmp_path / "h1_main_plan_result.json", tmp_path / "h1_main_plan_started.json"
    monkeypatch.setattr(g3, "RESULT", result)
    monkeypatch.setattr(g3, "LOCK", lock)
    monkeypatch.setattr(sys, "argv", ["run_h1_main_plan.py"])
    result.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        g3.main()
    assert e.value.code == 3
    result.unlink()
    lock.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        g3.main()
    assert e.value.code == 4


def test_runner_refuses_when_verification_fails(monkeypatch) -> None:
    monkeypatch.setattr(g3, "verify_all", lambda *a, **k: ({}, ["冻结文件已改动：x"]))
    monkeypatch.setattr(sys, "argv", ["run_h1_main_plan.py", "--check"])
    with pytest.raises(SystemExit) as e:
        g3.main()
    assert e.value.code == 2


def test_summary_feeds_registered_judge() -> None:
    """ev.summarize 的判定字段可直接交给 run_h1_holdout.judge，且只用登记的门槛。"""
    rows = [{"symbol": f"S{i:03d}", "day": f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}", "fill_status": "FILL",
             "net": 0.01, "gross": 0.013, "outcome": "TP_BARRIER", "action": LONG_ACTIONS[0],
             "setup": "RIGHT_SIDE_BREAKOUT"} for i in range(120)]
    base = [{**r, "symbol": f"B{i:03d}", "net": -0.01, "gross": -0.007, "outcome": "SL_BARRIER"} for i, r in enumerate(rows)]
    s = ev.summarize(rows, {"transplant": base, "own_plan": base}, note="测试")
    v = h1.judge(s, ["P1", "P2b", "P3"])
    assert v["pass"] is True and set(v["checks"]) == {"P1", "P2b", "P3"}
