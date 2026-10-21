"""前向跟踪 H2：增量合并、按日记账、到期打分、只报数量与一次性验收守卫（真实缓存行情，不联网）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))

import h2_forward as h2  # noqa: E402
import run_dev_region_v2 as v2  # noqa: E402
from common import PIT_UNIVERSE, load_cache  # noqa: E402

pytestmark = pytest.mark.skipif(not PIT_UNIVERSE.exists(), reason="需要 artifacts/long_backtest 真实行情缓存")


@pytest.fixture()
def h2_tmp(tmp_path, monkeypatch):
    for name in ("SIGNALS", "BASELINE", "OUTCOMES", "GAPS", "RUNS", "RESULT"):
        monkeypatch.setattr(h2, name, tmp_path / getattr(h2, name).name)
    monkeypatch.setattr(v2, "H2_UNIVERSE", tmp_path / "pit_universe_h2.json")
    return tmp_path


def test_merge_bars_requires_matching_overlap() -> None:
    bars = load_cache("CN", "600519.SH")["bars"]
    assert h2.merge_bars(bars[:-10], bars[-30:]) == bars
    changed = [dict(b) for b in bars[-30:]]
    changed[5]["close"] = changed[5]["close"] * 1.5  # 例如拆股后数据源整体调整了历史价格
    assert h2.merge_bars(bars[:-10], changed) is None
    assert h2.merge_bars(bars[:-40], bars[-30:]) is None  # 没有重叠：中间缺数，必须整段重抓


def test_log_market_records_latest_day_only_once_and_notes_gaps(h2_tmp, monkeypatch) -> None:
    """用缓存最后一天（2026-10-19，不属于开发区、H1 或 H2）演练记账：只记一次，信号写入时不含任何结果字段。"""
    market = "US"
    last_day = load_cache(market, "SPY.US")["bars"][-1]["day"]
    monkeypatch.setattr(v2, "H2_FIRST_DAY", last_day)
    uni = h2.load_universe()
    h2.save_universe(uni)
    full_pool = h2.pool_on(uni, market, last_day)
    monkeypatch.setattr(h2, "pool_on", lambda u, m, d: full_pool[:60])
    res = h2.log_market(market, uni, workers=2, prereg_sha="test", commit="test")
    assert res["status"] == "LOGGED" and res["day"] == last_day and res["with_bar"] > 0
    assert h2.log_market(market, uni, workers=2, prereg_sha="test", commit="test")["status"] == "ALREADY_LOGGED"
    for row in h2._read(h2.SIGNALS) + h2._read(h2.BASELINE):
        assert row["day"] == last_day and "written_at_utc" in row
        assert not {"net", "outcome", "fill_status"} & set(row)
    # 上一次记账日之后错过的交易日只记缺失
    h2._append(h2.RUNS, {"market": "CN", "day": "2026-10-12"})
    monkeypatch.setattr(v2, "H2_FIRST_DAY", "2026-10-12")
    cn_pool = h2.pool_on(uni, "CN", "2026-10-19")
    monkeypatch.setattr(h2, "pool_on", lambda u, m, d: cn_pool[:5])
    h2.log_market("CN", uni, workers=1, prereg_sha="test", commit="test")
    gaps = [g["day"] for g in h2._read(h2.GAPS) if g["market"] == "CN"]
    assert gaps and min(gaps) > "2026-10-12" and max(gaps) < h2._read(h2.RUNS)[-1]["day"]


def test_score_only_matured_entries(h2_tmp) -> None:
    """到期（满 5 + 15 个交易日）才打分；用开发区内的一个交易日演练，最后一天的条目不打分。"""
    market, symbol = "US", "AAPL.US"
    v2._init(market, mode="dev")
    data = v2._load_symbol(symbol)
    old_day, new_day = data["bars"][-200]["day"], data["bars"][-1]["day"]
    c = data["c"][-200]
    entries = [{"id": f"B|{market}|{symbol}|{d}", "market": market, "symbol": symbol, "day": d,
                "entry_high": c * 1.01, "tp": c * 1.05, "sl": c * 0.9} for d in (old_day, new_day)]
    out = h2._score_symbol((symbol, entries))
    assert [r["id"] for r in out] == [entries[0]["id"]]
    assert out[0]["fill_status"] in {"FILL", "NO_FILL_5D", "GAP_BELOW_STOP"} or out[0]["fill_status"].startswith("NOT_TRADABLE")


def test_status_reports_counts_only_and_evaluate_guards(h2_tmp) -> None:
    st = h2.status()
    assert st["ready_for_evaluation"] is False
    for m in h2.MARKETS:
        assert set(st[m]) == {"days_logged", "days_missed", "signals_logged", "signals_kept", "matured_episodes",
                              "matured_signal_days", "p3_reached"}
    with pytest.raises(SystemExit):
        h2.evaluate({"acceptance": {"acceptance_markets": ["CN", "US"], "gates": ["P1", "P2b", "P3"]}})
    h2.RESULT.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        h2.evaluate({})
    assert "只能运行一次" in str(e.value)


def test_h2_prereg_reuses_v2_rules() -> None:
    pre = h2.build_h2_prereg()
    v2_pre = json.loads((ROOT / "research" / "preregistration" / "preregistration_v2.json").read_text(encoding="utf-8"))
    assert pre["acceptance"]["gates"] == v2_pre["acceptance"]["gates"] == ["P1", "P2b", "P3"]
    assert pre["rules_from"]["frozen_specification"] == v2_pre["frozen_specification"]
    assert "research/h2_forward.py" in pre["frozen_files_sha256"]
    assert pre["h2"]["first_day"] == v2.H2_FIRST_DAY
