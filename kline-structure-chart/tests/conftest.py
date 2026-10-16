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
    """把新浪 / Yahoo 取数函数替换为录制数据回放，模拟真实联网运行但不访问网络。"""

    def sina(code: str, datalen: int = 450) -> list[dict]:
        return [dict(b) for b in load_recorded(f"sina_daily/{code}.json")][-datalen:]

    def yahoo(symbol: str, range_str: str = "2y") -> list[dict]:
        if symbol == "^VIX":
            raise OSError("VIX not recorded")
        return [dict(b) for b in load_recorded(f"yahoo_daily/{symbol.replace('^', '')}.json")]

    def events(symbol: str, range_str: str = "5y") -> list[dict]:
        return load_recorded(f"yahoo_events/{symbol}.json")

    monkeypatch.setattr(data_gateway, "fetch_sina_daily_bars", sina)
    monkeypatch.setattr(data_gateway, "fetch_yahoo_daily_bars", yahoo)
    monkeypatch.setattr(data_gateway, "fetch_yahoo_corporate_actions", events)
