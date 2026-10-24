from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
RECORDED_DIR = Path(__file__).resolve().parent / "fixtures" / "recorded"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import data_gateway  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试一律不访问真实网络：未被单独 mock 的取数函数会直接失败，避免测试结果依赖外部行情。"""

    def blocked(url: str, *_args, **_kwargs) -> str:
        raise OSError(f"network disabled in tests: {url[:60]}")

    monkeypatch.setattr(data_gateway, "_http_get", blocked)


def load_recorded(rel: str) -> list[dict]:
    """读取 tests/record_fixtures.py 录制的真实行情（只含已收盘K线）。"""
    return json.loads((RECORDED_DIR / rel).read_text(encoding="utf-8"))


@pytest.fixture
def replay_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """把新浪 / Yahoo / 腾讯取数函数替换为真实录制数据回放，模拟真实联网运行但不访问外网。"""

    def sina(code: str, datalen: int = 450) -> list[dict]:
        return [dict(b) for b in load_recorded(f"sina_daily/{code}.json")][-datalen:]

    def yahoo(symbol: str, range_str: str = "5y", datalen: int | None = data_gateway.FETCH_BARS) -> list[dict]:
        bars = [dict(b) for b in load_recorded(f"yahoo_daily/{symbol.replace('^', '')}.json")]
        if datalen is not None:
            bars = bars[-datalen:]
        return bars

    def tencent(code: str, datalen: int = 760) -> list[dict]:
        return [dict(b) for b in load_recorded(f"tencent_daily/{code}.json")][-datalen:]

    def events(symbol: str, range_str: str = "5y") -> list[dict]:
        return [dict(e) for e in load_recorded(f"yahoo_events/{symbol}.json")]

    def quote(code: str) -> dict:
        path = RECORDED_DIR / "tencent_quote" / f"{code}.json"
        if not path.exists():
            raise OSError(f"quote not recorded: {code}")
        return json.loads(path.read_text(encoding="utf-8"))

    monkeypatch.setattr(data_gateway, "fetch_sina_daily_bars", sina)
    monkeypatch.setattr(data_gateway, "fetch_yahoo_daily_bars", yahoo)
    monkeypatch.setattr(data_gateway, "fetch_tencent_index_daily_bars", tencent)
    monkeypatch.setattr(data_gateway, "fetch_yahoo_corporate_actions", events)
    monkeypatch.setattr(data_gateway, "fetch_tencent_quote_meta", quote)


def load_v6(report: dict) -> tuple[dict, dict]:
    """读取 run_pipeline 写出的 v6 result.json 与 chart_data.json。"""
    paths = report["rendered_paths"]
    return (
        json.loads(Path(paths["result_json"]).read_text(encoding="utf-8")),
        json.loads(Path(paths["chart_data_json"]).read_text(encoding="utf-8")),
    )
