"""指标金标准对照：在录制的真实行情上与 TA-Lib 逐点比对。

- SMA / EMA / RSI / ATR / 布林带 / 随机指标：首个有效下标与全部数值都必须与 TA-Lib 一致。
- MACD 与 ADX/±DI：初值播种口径不同（TA-Lib 的 MACD 快线与慢线对齐起点、ADX 用 period-1 根求和播种），
  前段存在收敛差异；只要求预热之后（最近 600 根）逐点一致。
"""
from __future__ import annotations

import numpy as np
import pytest
from conftest import load_recorded

import indicators as ind

talib = pytest.importorskip("talib")

DATASETS = ("sina_daily/sh600519.json", "sina_daily/sh000300.json", "yahoo_daily/NVDA.json", "yahoo_daily/0700.HK.json")


def _arr(values: list[float | None]) -> np.ndarray:
    return np.array([np.nan if v is None else float(v) for v in values], dtype=float)


def _assert_same(ours: list[float | None], ref: np.ndarray, tail: int | None = None, rtol: float = 1e-9) -> None:
    got = _arr(ours)
    if tail is None:
        assert int(np.argmax(~np.isnan(got))) == int(np.argmax(~np.isnan(ref))), "首个有效下标不一致"
        mask = ~np.isnan(ref)
        assert np.isnan(got[~mask]).all()
        np.testing.assert_allclose(got[mask], ref[mask], rtol=rtol, atol=1e-9)
    else:
        np.testing.assert_allclose(got[-tail:], ref[-tail:], rtol=rtol, atol=1e-9)


@pytest.fixture(params=DATASETS)
def ohlc(request: pytest.FixtureRequest) -> tuple[list[dict], np.ndarray, np.ndarray, np.ndarray]:
    bars = load_recorded(request.param)
    h, l, c = (np.array([float(b[k]) for b in bars]) for k in ("high", "low", "close"))
    return bars, h, l, c


def test_exact_match_indicators(ohlc) -> None:
    bars, h, l, c = ohlc
    closes = list(c)
    _assert_same(ind.sma(closes, 20), talib.SMA(c, 20))
    _assert_same(ind.ema(closes, 20), talib.EMA(c, 20))
    _assert_same(ind.ema(closes, 200), talib.EMA(c, 200))
    _assert_same(ind.rsi(closes, 14), talib.RSI(c, 14))
    _assert_same(ind.atr_series(bars, 14), talib.ATR(h, l, c, 14))
    bb = ind.bollinger_bands(closes, 20, 2.0)
    up, mid, lo = talib.BBANDS(c, 20, 2.0, 2.0, 0)
    _assert_same(bb["upper"], up)
    _assert_same(bb["mid"], mid)
    _assert_same(bb["lower"], lo)
    st = ind.stochastic(bars, 14, 3, 3)
    slow_k, slow_d = talib.STOCH(h, l, c, 14, 3, 0, 3, 0)
    _assert_same(st["k"], slow_k, tail=len(c) - 20)
    _assert_same(st["d"], slow_d)


def test_converged_match_indicators(ohlc) -> None:
    bars, h, l, c = ohlc
    m = ind.macd(list(c), 12, 26, 9)
    dif, dea, hist = talib.MACD(c, 12, 26, 9)
    _assert_same(m["macd"], dif, tail=600, rtol=1e-7)
    _assert_same(m["signal"], dea, tail=600, rtol=1e-7)
    _assert_same(m["hist"], hist, tail=600, rtol=1e-6)
    a = ind.adx(bars, 14)
    _assert_same(a["adx"], talib.ADX(h, l, c, 14), tail=600, rtol=1e-7)
    _assert_same(a["plus_di"], talib.PLUS_DI(h, l, c, 14), tail=600, rtol=1e-7)
    _assert_same(a["minus_di"], talib.MINUS_DI(h, l, c, 14), tail=600, rtol=1e-7)


def test_supertrend_matches_wilder_atr_ratchet_reference(ohlc) -> None:
    """以 TA-Lib Wilder ATR(14) 为基准独立推导 Supertrend(14, 3.0) 上下轨棘轮与方向翻转，逐点核对。"""
    bars, h, l, c = ohlc
    atr_ref = talib.ATR(h, l, c, 14)
    got = ind.supertrend(bars, period=14, multiplier=3.0)
    n = len(bars)
    ref_line = np.full(n, np.nan)
    ref_dir: list[int | None] = [None] * n
    final_u = final_l = 0.0
    cur_d = 1
    for i in range(n):
        if np.isnan(atr_ref[i]):
            continue
        hl2 = (h[i] + l[i]) / 2.0
        bu = hl2 + 3.0 * float(atr_ref[i])
        bl = hl2 - 3.0 * float(atr_ref[i])
        if i == 0 or np.isnan(ref_line[i - 1]):
            final_u, final_l = bu, bl
            cur_d = 1 if c[i] >= hl2 else -1
        else:
            if bu < final_u or c[i - 1] > final_u:
                final_u = bu
            if bl > final_l or c[i - 1] < final_l:
                final_l = bl
            if cur_d == 1 and c[i] < final_l:
                cur_d = -1
            elif cur_d == -1 and c[i] > final_u:
                cur_d = 1
        ref_dir[i] = cur_d
        ref_line[i] = final_l if cur_d == 1 else final_u
    _assert_same(got["line"], ref_line)
    assert got["direction"] == ref_dir
