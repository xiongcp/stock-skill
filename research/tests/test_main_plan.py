"""主做多计划检验：第一遍记录与线上一致、撮合与线上标签一致、基准抽样、汇总口径、续跑与出错计数（真实缓存行情）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))

import main_plan_eval as ev  # noqa: E402
import main_plan_scan as scan  # noqa: E402
import run_dev_region_v2 as v2  # noqa: E402
from common import LONG_ACTIONS, PIT_UNIVERSE  # noqa: E402
from run_backtest import make_pit_slice  # noqa: E402

from analysis import analyze_bundle  # noqa: E402
from evaluation import label_triple_barrier_event  # noqa: E402

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


def test_execution_matches_online_label(us_rows) -> None:
    """固定价位撮合：成交价不超过入场区上沿、止损在成交价下方，标签与用同一成交价调用线上函数的结果相同。"""
    scan.init("US")
    filled = 0
    for sym, data, r in us_rows:
        if r["a"] not in LONG_ACTIONS or not ev.valid_geometry(r):
            continue
        pit, cut = make_pit_slice(data["bars"], r["t"], v2.HORIZON)
        ex = ev.execute_main_plan(sym, data, pit, cut, r["b"], r["z"][0], r["z"][1], r["tp"], r["sl"], r["rt"])
        if ex["open_status"] == "FILL":
            assert r["z"][0] <= float(pit[cut + 1]["open"]) <= r["z"][1]
        if ex["fill_status"] != "FILL":
            continue
        assert r["sl"] < ex["fill_price"] <= r["z"][1]
        k = next(i for i, b in enumerate(pit) if b["day"] == ex["fill_day"])
        assert cut + 1 <= k <= cut + v2.LIMIT_SESSIONS
        ref = label_triple_barrier_event(
            pit, entry_idx=k, tp_price=r["tp"], sl_price=r["sl"], horizon_bars=v2.HORIZON, market="US",
            asset_type="equity", symbol=sym, is_st=data["is_st"], trailing_stop=r["rt"][0], trigger_r=r["rt"][1],
            confirm_close_r=r["rt"][2], lock_r=r["rt"][3], name=data["name"], entry_price_override=ex["fill_price"],
        )
        assert (ref["net_return"], ref["outcome"]) == (ex["net"], ex["outcome"])
        filled += 1
    assert filled >= 1, "抽样里没有成交的信号，检验没有说服力"


def test_transplant_levels_roundtrip_and_collapse(us_rows) -> None:
    """移植到信号自身应还原原价位；取整后挤到同一价位的几何必须剔除。"""
    sig = next(r for _, _, r in us_rows if r["a"] in LONG_ACTIONS and ev.valid_geometry(r))
    other = next(r for _, _, r in us_rows if r["a"] not in LONG_ACTIONS and r["c"] > 1.0)
    mult = ev.plan_atr_multiples(sig)
    back = ev.transplant_levels(mult, sig["c"], sig["atr"], "US", sig["day"])
    assert back is not None
    assert [back["lo"], back["hi"], back["sl"], back["tp"]] == pytest.approx([sig["z"][0], sig["z"][1], sig["sl"], sig["tp"]])
    moved = ev.transplant_levels(mult, other["c"], other["atr"], "US", other["day"])
    assert moved is None or moved["sl"] < moved["lo"] <= moved["hi"] < moved["tp"]
    assert ev.transplant_levels([-1e-4, 1e-4, -2e-4, 2e-4], other["c"], other["atr"], "US", other["day"]) is None
    # 止损倍数很大、套到高 ATR 股票上时止损会落到 0 以下：价位表取整会把它抬成一个最小价位，必须按原值剔除
    k_sl = -other["c"] / other["atr"] - 1.0
    assert ev.transplant_levels([-0.1, 0.1, k_sl, 1.0], other["c"], other["atr"], "US", other["day"]) is None


def test_baseline_plan_rate_cap_and_reproducible_draw(us_rows) -> None:
    sigs = [r for _, _, r in us_rows if r["a"] in LONG_ACTIONS][:3]
    plans = ev.baseline_plan({"2024-01-02": sigs, "2024-01-03": sigs[:1]}, {"2024-01-02": 100, "2024-01-03": 5})
    assert plans["2024-01-02"]["rate"] == pytest.approx(ev.BASELINE_MULTIPLE * len(sigs) / 100)
    assert plans["2024-01-03"]["rate"] == 1.0  # 股票池不足 10 倍时全部抽取
    assert len(plans["2024-01-02"]["mults"]) == len(sigs)
    row = {"sym": "AAPL.US", "day": "2024-01-03"}
    j = ev.draw("US", row, plans["2024-01-03"])
    assert j == 0 and ev.draw("US", row, plans["2024-01-03"]) == j
    assert ev.draw("US", row, None) is None


def _outcome_row(i: int, net: float, gross: float, **extra) -> dict:
    # 汇总口径测试只需要结局数值（不是行情序列）；每行一个独立标的、独立日期，避免 episode 去重
    return {"symbol": f"S{i:03d}", "day": f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}", "fill_status": "FILL",
            "net": net, "gross": gross, "outcome": "TP_BARRIER" if net > 0 else "SL_BARRIER", **extra}


def test_summarize_counts_criteria_cost_and_groups() -> None:
    signals = [
        _outcome_row(i, 0.01 if i % 3 else -0.01, (0.01 if i % 3 else -0.01) + 0.003,
                     action=LONG_ACTIONS[i % 2], setup="RIGHT_SIDE_BREAKOUT" if i % 4 else "LEFT_SIDE_PULLBACK",
                     open_status="OPEN_OUTSIDE_ZONE")
        for i in range(120)
    ]
    signals.append({"symbol": "G1", "day": "2024-06-03", "action": LONG_ACTIONS[0], "setup": "RIGHT_SIDE_BREAKOUT",
                    "fill_status": "GAP_BELOW_STOP", "open_status": "OPEN_OUTSIDE_ZONE"})
    signals.append({"symbol": "G2", "day": "2024-06-04", "action": LONG_ACTIONS[1], "setup": "LEFT_SIDE_PULLBACK",
                    "fill_status": "GEOMETRY_INVALID"})
    base = ev.aggregate_baseline([_outcome_row(i, 0.0, 0.003) for i in range(120)])
    s = ev.summarize(signals, {"transplant": base, "own_plan": base}, note="测试")

    assert s["n_signals"] == 122 and sum(s["fill_status"].values()) == 122
    assert s["fill_status"]["GAP_BELOW_STOP"] == 1 and s["fill_status"]["GEOMETRY_INVALID"] == 1
    assert s["fill_layer"]["n_episodes"] == 120
    mean = s["fill_layer"]["mean_net_return_pct"]
    assert mean == pytest.approx(0.333, abs=1e-3)
    assert s["cost_2x"]["fill_layer"]["mean_net_return_pct"] == pytest.approx(mean - 0.3, abs=1e-3)
    assert s["excess_vs_same_day_random"]["mean_pct"] == pytest.approx(mean, abs=1e-3)
    crit = s["criteria_diagnostic"]
    assert set(crit) == {"note", "P1_mean_net_positive", "P2a_rate_excess", "P2b_mean_excess", "P3_sample", "T_rate_ge_70"}
    assert crit["P3_sample"] is True and crit["note"] == "测试"
    assert set(s["groups"]["action"]) == set(LONG_ACTIONS)
    assert set(s["groups"]["setup"]) == {"RIGHT_SIDE_BREAKOUT", "LEFT_SIDE_PULLBACK"}
    assert set(s["groups"]["year"]) == {"2024"}


def test_summarize_flags_small_samples() -> None:
    signals = [_outcome_row(i, 0.02, 0.023, action=LONG_ACTIONS[0], setup="RIGHT_SIDE_BREAKOUT") for i in range(20)]
    s = ev.summarize(signals, {"transplant": {}, "own_plan": {}}, note="测试")
    assert s["criteria_diagnostic"]["P3_sample"] is False
    assert s["criteria_diagnostic"]["P2b_mean_excess"] is False  # 没有基准时不能判为通过
    assert s["excess_vs_same_day_random"] is None


def test_p2b_pairs_each_signal_episode_with_its_own_day() -> None:
    """P2b 按信号 episode 与其当日基准均值配对：各日信号与基准同涨同跌时超额为 0；
    若按两组各自汇总再相减，基准多的日子会压过信号多的日子，得出虚假的负超额。"""
    signals = [_outcome_row(0, 0.02, 0.023, action=LONG_ACTIONS[0], setup="RIGHT_SIDE_BREAKOUT")]
    signals += [_outcome_row(i, 0.0, 0.003, action=LONG_ACTIONS[0], setup="RIGHT_SIDE_BREAKOUT") for i in range(1, 10)]
    signals.append({**_outcome_row(200, 0.05, 0.053, action=LONG_ACTIONS[0], setup="RIGHT_SIDE_BREAKOUT"), "day": "2025-03-03"})
    base_rows = [{**_outcome_row(300 + k, 0.02, 0.023), "day": signals[0]["day"]} for k in range(10)]
    base_rows += [{**_outcome_row(400 + i, 0.0, 0.003), "day": signals[i]["day"]} for i in range(1, 10)]
    base = ev.aggregate_baseline(base_rows)
    ex = ev.summarize(signals, {"transplant": base, "own_plan": base}, note="测试")["excess_vs_same_day_random"]
    assert ex["mean_pct"] == pytest.approx(0.0, abs=1e-9)
    assert ex["rate_pp"] == pytest.approx(0.0, abs=1e-9)
    assert ex["n_paired_episodes"] == 10 and ex["n_unpaired_episodes"] == 1  # 2025-03-03 当天没有基准
    assert ex["baseline_to_signal_ratio"] == pytest.approx(1.9)


def test_aggregate_baseline_sums_by_day_without_dedup() -> None:
    rows = [{**_outcome_row(i, 0.01, 0.013), "symbol": "SAME", "day": "2024-01-02" if i < 3 else "2024-01-03"} for i in range(5)]
    rows.append({"symbol": "X", "day": "2024-01-02", "fill_status": "NO_FILL_5D", "net": None, "gross": None, "outcome": None})
    agg = ev.aggregate_baseline(rows)
    assert agg["2024-01-02"] == pytest.approx([0.03, 3, 3, 0.03 * 2 - 0.039])  # 同一标的相邻日不去重；未成交不计
    assert agg["2024-01-03"][1] == 2


# 开发区全量扫描（509e499）里报 ZeroDivisionError 的全部港股股票-日：下跌中的 ABC 测算位、外推的形态轨道落到 0 或以下，
# 被选作下方支撑 S1，scenarios.py 计算 ref / s1 时除零
ZERO_SUPPORT_CASES = [("00616.HK", "2023-10-20"), ("00616.HK", "2023-10-24"), ("01259.HK", "2025-04-16"),
                      ("01592.HK", "2022-02-22"), ("01592.HK", "2022-02-23")]


def test_non_positive_levels_never_become_support() -> None:
    """价格不为正的测算位、轨道不是有效价位：不进入支撑阶梯，不作 S1，流水线不再除零。"""
    scan.init("HK")
    for sym, day in ZERO_SUPPORT_CASES:
        data = v2._load_symbol(sym)
        t = next(t for d, t, _ in v2._eligible_days(sym, data) if d == day)
        pit, cut = make_pit_slice(data["bars"], t, v2.HORIZON)
        bundle, request = v2.build_bundle(sym, data, pit, cut, day)
        rep = analyze_bundle(bundle, request, include_replay=False, include_forecast=False)
        supports = rep["mathematical_indicators"]["structure_levels"]["key_levels"]["supports"]
        assert all(lvl["price"] > 0 for lvl in supports), (sym, day, supports)
        assert rep["rule_scenarios"]["breakdown_level"]["price"] > 0, (sym, day)
