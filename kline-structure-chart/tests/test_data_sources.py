"""数据源可靠性：录制真实行情回放的端到端测试 + 网络/厂商数据异常的 mock 测试。"""
from __future__ import annotations

import json
import socket
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import load_recorded

import data_gateway
from analyze_and_forecast import run_pipeline
from contracts import classify_data_source
from data_gateway import load_bundle

UTC = timezone.utc
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)  # 录制数据最后一根为 2026-10-15，均已收盘


# ---------------------------------------------------------------------------
# 录制真实行情回放（不访问网络）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("market", "symbol", "asset_type", "recorded", "bench_source"),
    [
        ("CN", "600519.SH", "equity", "sina_daily/sh600519.json", "SINA_LIVE_DAILY"),
        ("US", "NVDA.US", "equity", "yahoo_daily/NVDA.json", "YAHOO_DAILY(SPY)"),
        ("HK", "00700.HK", "equity", "yahoo_daily/0700.HK.json", "YAHOO_DAILY(^HSI)"),
    ],
)
def test_pipeline_on_recorded_real_data(
    replay_recorded, tmp_path: Path, market: str, symbol: str, asset_type: str, recorded: str, bench_source: str
) -> None:
    raw_last = load_recorded(recorded)[-1]
    report = run_pipeline(
        {"market": market, "symbol": symbol, "asset_type": asset_type, "display_bars": 120, "horizon_bars": 15,
         "account_equity": 500000.0},
        output_dir=tmp_path,
        run_backtest=True,
        backtest_step=10,
    )
    q = report["quality"]
    assert q["status"] == "OK" and q["warnings"] == []
    assert q["data_source_class"] == "LIVE" and q["benchmark_source"] == bench_source
    assert report["meta"]["as_of"] == raw_last["day"]
    # 复权以录制的除权除息事件为准，最后一根（除息日之后）等于原始收盘价
    assert report["meta"]["adjustment"] == "total_return"
    assert report["series_export"]["bars"][-1]["close"] == pytest.approx(raw_last["close"], abs=0.01)
    assert report["mathematical_indicators"]["trend"]["ema200"] is not None
    assert report["future_projection_15d"]["calibration_audit"]["calibrated"] is True
    bt = json.loads(Path(report["rendered_paths"]["backtest_json"]).read_text(encoding="utf-8"))
    assert bt["backtest"]["method"] == "LIVE_PLAN_REPLAY_V6"


def test_recorded_fixtures_are_real_and_documented() -> None:
    manifest = json.loads((Path(__file__).parent / "fixtures" / "recorded" / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["recorded_at_utc"].startswith("2026-")
    bars = load_recorded("sina_daily/sh000001.json")
    days = {b["day"] for b in bars}
    # 真实交易所休市日不应出现在录制数据里（合成数据曾在这些日子生成K线）
    assert not days & {"2025-06-02", "2026-02-23", "2025-10-01"}
    assert len({b["volume"] for b in bars[-5:]}) > 1


# ---------------------------------------------------------------------------
# 网络与厂商数据异常（全部 mock，不访问网络）
# ---------------------------------------------------------------------------
REQ_CN = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity"}
REQ_US = {"symbol": "NVDA.US", "market": "US", "asset_type": "equity"}


def _http(responses: dict[str, object]):
    """按 URL 片段返回固定文本或抛出异常的假 _http_get。"""

    def fake(url: str, *_args, **_kwargs) -> str:
        for key, val in responses.items():
            if key in url:
                if isinstance(val, BaseException):
                    raise val
                return val  # type: ignore[return-value]
        raise OSError(f"unexpected url {url}")

    return fake


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("null", "unavailable"),
        ("[]", "unavailable"),
        ("<html>blocked</html>", "JSONDecodeError"),
        (urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None), "429"),
        (socket.timeout("timed out"), "timed out"),
        (urllib.error.URLError("dns failure"), "dns failure"),
    ],
)
def test_sina_failures_raise_with_reason(monkeypatch: pytest.MonkeyPatch, body: object, reason: str) -> None:
    monkeypatch.setattr(data_gateway, "_http_get", _http({"sina.com.cn": body}))
    with pytest.raises(RuntimeError) as exc:
        load_bundle(REQ_CN, now=NOW)
    assert reason in str(exc.value) and "拒绝使用合成数据" in str(exc.value)


