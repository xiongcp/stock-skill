from __future__ import annotations

import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, TypedDict

MarketCode = Literal["CN", "HK", "US"]
AssetType = Literal["index", "equity", "etf"]
AdjustmentType = Literal["raw", "split", "total_return"]
QualityState = Literal["OK", "PARTIAL", "UNAVAILABLE"]
SourceClass = Literal["LIVE", "LOCAL_FILE", "SYNTHETIC", "UNKNOWN"]

SYNTHETIC_SOURCE = "DETERMINISTIC_SYNTHETIC_FIXTURE"


def classify_data_source(source: str | None) -> SourceClass:
    """把数据源标签归类为枚举，质量判定只依赖该枚举而非子串猜测。"""
    s = str(source or "").upper()
    if "SYNTHETIC" in s or "OFFLINE_FIXTURE" in s:
        return "SYNTHETIC"
    if s.startswith(("SINA_", "YAHOO_", "TENCENT_", "CACHED_", "CACHE", "RECORDED_FIXTURE")):
        return "LIVE"
    if s.startswith("LOCAL_FILE"):
        return "LOCAL_FILE"
    return "UNKNOWN"


def assess_data_quality(
    target_source: str | None,
    benchmark_source: str | None,
    needs_benchmark: bool,
    intraday_day: str | None = None,
) -> dict[str, Any]:
    """标的与基准分别判定数据来源：任一为合成数据即 UNAVAILABLE；来源未知或缺少基准为 PARTIAL。"""
    target_cls = classify_data_source(target_source)
    bench_cls = classify_data_source(benchmark_source) if benchmark_source else None
    warnings: list[str] = []
    notes: list[str] = []
    status: QualityState = "OK"
    if target_cls == "SYNTHETIC":
        status = "UNAVAILABLE"
        warnings.append(f"标的行情为合成数据（{target_source}），价格与形态均非真实，不得用于任何真实判断")
    if bench_cls == "SYNTHETIC":
        status = "UNAVAILABLE"
        warnings.append(f"大盘基准为合成数据（{benchmark_source}），基准状态分与相对强弱无效")
    if status != "UNAVAILABLE":
        if target_cls == "UNKNOWN" or bench_cls == "UNKNOWN":
            status = "PARTIAL"
            warnings.append("数据来源无法识别，请人工确认行情真实性")
        if needs_benchmark and bench_cls is None:
            status = "PARTIAL"
            warnings.append("缺少大盘基准行情，regime_score 与相对强弱仅基于标的自身")
    if intraday_day:
        notes.append(f"{intraday_day} 的日线尚未收盘，已排除出指标、形态、风控与预测计算")
    return {
        "status": status,
        "data_source_class": target_cls,
        "benchmark_source": benchmark_source,
        "benchmark_source_class": bench_cls,
        "warnings": warnings,
        "notes": notes,
    }


class AnalysisRequest(TypedDict, total=False):
    market: MarketCode
    symbol: str
    name: str
    asset_type: AssetType
    is_st: bool
    is_ipo_no_limit: bool
    benchmark_symbol: str
    timeframes: list[str]
    as_of: str
    display_bars: int
    structure_bars: int
    horizon_bars: int
    adjustment: AdjustmentType
    account_equity: float | None
    max_risk_pct: float
    color_scheme: Literal["auto", "cn_red_up", "us_green_up"]


class MarketDataBundle(TypedDict, total=False):
    schema_version: str
    market: MarketCode
    target: dict[str, Any]
    benchmark: dict[str, Any] | None
    extra_context: dict[str, Any]
    provenance: dict[str, Any]


class AnalysisReport(TypedDict, total=False):
    schema_version: str
    meta: dict[str, Any]
    request: AnalysisRequest
    market_benchmark: dict[str, Any]
    mathematical_indicators: dict[str, Any]
    algebraic_patterns_detected: list[dict[str, Any]]
    smc_structures: dict[str, Any]
    volume_profile: dict[str, Any]
    market_regime: dict[str, Any]
    strategy_signals: list[dict[str, Any]]
    multi_timeframe_score: dict[str, Any]
    meta_labeling_decision: dict[str, Any]
    triple_barrier_plan: dict[str, Any]
    rule_scenarios: dict[str, Any]
    future_projection_15d: dict[str, Any]
    debate_and_veto: dict[str, Any]
    replay_snapshots: list[dict[str, Any]]
    series_export: dict[str, Any]
    legacy_summary: dict[str, Any]
    rendered_paths: dict[str, str]
    quality: dict[str, Any]
    reproducibility: dict[str, Any]


