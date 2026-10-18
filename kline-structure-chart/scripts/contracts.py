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
    if s.startswith(("SINA_", "YAHOO_", "TENCENT_")):
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
    judgment_pack: dict[str, Any]
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


def _check_schema_contract(report: AnalysisReport) -> None:
    """按 assets/analysis.schema.json 校验顶层必填键、schema_version 与 meta 字段（必填、类型、枚举）。

    schema 是唯一信息源：这里不再另写一份必填键清单。只实现该 schema 用到的关键字（required / enum / type / minLength）。
    """
    schema = load_report_schema()
    for key in schema["required"]:
        if key not in report:
            raise ValueError(f"Missing required report key: {key}")
    props = schema["properties"]
    version_enum = props["schema_version"].get("enum")
    if version_enum and report["schema_version"] not in version_enum:
        raise ValueError(f"Unsupported schema_version: {report['schema_version']}")
    meta_schema = props["meta"]
    meta = report["meta"]
    for key in meta_schema.get("required", []):
        if key not in meta:
            raise ValueError(f"Missing required meta key: {key}")
    for key, rule in meta_schema.get("properties", {}).items():
        if key not in meta:
            continue
        value = meta[key]
        if rule.get("type") == "string" and not isinstance(value, str):
            raise ValueError(f"Invalid meta.{key}: expected string, got {type(value).__name__}")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"Invalid meta.{key}: {value!r} not in {rule['enum']}")
        if "minLength" in rule and len(str(value)) < rule["minLength"]:
            raise ValueError(f"Invalid meta.{key}: shorter than {rule['minLength']}")
    for section_key, section_schema in props.items():
        if section_key in ("schema_version", "meta") or section_key not in report:
            continue
        sec_val = report[section_key]
        if section_schema.get("type") == "object" and isinstance(sec_val, dict):
            for req_k in section_schema.get("required", []):
                if req_k not in sec_val:
                    raise ValueError(f"Missing required {section_key} key: {req_k}")


def validate_report(report: AnalysisReport) -> None:
    _check_finite_numbers(report)
    _check_schema_contract(report)

    fp = report["future_projection_15d"]
    horizon_dates = fp.get("horizon_dates", [])
    h_len = len(horizon_dates)
    if h_len <= 0:
        raise ValueError("future_projection_15d.horizon_dates must not be empty")
    for q_key in ("q10", "q25", "base_median_q50", "optimistic_q75", "q90"):
        arr = fp.get(q_key, [])
        if len(arr) != h_len:
            raise ValueError(f"future_projection_15d.{q_key} length {len(arr)} != {h_len}")
        if any(float(v) <= 0 for v in arr):
            raise ValueError(f"future_projection_15d.{q_key} contains non-positive price")

    for step_idx in range(h_len):
        q10 = float(fp["q10"][step_idx])
        q25 = float(fp["q25"][step_idx])
        q50 = float(fp["base_median_q50"][step_idx])
        q75 = float(fp["optimistic_q75"][step_idx])
        q90 = float(fp["q90"][step_idx])
        if not (q10 <= q25 + 1e-6 and q25 <= q50 + 1e-6 and q50 <= q75 + 1e-6 and q75 <= q90 + 1e-6):
            raise ValueError(
                f"Quantile crossing at step {step_idx}: q10={q10}, q25={q25}, q50={q50}, q75={q75}, q90={q90}"
            )

    series = report.get("series_export", {})
    bars = series.get("bars", [])
    if bars:
        validate_bars(bars, "series_export.bars")

