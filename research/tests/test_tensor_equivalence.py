"""张量重测引擎与线上 label_triple_barrier_event 的逐笔等价性（真实缓存行情，float64）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))

from common import adjusted_bars, load_cache  # noqa: E402
from evaluation import label_triple_barrier_event  # noqa: E402
from fast_tensor_backtest import OUTCOMES, dump_paths, vectorized_label  # noqa: E402
from run_backtest import make_pit_slice  # noqa: E402

SHARD_ROOT = ROOT / "artifacts" / "long_backtest" / "runs" / "baseline_v1"


def _signals(market: str, limit: int) -> list[dict]:
    """跨全部分片等间隔抽样，避免只取到字典序靠前的少数股票。"""
    rows: list[dict] = []
    shards = sorted((SHARD_ROOT / market / "shards").glob("shard_*.jsonl"))
    per_shard = max(1, limit // max(1, len(shards)) + 1)
    for sp in shards:
        got = []
        for line in sp.open(encoding="utf-8"):
            r = json.loads(line)
            if not r.get("__error__") and r.get("entry_zone"):
                got.append(r)
        step = max(1, len(got) // per_shard)
        rows.extend(got[::step][:per_shard])
    step = max(1, len(rows) // limit)
    return rows[::step][:limit]


@pytest.mark.parametrize("market,limit", [("CN", 600), ("HK", 300), ("US", 300)])
def test_tensor_vs_live_equivalence(market: str, limit: int) -> None:
    rows = _signals(market, limit)
    if not rows:
        pytest.skip("缺少 baseline_v1 分片（artifacts 不随仓库分发）")
    cache: dict[str, tuple[dict, list[dict], dict[str, int]]] = {}
    pit_bars, entry_idx, tp, sl, syms, names, st = [], [], [], [], [], [], []
    live = []
    for r in rows:
        sym = r["symbol"]
        if sym not in cache:
            payload = load_cache(market, sym)
            _, bars = adjusted_bars(payload, mode="total_return")
            cache[sym] = (payload, bars, {b["day"]: i for i, b in enumerate(bars)})
        payload, bars, idx = cache[sym]
        if r["day"] not in idx:  # 旧分片在占位K线上产生的信号，剔除后该日已不存在
            continue
        pit, cut = make_pit_slice(bars, idx[r["day"]], 15)
        name = str(payload.get("name") or "")
        pit_bars.append(pit)
        entry_idx.append(cut + 1)
        tp.append(float(r["tp1"]))
        sl.append(float(r["stop"]))
        syms.append(sym)
        names.append(name)
        st.append(False)
        live.append(
            label_triple_barrier_event(
                pit, entry_idx=cut + 1, tp_price=float(r["tp1"]), sl_price=float(r["stop"]), horizon_bars=15,
                market=market, asset_type="equity", symbol=sym, is_st=False, is_ipo_no_limit=False,
                trailing_stop=True, trigger_r=0.90, confirm_close_r=0.30, lock_r=0.08, name=name,
            )
        )
    tensors = dump_paths(pit_bars, entry_idx, tp, sl, market, syms, names, st)
    assert tensors["open"].dtype == np.float64 and tensors["entry_price"].dtype == np.float64
    vec = vectorized_label(tensors)
    mism = []
    for i, lv in enumerate(live):
        if not lv.get("tradable"):
            if bool(vec["tradable"][i]):
                mism.append((i, "tradable", lv.get("reason"), OUTCOMES[int(vec["outcome"][i])]))
            continue
        if (
            OUTCOMES[int(vec["outcome"][i])] != lv["outcome"]
            or abs(float(vec["net_return"][i]) - lv["net_return"]) > 1e-4  # live 侧四舍五入到 5 位小数
            or int(vec["holding_bars"][i]) != lv["holding_bars"]
        ):
            mism.append((i, lv["outcome"], lv["net_return"], OUTCOMES[int(vec["outcome"][i])], float(vec["net_return"][i])))
    assert not mism, f"{market} 张量与线上不一致 {len(mism)}/{len(live)}: {mism[:5]}"
