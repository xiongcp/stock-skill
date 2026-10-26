from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
RECORDED_DIR = Path(__file__).resolve().parent / "fixtures" / "recorded"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import analyze_and_forecast  # noqa: E402
import data_gateway  # noqa: E402
import render_structure_chart  # noqa: E402

_PIPELINE_SESSION_CACHE: dict[tuple, tuple[dict, Path]] = {}
_ORIG_RUN_PIPELINE = analyze_and_forecast.run_pipeline


@pytest.fixture(autouse=True)
def _fast_ssaa_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试环境默认按 1x 原生分辨率 (1920x1160) 渲染 PNG，跳过 3840x2320 LANCZOS 超采样耗时。"""
    monkeypatch.setattr(render_structure_chart, "SSAA", 1)


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
def replay_recorded(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
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

    orig_hooks = (
        sina,
        yahoo,
        tencent,
        events,
        quote,
        analyze_and_forecast.build_result,
        analyze_and_forecast.build_chart_data,
        analyze_and_forecast.render_png_chart,
    )

    fixture_patch_count = [0]

    def cached_run_pipeline(
        request: dict,
        output_dir: Path,
        input_path: Path | None = None,
        allow_network: bool = True,
        run_backtest: bool = False,
        backtest_step: int = 1,
    ) -> dict:
        current_hooks = (
            data_gateway.fetch_sina_daily_bars,
            data_gateway.fetch_yahoo_daily_bars,
            data_gateway.fetch_tencent_index_daily_bars,
            data_gateway.fetch_yahoo_corporate_actions,
            data_gateway.fetch_tencent_quote_meta,
            analyze_and_forecast.build_result,
            analyze_and_forecast.build_chart_data,
            analyze_and_forecast.render_png_chart,
        )
        # 用例在本夹具之后又 monkeypatch 了任何东西（如 test_g02 让渲染器重算结构层时报错）时不能走缓存，
        # 否则被改写的函数根本不会执行，守护测试会随用例顺序静默失效
        patched_after_fixture = len(monkeypatch._setattr) != fixture_patch_count[0]
        if input_path is not None or not allow_network or current_hooks != orig_hooks or patched_after_fixture:
            return _ORIG_RUN_PIPELINE(
                request,
                output_dir=output_dir,
                input_path=input_path,
                allow_network=allow_network,
                run_backtest=run_backtest,
                backtest_step=backtest_step,
            )
        norm_req = {k: v for k, v in request.items() if v is not None}
        key = (json.dumps(norm_req, sort_keys=True, ensure_ascii=False), bool(run_backtest), int(backtest_step))
        if key not in _PIPELINE_SESSION_CACHE:
            cache_dir = tmp_path_factory.mktemp("pipeline_cache")
            rep = _ORIG_RUN_PIPELINE(
                copy.deepcopy(request),
                output_dir=cache_dir,
                input_path=None,
                allow_network=True,
                run_backtest=run_backtest,
                backtest_step=backtest_step,
            )
            _PIPELINE_SESSION_CACHE[key] = (copy.deepcopy(rep), cache_dir)
        cached_rep, cache_dir = _PIPELINE_SESSION_CACHE[key]
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        rep_copy = copy.deepcopy(cached_rep)
        new_paths: dict[str, str] = {}
        for k, src_str in cached_rep["rendered_paths"].items():
            src_p = Path(src_str)
            dst_p = out_dir / src_p.name
            if src_p.resolve() != dst_p.resolve():
                shutil.copy2(src_p, dst_p)
            new_paths[k] = str(dst_p.resolve())
        rep_copy["rendered_paths"] = new_paths
        return rep_copy

    monkeypatch.setattr(analyze_and_forecast, "run_pipeline", cached_run_pipeline)
    for mod_name in list(sys.modules):
        mod = sys.modules.get(mod_name)
        if mod is not None and "test_" in mod_name and hasattr(mod, "run_pipeline"):
            monkeypatch.setattr(mod, "run_pipeline", cached_run_pipeline)
    fixture_patch_count[0] = len(monkeypatch._setattr)


def load_v6(report: dict) -> tuple[dict, dict]:
    """读取 run_pipeline 写出的 v6 result.json 与 chart_data.json。"""
    paths = report["rendered_paths"]
    return (
        json.loads(Path(paths["result_json"]).read_text(encoding="utf-8")),
        json.loads(Path(paths["chart_data_json"]).read_text(encoding="utf-8")),
    )
