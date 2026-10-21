"""主做多计划检验：第一遍记录与线上一致、撮合与线上标签一致、基准抽样、汇总口径、续跑与出错计数（真实缓存行情）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))

import main_plan_scan as scan  # noqa: E402
import run_dev_region_v2 as v2  # noqa: E402
from common import LONG_ACTIONS, PIT_UNIVERSE  # noqa: E402
from run_backtest import make_pit_slice  # noqa: E402

from analysis import analyze_bundle  # noqa: E402

pytestmark = pytest.mark.skipif(not PIT_UNIVERSE.exists(), reason="需要 artifacts/long_backtest 真实行情缓存")


def _scan_some(market: str, n_symbols: int = 6, days_per_symbol: int = 40) -> list[tuple[str, dict, dict]]:
    """等间隔抽几只标的、每只等间隔抽若干天，逐个调用线上流水线（整只标的扫完太慢，不适合单测）。"""
    scan.init(market)
    symbols = sorted(v2._C["periods"])
    out = []
    for sym in symbols[:: max(1, len(symbols) // n_symbols)][:n_symbols]:
        data = v2._load_symbol(sym)
        if data is None:
            continue
        days = list(v2._eligible_days(sym, data))
        for day, t, b_pos in days[:: max(1, len(days) // days_per_symbol)][:days_per_symbol]:
            out.append((sym, data, scan.scan_stock_day(sym, data, t, day, b_pos)))
    return out


@pytest.fixture(scope="module")
def us_rows() -> list[tuple[str, dict, dict]]:
    return _scan_some("US")


def test_scan_record_matches_live_report(us_rows) -> None:
    """记录的评级、入场类型与计划价位必须与线上流水线逐项相同（防止被改成复制实现）。"""
    scan.init("US")
    signals = [x for x in us_rows if x[2]["a"] in LONG_ACTIONS]
    assert signals, "抽样里没有主计划信号，检验没有说服力：加大 days_per_symbol"
    for sym, data, r in us_rows[:20] + signals:
        pit, cut = make_pit_slice(data["bars"], r["t"], v2.HORIZON)
        bundle, request = v2.build_bundle(sym, data, pit, cut, r["day"])
        rep = analyze_bundle(bundle, request, include_replay=False, include_forecast=False)
        ml, plan = rep["meta_labeling_decision"], rep["triple_barrier_plan"]
        assert (r["a"], r["st"]) == (ml["action"], ml["setup_type"])
        assert r["z"] == [float(x) for x in plan["entry_zone"]]
        assert (r["sl"], r["tp"]) == (float(plan["stop_loss_barrier"]), float(plan["take_profit_1"]))
        rt = plan["breakeven_ratchet"]
        assert r["rt"] == [bool(rt["enabled"]), float(rt["trigger_r"]), float(rt["confirm_close_r"]), float(rt["lock_r"])]
        c = float(pit[cut]["close"])
        assert r["c"] == c
        assert r["atr"] == max(c * 0.005, float(rep["mathematical_indicators"]["volatility"]["atr14"]))
    for _, _, r in signals:
        assert r["sl"] < r["z"][0] <= r["z"][1] < r["tp"], r


def test_h1_requires_authorization_and_dev_stays_out(monkeypatch) -> None:
    monkeypatch.setattr(scan, "H1_AUTHORIZED", False)
    with pytest.raises(PermissionError):
        scan.mode_config("h1", "CN")
    with pytest.raises(PermissionError):
        scan.init("CN", mode="h1")
    region, universe = scan.mode_config("dev", "CN")
    assert region == v2.DEV_REGION and region[0] > v2.H1_LAST_DAY and universe == PIT_UNIVERSE


def test_scan_resume_and_error_capture(tmp_path, monkeypatch) -> None:
    """单个股票-日出错只记错误、不中断；中途崩溃后续跑只补未完成的分片，已完成的分片不重写。"""
    monkeypatch.setattr(scan, "SCAN_DIR", tmp_path)
    monkeypatch.setattr(scan, "CHUNK", 1)
    seen: list[str] = []

    def fake_scan_stock_day(symbol, data, t, day, b_pos):
        # 只替换耗时的线上流水线调用，记录字段与真实记录相同；第一次遇到的第 3 个股票-日模拟流水线报错
        seen.append(day)
        if len(seen) == 3:
            raise ValueError("模拟流水线异常")
        return {"sym": symbol, "day": day, "t": t, "b": b_pos, "a": "NEUTRAL_HOLD_WAIT", "st": "WAIT_CONFIRMATION",
                "z": [0.0, 0.0], "sl": 0.0, "tp": 0.0, "rt": [True, 0.9, 0.3, 0.08], "atr": 0.0, "c": 0.0}

    monkeypatch.setattr(scan, "scan_stock_day", fake_scan_stock_day)
    out_dir = scan.run_scan("US", workers=1, limit_symbols=2)
    stats1 = scan.scan_stats(out_dir)
    assert stats1["n_shards"] == 2 and stats1["errors"] == 1 and stats1["rows"] == len(seen) - 1
    assert (out_dir / "COMPLETE").exists()
    rows1 = list(scan.iter_rows(out_dir))
    assert sum(1 for r in rows1 if r.get("__error__")) == 1

    kept = out_dir / "shard_0000.jsonl.gz"
    mtime = kept.stat().st_mtime_ns
    (out_dir / "shard_0001.done").unlink()
    (out_dir / "shard_0001.jsonl.gz").unlink()
    seen.clear()
    seen.extend(["x", "y", "z"])  # 续跑时不再触发模拟异常
    out_dir2 = scan.run_scan("US", workers=1, limit_symbols=2)
    assert out_dir2 == out_dir and kept.stat().st_mtime_ns == mtime
    stats2 = scan.scan_stats(out_dir)
    assert stats2["n_shards"] == 2 and stats2["rows"] >= stats1["rows"]