def _check_finite_numbers(obj: Any, path: str = "root") -> None:
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise ValueError(f"Non-finite float at {path}: {obj}")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _check_finite_numbers(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            _check_finite_numbers(item, f"{path}[{idx}]")


def sanitize_filename(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw.strip())
    cleaned = cleaned.strip("._")
    if not cleaned or ".." in cleaned:
        raise ValueError(f"Unsafe filename token: {raw!r}")
    return cleaned[:80]


def compute_canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def validate_bars(bars: list[dict[str, Any]], label: str = "bars") -> None:
    if not bars:
        raise ValueError(f"{label}: empty bar series")
    prev_day = ""
    for idx, bar in enumerate(bars):
        day = str(bar.get("day", ""))
        if not day:
            raise ValueError(f"{label}[{idx}]: missing day")
        if prev_day and day <= prev_day:
            raise ValueError(f"{label}[{idx}]: non-increasing timestamp {day} after {prev_day}")
        prev_day = day
        o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
        if min(o, h, l, c) <= 0:
            raise ValueError(f"{label}[{idx}]: non-positive OHLC price on {day}")
        if h < max(o, c, l) - 1e-6 or l > min(o, c, h) + 1e-6:
            raise ValueError(f"{label}[{idx}]: invalid OHLC envelope on {day}: O={o}, H={h}, L={l}, C={c}")
        vol = bar.get("volume")
        if vol is not None and float(vol) < 0:
            raise ValueError(f"{label}[{idx}]: negative volume on {day}")


REPORT_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "assets" / "analysis.schema.json"


@lru_cache(maxsize=1)
def load_report_schema() -> dict[str, Any]:
    return json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))


class InsufficientHistoryError(RuntimeError):
    """已收盘K线不足以计算（上市太短或 --as-of 太早）；属于数据不可用的一种，命令行单独以退出码 4 报告。"""


_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict, "array": list, "string": str, "boolean": bool, "null": type(None),
}


def _type_ok(value: Any, json_type: str) -> bool:
    if json_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if json_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _JSON_TYPES[json_type])


def schema_errors(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """按 schema 的 type / enum / minLength / required / properties / additionalProperties:false / items 校验，返回错误列表。"""
    types = schema.get("type")
    if types is not None:
        tlist = types if isinstance(types, list) else [types]
        if not any(_type_ok(value, t) for t in tlist):
            return [f"{path}: 期望 {'/'.join(tlist)}，实际 {type(value).__name__}"]
    errs: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} 不在 {schema['enum']} 中")
    if isinstance(value, str) and len(value) < int(schema.get("minLength", 0)):
        errs.append(f"{path}: 长度小于 {schema['minLength']}")
    if isinstance(value, dict):
        errs += [f"{path}: 缺少必填字段 {k}" for k in schema.get("required", []) if k not in value]
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            errs += [f"{path}: 未声明的字段 {k}" for k in value if k not in props]
        for k, sub in props.items():
            if k in value:
                errs += schema_errors(value[k], sub, f"{path}.{k}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errs += schema_errors(item, schema["items"], f"{path}[{i}]")
    return errs


def validate_report(result: dict[str, Any]) -> None:
    """校验 v6 result.json：无 NaN/Infinity、符合 assets/analysis.schema.json、正常波动范围分位单调且为正。"""
    _check_finite_numbers(result)
    errors = schema_errors(result, load_report_schema())
    if errors:
        raise ValueError("result.json 不符合 v6 契约：" + "；".join(errors[:10]))
    vr = result["volatility_range"]
    h_len = len(vr["horizon_dates"])
    if h_len <= 0:
        raise ValueError("volatility_range.horizon_dates must not be empty")
    keys = ("q10", "q25", "q50", "q75", "q90")
    for key in keys:
        if len(vr[key]) != h_len:
            raise ValueError(f"volatility_range.{key} length {len(vr[key])} != {h_len}")
        if any(float(v) <= 0 for v in vr[key]):
            raise ValueError(f"volatility_range.{key} contains non-positive price")
    for step in range(h_len):
        q = [float(vr[k][step]) for k in keys]
        if any(q[i] > q[i + 1] + 1e-6 for i in range(4)):
            raise ValueError(f"Quantile crossing at step {step}: {dict(zip(keys, q))}")


def validate_chart_data(chart: dict[str, Any]) -> None:
    _check_finite_numbers(chart)
    if chart.get("bars"):
        validate_bars(chart["bars"], "chart_data.bars")
