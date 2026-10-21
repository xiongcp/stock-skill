"""主做多计划 H1 守卫与预注册 v3：篡改检测、缓存与清单逐个核对、只能运行一次、判定只用登记的门槛。均不评估任何 H1 数据。"""
from __future__ import annotations

import gzip
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
    assert "kline-structure-chart/uv.lock" in prereg["frozen_files_sha256"]
    assert "kline-structure-chart/pyproject.toml" in prereg["frozen_files_sha256"]
    assert prereg["environment"]["numpy"] and prereg["environment"]["python"]
    assert prereg["run_tolerance"] == {"max_scan_errors": 0, "max_skipped_symbols": 0}
    path = tmp_path / "prereg.json"
    path.write_text(json.dumps(prereg), encoding="utf-8")
    _, problems = h1.verify_preregistration(path, check_git=False)
    assert problems == []
    prereg["frozen_files_sha256"]["research/main_plan_eval.py"] = "0" * 64
    path.write_text(json.dumps(prereg), encoding="utf-8")
    _, problems = h1.verify_preregistration(path, check_git=False)
    assert any("main_plan_eval.py" in p for p in problems)


def _copy_real_cache(tmp_path, rels: tuple[str, ...], drop_sha_for: str | None = None) -> Path:
    """把真实缓存文件和真实清单里对应的条目复制到临时目录（清单登记的是 K线与事件内容的 hash，不是文件字节的 hash）。"""
    cache = tmp_path / "cache"
    real = {e["file"]: e for e in json.loads((CACHE / "MANIFEST.json").read_text(encoding="utf-8"))["files"]}
    entries = []
    for rel in rels:
        (cache / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CACHE / rel, cache / rel)
        entry = dict(real[rel])
        if rel == drop_sha_for:
            entry = {"file": rel, "error": "写清单时出错"}
        entries.append(entry)
    (cache / "MANIFEST.json").write_text(json.dumps({"files": entries}), encoding="utf-8")
    return cache


def test_cache_files_checked_against_manifest(tmp_path) -> None:
    """真实清单与真实缓存一致时通过；改动一根K线、清单缺项或清单条目缺 hash 时拒绝。"""
    rels = ("US/SPY.US.json.gz", "US/_VIX.json.gz")
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"markets": {"US": {"periods": [{"symbols": []}]}}}), encoding="utf-8")
    cache = _copy_real_cache(tmp_path / "a", rels)
    assert g3.verify_cache_files(["US"], universe, cache) == []

    with gzip.open(cache / "US/_VIX.json.gz", "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    payload["bars"][100]["close"] = float(payload["bars"][100]["close"]) + 1.0  # 篡改副本，模拟缓存被就地改写（文件自带的 hash 不变）
    with gzip.open(cache / "US/_VIX.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(payload, fh)
    assert any("_VIX" in p for p in g3.verify_cache_files(["US"], universe, cache))

    cache_b = _copy_real_cache(tmp_path / "b", rels[1:])
    assert any("清单缺少" in p and "SPY.US" in p for p in g3.verify_cache_files(["US"], universe, cache_b))
    cache_c = _copy_real_cache(tmp_path / "c", rels, drop_sha_for="US/SPY.US.json.gz")
    assert any("SPY.US" in p for p in g3.verify_cache_files(["US"], universe, cache_c))


def test_scan_tolerance_blocks_judgment_on_shrunken_sample() -> None:
    ok = {"CN": {"errors": 0, "skipped_symbols": 0}, "US": {"errors": 0, "skipped_symbols": 0}}
    assert g3.check_scan_tolerance(ok, {"max_scan_errors": 0, "max_skipped_symbols": 0}) == []
    bad = {**ok, "HK": {"errors": 3, "skipped_symbols": 1}}
    problems = g3.check_scan_tolerance(bad, {"max_scan_errors": 0, "max_skipped_symbols": 0})
    assert len(problems) == 2 and all("HK" in p for p in problems)


def test_stale_h1_scan_dirs_detected(tmp_path) -> None:
    assert g3.stale_h1_scan_dirs(tmp_path) == []
    (tmp_path / "dev_CN_abc").mkdir()
    assert g3.stale_h1_scan_dirs(tmp_path) == []
    (tmp_path / "h1_CN_abc").mkdir()
    assert [p.name for p in g3.stale_h1_scan_dirs(tmp_path)] == ["h1_CN_abc"]


def test_runner_refuses_when_h1_was_scanned_outside_guard(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(g3, "verify_all", lambda *a, **k: ({}, []))
    monkeypatch.setattr(g3, "RESULT", tmp_path / "r.json")
    monkeypatch.setattr(g3, "LOCK", tmp_path / "l.json")
    monkeypatch.setattr(g3, "stale_h1_scan_dirs", lambda *a, **k: [tmp_path / "h1_CN_abc"])
    monkeypatch.setattr(sys, "argv", ["run_h1_main_plan.py", "--check"])
    with pytest.raises(SystemExit) as e:
        g3.main()
    assert e.value.code == 6


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
