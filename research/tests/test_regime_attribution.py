"""分状态归因：基准状态表与线上一致、出场日与持有期基准收益、分组守恒、复现核对、覆盖率抽样（真实缓存行情）。"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))

import main_plan_eval as ev  # noqa: E402
import main_plan_scan as scan  # noqa: E402
import regime_attribution as ra  # noqa: E402
import run_dev_region_v2 as v2  # noqa: E402
from common import LONG_ACTIONS, PIT_UNIVERSE  # noqa: E402
from run_backtest import make_pit_slice  # noqa: E402

from analysis import analyze_bundle  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (PIT_UNIVERSE.exists() and ra.REFERENCE.exists()), reason="需要 artifacts/long_backtest 的真实行情缓存与开发区记录"
)


@pytest.mark.parametrize("market", ["CN", "HK", "US"])
def test_bench_state_table_matches_live_pipeline(market: str) -> None:
    """状态表必须与线上流水线在同一股票-日给出的大盘状态逐项相同（每市场约 200 个股票-日）。"""
    scan.init(market)
    table = ra.bench_state_table()
    assert min(table) >= v2.DEV_REGION[0] and max(table) <= v2.DEV_REGION[1]
    symbols = sorted(v2._C["periods"])
    checked = 0
    for sym in symbols[:: max(1, len(symbols) // 8)][:8]:
        data = v2._load_symbol(sym)
        if data is None:
            continue
        days = list(v2._eligible_days(sym, data))
        for day, t, _ in days[:: max(1, len(days) // 25)][:25]:
            pit, cut = make_pit_slice(data["bars"], t, v2.HORIZON)
            bundle, request = v2.build_bundle(sym, data, pit, cut, day)
            mb = analyze_bundle(bundle, request, include_replay=False, include_forecast=False)["market_benchmark"]
            assert (table[day]["regime"], table[day]["score"]) == (mb["regime"], float(mb["regime_score"])), (sym, day)
            checked += 1
    assert checked >= 150


def test_score_bucket_edges() -> None:
    assert [ra.score_bucket(x) for x in (0.0, 34.99, 35.0, 44.99, 45.0, 54.99, 55.0, 69.99, 70.0, 100.0)] == [
        "<35", "<35", "35-45", "35-45", "45-55", "45-55", "55-70", "55-70", ">=70", ">=70"]


def test_reference_points_at_existing_complete_scan() -> None:
    for market in ("CN", "HK", "US"):
        d = ROOT / ra.reference(market)["scan_dir"]
        assert (d / "COMPLETE").exists() and next(ra.read_shard(sorted(d.glob("shard_*.jsonl.gz"))[0]))["sym"]


@pytest.fixture(scope="module")
def cn_shard() -> tuple[list[dict], dict]:
    """CN 第一个分片的真实记录。基准抽样计划只由这一个分片构成：单测只检验口径，不代表全市场数字。"""
    path = sorted((ROOT / ra.reference("CN")["scan_dir"]).glob("shard_*.jsonl.gz"))[0]
    pool: Counter = Counter()
    sig: dict[str, list[dict]] = defaultdict(list)
    for r in ra.read_shard(path):
        pool[r["day"]] += 1
        if r["a"] in LONG_ACTIONS:
            sig[r["day"]].append(r)
    ev._eval_init(False, "CN", "dev", ev.baseline_plan(sig, pool))
    signals, base, _ = ra.eval_shard_with_exit(str(path))
    return signals, base


def test_exit_day_and_bench_hold_return(cn_shard) -> None:
    scan.init("CN")  # 其他测试可能把全局状态切到了别的市场
    signals, _ = cn_shard
    fills = [s for s in signals if s["fill_status"] == "FILL"]
    assert len(fills) >= 30
    days, closes = v2._C["bench_days"], [float(b["close"]) for b in v2._C["bench"]]
    for s in fills:
        assert s["day"] < s["fill_day"] <= s["exit_day"]
        i, j = days.index(s["fill_day"]), days.index(s["exit_day"])  # A 股标的的交易日都是沪深300 的交易日
        assert s["bench_hold_ret"] == pytest.approx(closes[j] / closes[i - 1] - 1.0, abs=1e-12)
    assert all("exit_day" not in s for s in signals if s["fill_status"] != "FILL")


def test_bench_hold_return_on_non_benchmark_days(cn_shard) -> None:
    """成交日或出场日落在基准没有K线的日子（用周日代表）：取前一个基准收盘，不报错。"""
    scan.init("CN")  # 其他测试可能把全局状态切到了别的市场
    days, closes = v2._C["bench_days"], [float(b["close"]) for b in v2._C["bench"]]
    i = next(k for k in range(500, len(days)) if days[k][:4] == "2023" and date.fromisoformat(days[k]).weekday() == 0)
    sunday = (date.fromisoformat(days[i]) - timedelta(days=1)).isoformat()
    assert sunday not in days
    # 周日"成交"：之前最近的基准收盘是 days[i-1]；周日"出场"：当天或之前最近的也是 days[i-1]
    assert ra.bench_hold_return(sunday, days[i + 3]) == pytest.approx(closes[i + 3] / closes[i - 1] - 1.0)
    assert ra.bench_hold_return(days[i - 3], sunday) == pytest.approx(closes[i - 1] / closes[i - 4] - 1.0)


def test_cells_conserve_and_match_frozen_stats(cn_shard) -> None:
    scan.init("CN")  # 其他测试可能把全局状态切到了别的市场
    signals, base = cn_shard
    eps = v2._fill_eps(signals)
    table = ra.bench_state_table()
    groups = ra.attribution_groups(eps, table, base["transplant"])
    total = v2._layer_stats(eps)["n_episodes"]
    for name in ("E1_regime", "E2_score", "X1_bench_sign", "X2_bench_tercile"):
        assert sum(c["n_episodes"] for c in groups[name].values()) == total, name
        assert all(c["n_episodes"] > 0 for c in groups[name].values())  # 空格子不出现
    # 任取一格，用冻结函数在同一子集上直接重算，数字必须相同
    label, cell = next(iter(groups["E1_regime"].items()))
    sub = [r for r in eps if table[r["day"]]["regime"] == label]
    frozen = v2._layer_stats(sub)
    assert (cell["mean_net_return_pct"], cell["net_positive_rate_pct"]) == (
        frozen["mean_net_return_pct"], frozen["net_positive_rate_pct"])
    ex = ev._excess(sub, base["transplant"])
    assert cell["excess_mean_pct"] == (ex["mean_pct"] if ex else None)
    assert cell["insufficient_sample"] == (cell["n_episodes"] < 100 or cell["n_signal_days"] < 30)
    # 三分位：切点有序，三组的持有期基准收益不交叠
    t1, t2 = ra.tercile_cuts([r["bench_hold_ret"] for r in eps])  # 结果里的切点四舍五入到 0.001%，计数要用未舍入的切点
    assert groups["X2_cuts_pct"] == [round(t1 * 100.0, 3), round(t2 * 100.0, 3)]
    assert t1 <= t2 and set(groups["X2_bench_tercile"]) <= {"LOW", "MID", "HIGH"}
    assert sum(1 for r in eps if r["bench_hold_ret"] < t1) == groups["X2_bench_tercile"].get("LOW", {"n_episodes": 0})["n_episodes"]
    up = sum(1 for r in eps if r["bench_hold_ret"] > 0)
    assert groups["X1_bench_sign"].get("UP", {"n_episodes": 0})["n_episodes"] == up


def test_regression_matches_numpy_polyfit(cn_shard) -> None:
    eps = v2._fill_eps(cn_shard[0])
    got = ra.beta_regression(eps)
    x = np.array([r["bench_hold_ret"] for r in eps])
    y = np.array([r["net"] for r in eps])
    slope, icpt = np.polyfit(x, y, 1)
    assert got["n"] == len(eps)
    assert got["slope"] == pytest.approx(slope, abs=1e-4)
    assert got["intercept_pct"] == pytest.approx(icpt * 100.0, abs=1e-3)
    assert got["r2"] == pytest.approx(np.corrcoef(x, y)[0, 1] ** 2, abs=1e-4)
    assert got["slope_date_cluster95"][0] < got["slope_date_cluster95"][1]
    assert got == ra.beta_regression(eps)  # 固定种子，可复现


def test_reproduction_check_rejects_mismatch(cn_shard) -> None:
    signals, base = cn_shard
    eps = v2._fill_eps(signals)
    good = {"summary": {
        "fill_layer": ra._jsonable(v2._layer_stats(eps)),
        "excess_vs_same_day_random": ra._jsonable(ev._excess(eps, base["transplant"])),
    }}
    ra.check_reproduction("CN", eps, base["transplant"], good)  # 一致时不报错
    bad = {"summary": {**good["summary"], "fill_layer": {**good["summary"]["fill_layer"], "n_episodes": len(eps) + 1}}}
    with pytest.raises(RuntimeError, match="未能复现"):
        ra.check_reproduction("CN", eps, base["transplant"], bad)


# 切片按信号日复权时优先取 raw_* 价格，所以篡改未来K线必须连 raw_* 一起改，否则探针是空的
PRICE_KEYS = ("open", "high", "low", "close", "raw_open", "raw_high", "raw_low", "raw_close")


def _first_symbol_with_days(n: int = 5) -> tuple[str, dict, list]:
    for sym in sorted(v2._C["periods"]):
        data = v2._load_symbol(sym)
        if data is not None:
            days = list(v2._eligible_days(sym, data))
            if len(days) >= 200 * n:
                return sym, data, days[::200][:n]
    raise AssertionError("没有找到历史足够长的标的")


def test_coverage_bands_ignore_future_bars() -> None:
    """把信号日之后的K线价格乘以 3：区间必须完全不变（只有用来对答案的实际收盘价会变）。"""
    scan.init("US")
    sym, data, days = _first_symbol_with_days()
    for day, t, _ in days:
        pit, cut, b50, b80 = ra.bands_at(sym, data, t, day)
        assert pit[cut]["day"] == day and len(b50) == len(b80) == v2.HORIZON
        bad = {**data, "bars": data["bars"][: t + 1] + [
            {**b, **{k: float(b[k]) * 3.0 for k in PRICE_KEYS if k in b}} for b in data["bars"][t + 1:]]}
        pit2, cut2, c50, c80 = ra.bands_at(sym, bad, t, day)
        assert (b50, b80) == (c50, c80), (sym, day)
        assert float(pit2[cut2 + 5]["close"]) == pytest.approx(float(pit[cut + 5]["close"]) * 3.0)


def test_coverage_sampling_and_record() -> None:
    scan.init("CN")
    path = sorted((ROOT / ra.reference("CN")["scan_dir"]).glob("shard_*.jsonl.gz"))[0]
    rows = list(ra.read_shard(path))
    picked = [r for r in rows if ra.coverage_sampled(r["sym"], r["day"])]
    assert 0.01 < len(picked) / len(rows) < 0.03  # 约 2%
    assert [ra.coverage_sampled(r["sym"], r["day"]) for r in rows[:50]] == [ra.coverage_sampled(r["sym"], r["day"]) for r in rows[:50]]
    recs = ra.coverage_shard(str(path))
    assert len(recs) == len(picked)
    ok = [r for r in recs if "__error__" not in r]
    assert len(ok) >= 0.95 * len(recs)
    for r in ok[:20]:
        # 80% 区间包含 50% 区间：落在 50% 区间内的一定也落在 80% 区间内
        assert all((not r[f"in50_{s}"]) or r[f"in80_{s}"] for s in (5, 10, 15))
    cells = ra.coverage_cells(ok, ra.bench_state_table())
    assert cells["ALL"]["ALL"]["n_samples"] == len(ok)
    assert sum(c["n_samples"] for c in cells["E1_regime"].values()) == len(ok)
    assert sum(c["n_samples"] for c in cells["E2_score"].values()) == len(ok)
    c = cells["ALL"]["ALL"]
    assert c["cover50_step5_pct"] <= c["cover80_step5_pct"]
    assert c["insufficient_sample"] == (c["n_samples"] < 300 or c["n_days"] < 30)


def test_render_markdown_uses_only_file_numbers(cn_shard) -> None:
    scan.init("CN")  # 其他测试可能把全局状态切到了别的市场
    signals, base = cn_shard
    eps = v2._fill_eps(signals)
    table = ra.bench_state_table()
    result = {"generated_at_utc": "T", "code_commit": "abc", "markets": {"CN": {"attribution": {
        "overall": ra.cell_stats(eps, base["transplant"]),
        "groups": ra.attribution_groups(eps, table, base["transplant"]),
        "beta_regression": ra.beta_regression(eps),
    }}}}
    md = ra.render_markdown(result)
    o = result["markets"]["CN"]["attribution"]["overall"]
    assert "#### CN" in md and f"| 全部 | {o['n_episodes']} |" in md
    assert "样本内" in md and "事后变量" in md
    small = [c for g, cells in result["markets"]["CN"]["attribution"]["groups"].items() if g != "X2_cuts_pct"
             for c in cells.values() if c["insufficient_sample"]]
    assert ("样本不足" in md) == bool(small)