def test_sina_corrupt_ohlc_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"day": b["day"], "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"], "volume": b["volume"]}
        for b in load_recorded("sina_daily/sh600519.json")[-300:]
    ]
    rows[150]["high"] = rows[150]["low"] * 0.5  # 最高价低于最低价
    monkeypatch.setattr(data_gateway, "_http_get", _http({"sina.com.cn": json.dumps(rows)}))
    with pytest.raises(RuntimeError, match="invalid OHLC envelope"):
        load_bundle(REQ_CN, now=NOW)


def test_too_short_history_after_as_of_is_rejected(replay_recorded) -> None:
    first_days = load_recorded("sina_daily/sh600519.json")
    with pytest.raises(RuntimeError, match="历史长度不足"):
        load_bundle({**REQ_CN, "as_of": first_days[50]["day"]}, now=NOW)


def test_benchmark_failure_blocks_analysis(monkeypatch: pytest.MonkeyPatch, replay_recorded) -> None:
    original = data_gateway.fetch_sina_daily_bars

    def target_only(code: str, datalen: int = 450) -> list[dict]:
        if code == "sh000300":
            raise OSError("benchmark endpoint down")
        return original(code, datalen)

    monkeypatch.setattr(data_gateway, "fetch_sina_daily_bars", target_only)
    with pytest.raises(RuntimeError, match="000300.SH.*benchmark endpoint down"):
        load_bundle(REQ_CN, now=NOW)


@pytest.mark.parametrize(
    "body",
    [
        json.dumps({"chart": {"result": None, "error": {"code": "Not Found"}}}),
        json.dumps({"chart": {"result": []}}),
        urllib.error.HTTPError("u", 404, "Not Found", {}, None),
    ],
)
def test_yahoo_failures_raise_with_reason(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    monkeypatch.setattr(data_gateway, "_http_get", _http({"yahoo.com": body}))
    with pytest.raises(RuntimeError, match="无法获取 NVDA.US 的真实行情"):
        load_bundle(REQ_US, now=NOW)


def test_yahoo_null_and_nonpositive_rows_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = load_recorded("yahoo_daily/NVDA.json")[-200:]
    ts0 = 1_700_000_000
    quote = {k: [b[k] for b in bars] for k in ("open", "high", "low", "close", "volume")}
    quote["close"][10] = None
    quote["open"][20] = -1.0
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"exchangeTimezoneName": "America/New_York"},
                    "timestamp": [ts0 + i * 86400 + 14 * 3600 for i in range(len(bars))],
                    "indicators": {"quote": [quote]},
                }
            ]
        }
    }
    monkeypatch.setattr(data_gateway, "_http_get", _http({"yahoo.com": json.dumps(payload)}))
    rows = data_gateway.fetch_yahoo_daily_bars("NVDA")
    assert len(rows) == len(bars) - 2
    assert all(min(r["open"], r["high"], r["low"], r["close"]) > 0 for r in rows)


def test_tencent_index_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(data_gateway, "_http_get", _http({"gtimg.cn": json.dumps({"data": {"hkHSTECH": {"day": []}}})}))
    with pytest.raises(RuntimeError, match="Tencent daily kline empty"):
        load_bundle({"symbol": "HSTECH.HK", "market": "HK", "asset_type": "index"}, now=NOW)


def test_vix_and_events_failures_degrade_with_disclosure(monkeypatch: pytest.MonkeyPatch, replay_recorded) -> None:
    orig_yahoo = data_gateway.fetch_yahoo_daily_bars

    def yahoo_no_vix(symbol: str, range_str: str = "2y") -> list[dict]:
        if symbol == "^VIX":
            raise urllib.error.HTTPError("u", 503, "VIX Unavailable", {}, None)
        return orig_yahoo(symbol, range_str)

    def events_down(symbol: str, range_str: str = "5y") -> list[dict]:
        raise urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(data_gateway, "fetch_yahoo_daily_bars", yahoo_no_vix)
    monkeypatch.setattr(data_gateway, "fetch_yahoo_corporate_actions", events_down)
    bundle = load_bundle(REQ_US, now=NOW)
    assert bundle["extra_context"]["vix"] is None and bundle["extra_context"]["vix_source"].startswith("UNAVAILABLE")
    status = bundle["provenance"]["adjustment_status"]
    assert status["applied"] == "raw" and "503" in status["error"]
    assert classify_data_source(bundle["provenance"]["source"]) == "LIVE"
