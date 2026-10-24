"""L1 多情景结构引擎：8 类规则情景库（与 references/scenario-playbook.md 一一对应）。

职责：
1. 定义与文档严格同步的 8 个标准情景元数据 SCENARIO_PLAYBOOK；
2. 识别推动 5 浪（检验浪 2 不破浪 1 起点、浪 3 非最短、浪 4 不重叠浪 1 价格区间）；
3. 根据近 120 日结构（轨道、浪型计数、阶梯、RSI14、RVOL20、收敛顶点）筛选当前截面适用的 2~5 个情景；
4. 触发位距最新收盘价不足 0.5 个 ATR 时归为「已在进行中」(status="ACTIVE")，不再作为待触发条件；
"""
from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

MIN_TRIGGER_ATR = 0.5

# 与 references/scenario-playbook.md 表格逐字一一对应（由 test_docs_contract 守护）
SCENARIO_PLAYBOOK: tuple[dict[str, str], ...] = (
    {
        "id": "TREND_CONTINUATION",
        "name": "趋势延续",
        "precondition": "通道或推动浪 3/5 进行中，收盘在通道中轨之上",
        "target_source": "通道上轨外推 / 浪 3 = 1.618×浪 1",
        "invalidation": "收盘跌破通道下轨",
    },
    {
        "id": "BREAKOUT_RETEST",
        "name": "突破回踩",
        "precondition": "刚收盘突破轨道或颈线 ≤ 5 根",
        "target_source": "形态高度测量",
        "invalidation": "收盘跌回轨道内超过 0.5 个 ATR",
    },
    {
        "id": "FALSE_BREAKOUT_REVERSAL",
        "name": "假突破回落",
        "precondition": "突破后量能不足，或收盘重新回到轨道内",
        "target_source": "轨道另一侧",
        "invalidation": "再次收盘站上突破位",
    },
    {
        "id": "RANGE_OSCILLATION",
        "name": "区间震荡",
        "precondition": "收敛或箱体内，距两侧都超过 1 个 ATR",
        "target_source": "区间两端",
        "invalidation": "收盘越出任一侧",
    },
    {
        "id": "BREAKDOWN_DECLINE",
        "name": "破位下行",
        "precondition": "收盘跌破下轨、颈线或前低",
        "target_source": "形态高度 / 前低",
        "invalidation": "收盘收回破位位",
    },
    {
        "id": "C_WAVE_REVERSAL",
        "name": "C 浪末端反转",
        "precondition": "C 浪达到 1.0×A 附近，且出现背离或反转形态",
        "target_source": "B 浪终点",
        "invalidation": "C 浪继续创新低超过 1.618×A",
    },
    {
        "id": "OVERSOLD_BOUNCE",
        "name": "超跌反弹",
        "precondition": "RSI 低于 30、偏离 MA20 超过 2 个 ATR",
        "target_source": "MA20 / 0.382 反弹位",
        "invalidation": "再创新低",
    },
    {
        "id": "APEX_BREAKOUT",
        "name": "收敛末端变盘",
        "precondition": "距收敛顶点 ≤ 10 根",
        "target_source": "双向，形态起点高度",
        "invalidation": "—",
    },
)

PLAYBOOK_BY_ID: dict[str, dict[str, str]] = {row["id"]: row for row in SCENARIO_PLAYBOOK}


def _money(val: float | Decimal, decimals: int | None = None) -> float:
    v = round(float(val), 4)
    cleaned = Decimal(str(v))
    if decimals is not None and decimals >= 3:
        return float(cleaned.quantize(Decimal("10") ** -int(decimals), rounding=ROUND_HALF_UP))
    if 0 < abs(v) < 10.0 and round(v, 3) != round(v, 2):
        return float(cleaned.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
    return float(cleaned.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _fmt(val: float, decimals: int = 2) -> str:
    return f"{float(val):.{decimals}f}"


def detect_impulse_wave(
    sview: list[dict[str, Any]],
    pivots: list[dict[str, Any]],
    atr: float,
    decimals: int = 2,
) -> dict[str, Any] | None:
    """在近 120 日已确认拐点序列中识别上升/下降推动 5 浪（含进行中的第 3 浪或第 5 浪）。

    硬规则逐条校验：
    1. 浪 2 不破浪 1 起点；
    2. 浪 3 在已走完或进行中的同向推动浪里不是最短；
    3. 浪 4 不进入浪 1 的价格区间。
    """
    confirmed = [p for p in pivots if p.get("confirmed")]
    if len(confirmed) < 3 or not sview:
        return None
    last_c = float(sview[-1]["close"])
    last_hi = max(float(b["high"]) for b in sview[-5:])
    last_lo = min(float(b["low"]) for b in sview[-5:])
    min_leg = max(0.015, 1.1 * atr)

    # 1) 检查 5 个已确认拐点 (p0..p4) + 当前进行中的第 5 浪
    if len(confirmed) >= 5:
        for start_i in range(len(confirmed) - 5, max(-1, len(confirmed) - 8), -1):
            seq = confirmed[start_i : start_i + 5]
            p0, p1, p2, p3, p4 = seq
            if [p["kind"] for p in seq] == ["L", "H", "L", "H", "L"]:
                w1 = p1["price"] - p0["price"]
                w3 = p3["price"] - p2["price"]
                w5 = max(last_hi, last_c) - p4["price"]
                rule_w2 = p2["price"] > p0["price"]
                rule_w3 = w3 >= w1 * 0.95 and (w5 <= 0 or w3 >= min(w1, w5))
                rule_w4 = p4["price"] > p1["price"]
                if w1 / max(p0["price"], 1e-6) >= min_leg and rule_w2 and rule_w3 and rule_w4 and last_c > p4["price"]:
                    t1618 = _money(p2["price"] + 1.618 * w1)
                    w5_target = _money(max(t1618, p4["price"] + w1, p3["price"] * (1.0 + 0.8 * atr), last_c * (1.0 + 0.8 * atr)))
                    return {
                        "direction": "up",
                        "label": "上升推动 5 浪（第 5 浪进行中）",
                        "phase": "wave_5",
                        "wave1_start": {"day": p0["day"], "price": p0["price"]},
                        "wave1_end": {"day": p1["day"], "price": p1["price"]},
                        "wave2_end": {"day": p2["day"], "price": p2["price"]},
                        "wave3_end": {"day": p3["day"], "price": p3["price"]},
                        "wave4_end": {"day": p4["day"], "price": p4["price"]},
                        "wave3_1618_target": w5_target,
                        "invalidation_price": _money(p4["price"]),
                        "invalidation_text": f"收盘跌破浪 4 终点 {_fmt(p4['price'], decimals)} 则上升推动 5 浪计数失效",
                        "hard_rules": {
                            "wave2_above_wave1_start": True,
                            "wave3_not_shortest": True,
                            "wave4_outside_wave1": True,
                        },
                    }
            if [p["kind"] for p in seq] == ["H", "L", "H", "L", "H"]:
                w1 = p0["price"] - p1["price"]
                w3 = p2["price"] - p3["price"]
                w5 = p4["price"] - min(last_lo, last_c)
                rule_w2 = p2["price"] < p0["price"]
                rule_w3 = w3 >= w1 * 0.95 and (w5 <= 0 or w3 >= min(w1, w5))
                rule_w4 = p4["price"] < p1["price"]
                if w1 / max(p0["price"], 1e-6) >= min_leg and rule_w2 and rule_w3 and rule_w4 and last_c < p4["price"]:
                    t1618 = _money(max(0.01, p2["price"] - 1.618 * w1))
                    w5_target = _money(min(t1618, max(0.01, p4["price"] - w1), p3["price"] * (1.0 - 0.8 * atr), max(0.01, last_c * (1.0 - 0.8 * atr))))
                    return {
                        "direction": "down",
                        "label": "下降推动 5 浪（第 5 浪进行中）",
                        "phase": "wave_5",
                        "wave1_start": {"day": p0["day"], "price": p0["price"]},
                        "wave1_end": {"day": p1["day"], "price": p1["price"]},
                        "wave2_end": {"day": p2["day"], "price": p2["price"]},
                        "wave3_end": {"day": p3["day"], "price": p3["price"]},
                        "wave4_end": {"day": p4["day"], "price": p4["price"]},
                        "wave3_1618_target": w5_target,
                        "invalidation_price": _money(p4["price"]),
                        "invalidation_text": f"收盘站上浪 4 终点 {_fmt(p4['price'], decimals)} 则下降推动 5 浪计数失效",
                        "hard_rules": {
                            "wave2_above_wave1_start": True,
                            "wave3_not_shortest": True,
                            "wave4_outside_wave1": True,
                        },
                    }

    # 2) 检查最近 3 个已确认拐点 (p0, p1, p2) + 当前正在推进的第 3 浪主升/主跌
    for start_i in range(len(confirmed) - 3, max(-1, len(confirmed) - 6), -1):
        p0, p1, p2 = confirmed[start_i : start_i + 3]
        if [p0["kind"], p1["kind"], p2["kind"]] == ["L", "H", "L"]:
            w1 = p1["price"] - p0["price"]
            retrace = (p1["price"] - p2["price"]) / max(w1, 1e-6)
            w3_now = max(last_hi, last_c) - p2["price"]
            if w1 / max(p0["price"], 1e-6) >= min_leg and 0.236 <= retrace < 0.95 and last_c > p1["price"] and w3_now >= 0.8 * w1:
                t1618 = _money(p2["price"] + 1.618 * w1)
                for mult in (2.0, 2.618, 3.618):
                    if t1618 <= last_c * (1.0 + 0.5 * atr):
                        t1618 = _money(p2["price"] + mult * w1)
                if t1618 <= last_c * (1.0 + 0.5 * atr):
                    t1618 = _money(last_c * (1.0 + 1.2 * atr))
                return {
                    "direction": "up",
                    "label": "上升推动浪（第 3 浪推进中）",
                    "phase": "wave_3",
                    "wave1_start": {"day": p0["day"], "price": p0["price"]},
                    "wave1_end": {"day": p1["day"], "price": p1["price"]},
                    "wave2_end": {"day": p2["day"], "price": p2["price"]},
                    "wave3_1618_target": t1618,
                    "invalidation_price": _money(p0["price"]),
                    "invalidation_text": f"收盘跌破浪 1 起点 {_fmt(p0['price'], decimals)} 则上升推动浪计数失效",
                    "hard_rules": {
                        "wave2_above_wave1_start": p2["price"] > p0["price"],
                        "wave3_not_shortest": True,
                        "wave4_outside_wave1": True,
                    },
                }
        if [p0["kind"], p1["kind"], p2["kind"]] == ["H", "L", "H"]:
            w1 = p0["price"] - p1["price"]
            retrace = (p2["price"] - p1["price"]) / max(w1, 1e-6)
            w3_now = p2["price"] - min(last_lo, last_c)
            if w1 / max(p0["price"], 1e-6) >= min_leg and 0.236 <= retrace < 0.95 and last_c < p1["price"] and w3_now >= 0.8 * w1:
                t1618 = _money(max(0.01, p2["price"] - 1.618 * w1))
                for mult in (2.0, 2.618, 3.618):
                    if t1618 >= last_c * (1.0 - 0.5 * atr):
                        t1618 = _money(max(0.01, p2["price"] - mult * w1))
                if t1618 >= last_c * (1.0 - 0.5 * atr):
                    t1618 = _money(max(0.01, last_c * (1.0 - 1.2 * atr)))
                return {
                    "direction": "down",
                    "label": "下降推动浪（第 3 浪推进中）",
                    "phase": "wave_3",
                    "wave1_start": {"day": p0["day"], "price": p0["price"]},
                    "wave1_end": {"day": p1["day"], "price": p1["price"]},
                    "wave2_end": {"day": p2["day"], "price": p2["price"]},
                    "wave3_1618_target": t1618,
                    "invalidation_price": _money(p0["price"]),
                    "invalidation_text": f"收盘站上浪 1 起点 {_fmt(p0['price'], decimals)} 则下降推动浪计数失效",
                    "hard_rules": {
                        "wave2_above_wave1_start": p2["price"] < p0["price"],
                        "wave3_not_shortest": True,
                        "wave4_outside_wave1": True,
                    },
                }
    return None


def _last_causal_state(bars: list[dict[str, Any]]) -> dict[str, float]:
    """最后一根已收盘K线的 RSI14（Wilder）、RVOL20、MA20、MA60，口径与原历史事件研究模块一致。"""
    closes = [float(b["close"]) for b in bars]
    vols = [float(b.get("volume") or 0.0) for b in bars]
    n = len(closes)
    rsi14 = 50.0
    avg_gain = avg_loss = 0.0
    for i in range(1, n):
        chg = closes[i] - closes[i - 1]
        gain, loss = max(0.0, chg), max(0.0, -chg)
        if i <= 14:
            avg_gain += gain / 14.0
            avg_loss += loss / 14.0
            if i < 14:
                continue
        else:
            avg_gain = (avg_gain * 13.0 + gain) / 14.0
            avg_loss = (avg_loss * 13.0 + loss) / 14.0
        if avg_gain == 0.0 and avg_loss == 0.0:
            rsi14 = 50.0
        elif avg_loss == 0.0:
            rsi14 = 100.0
        else:
            rsi14 = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    win_v = vols[-20:]
    mean_v = sum(win_v) / len(win_v) if win_v else 0.0
    return {
        "rsi14": rsi14,
        "rvol20": vols[-1] / mean_v if mean_v > 0 else 1.0,
        "ma20": sum(closes[-20:]) / len(closes[-20:]) if closes else 0.0,
        "ma60": sum(closes[-60:]) / len(closes[-60:]) if closes else 0.0,
    }


def _slot_triplet(t_med: float, horizon: int) -> tuple[int, int, int]:
    if horizon < 3:
        return horizon, horizon, horizon
    t3 = min(horizon, max(3, round(t_med)))
    t1 = min(t3 - 2, max(1, round(t3 * 0.35)))
    t2 = min(t3 - 1, max(t1 + 1, round(t3 * 0.68)))
    return t1, t2, t3


def build_scenarios(
    *,
    hist: list[dict[str, Any]],
    sview: list[dict[str, Any]],
    ref: float,
    live_price: float | None,
    struct: dict[str, Any],
    ladder: dict[str, Any],
    pivots: list[dict[str, Any]],
    scenario_abc: dict[str, Any] | None,
    wave_counts: list[dict[str, Any]],
    fibs: dict[str, Any],
    ma_last: dict[int, float | None],
    atr: float,
    sigma: float,
    horizon_bars: int,
    base_projections: dict[str, Any],
    decimals: int = 2,
    round_price: Callable[[float, str], float] | None = None,
) -> dict[str, Any]:
    """根据 L1 状态评估 8 类情景，选出 2~5 个适用情景。"""
    horizon = max(1, int(horizon_bars))
    start_p = float(live_price) if live_price is not None else float(ref)
    fp = lambda v: _fmt(v, decimals)
    as_of_day = str(sview[-1]["day"]) if sview else ""
    rp = (lambda v: float(round_price(float(v), as_of_day))) if round_price is not None else (lambda v: _money(v, decimals))

    state = _last_causal_state(hist)
    rsi_now = state["rsi14"]
    rvol_now = state["rvol20"]
    ma20 = ma_last.get(20) or (state["ma20"] or ref)
    ma60 = ma_last.get(60) or (state["ma60"] or ref)

    u = float(struct["upper_today"])
    l = float(struct["lower_today"])
    mid_rail = 0.5 * (u + l)
    su = float(struct.get("slope_upper_per_day") or 0.0)
    sl = float(struct.get("slope_lower_per_day") or 0.0)
    pat_name = str(struct.get("pattern") or "")
    st_code = str(struct.get("status") or "inside")
    apex_ahead = struct.get("apex_bars_ahead")
    height = float(struct.get("height_at_start") or 0.0) if struct.get("fitted") else 0.0
    atr_abs = max(1e-6, ref * atr)
    min_trig_dist = MIN_TRIGGER_ATR * atr

    r1 = float(base_projections["breakout_level"]["price"])
    r1_src = str(base_projections["breakout_level"]["source"])
    s1 = float(base_projections["breakdown_level"]["price"])
    s1_src = str(base_projections["breakdown_level"]["source"])
    bull_base = base_projections["bull"]
    neut_base = base_projections["neutral"]
    bear_base = base_projections["bear"]

    impulse = detect_impulse_wave(sview, pivots, atr, decimals=decimals)

    # 预计算近期突破/冲高回落/跌破特征（只用已收盘 sview）
    recent5 = sview[-5:] if len(sview) >= 5 else sview
    recent10 = sview[-10:] if len(sview) >= 10 else sview
    prev20_hi = max(float(b["high"]) for b in sview[-25:-5]) if len(sview) >= 25 else max(float(b["high"]) for b in sview)
    prev20_lo = min(float(b["low"]) for b in sview[-25:-5]) if len(sview) >= 25 else min(float(b["low"]) for b in sview)
    recent_hi5 = max(float(b["high"]) for b in recent5)
    recent_lo5 = min(float(b["low"]) for b in recent5)
    recent_hi10 = max(float(b["high"]) for b in recent10)
    piv_hs = [float(p["price"]) for p in pivots if p.get("confirmed") and p.get("kind") == "H"]
    piv_ls = [float(p["price"]) for p in pivots if p.get("confirmed") and p.get("kind") == "L"]
    status_run_bars = int(struct.get("status_run_bars") or 1)

    def build_item(
        *,
        sc_id: str,
        direction: str,
        precondition_met: bool,
        applicability_score: float,
        rationale: str,
        raw_trigger_price: float,
        pending_trigger_text: str,
        active_trigger_text: str,
        mid_waypoint_price: float,
        mid_step_label: str,
        target_price: float,
        target_source_detail: str,
        invalidation_price: float | None,
        invalidation_text: str,
        already_active: bool = False,
    ) -> dict[str, Any]:
        meta = PLAYBOOK_BY_ID[sc_id]
        # 折点时间按扩散尺度 t≈(距离/日波动σ)² 估计，只用于画路径，截断在预测期内
        diff_eta = ((abs(target_price / ref - 1.0)) / max(sigma, 1e-6)) ** 2
        med_c = min(float(horizon), max(1.0, round(diff_eta, 1)))

        trig_dist_atr = round(abs(raw_trigger_price / ref - 1.0) / max(atr, 1e-6), 2)
        is_active = bool(precondition_met and (already_active or abs(raw_trigger_price / ref - 1.0) < min_trig_dist))
        status = "ACTIVE" if is_active else "PENDING_TRIGGER"
        status_cn = "已在进行中" if is_active else ("待触发" if precondition_met else "待触发（前提未成立，条件备选）")
        trigger_text = active_trigger_text if is_active else pending_trigger_text
        if live_price is not None and not is_active:
            if direction == "BULLISH" and live_price >= raw_trigger_price:
                trigger_text += "（盘中已触及，待收盘确认）"
            elif direction == "BEARISH" and live_price <= raw_trigger_price:
                trigger_text += "（盘中已触及，待收盘确认）"

        t1, t2, t3 = _slot_triplet(med_c, horizon)
        wp1_price = rp(0.5 * (ref + mid_waypoint_price)) if is_active else rp(raw_trigger_price)
        wp2_price = rp(mid_waypoint_price)
        wp3_price = rp(target_price)
        if direction == "BULLISH" and wp3_price <= ref:
            wp3_price = rp(ref * (1.0 + max(0.01, 0.6 * atr)))
        elif direction == "BEARISH" and wp3_price >= ref:
            wp3_price = rp(max(0.01, ref * (1.0 - max(0.01, 0.6 * atr))))
        inv_p_clean = rp(invalidation_price) if invalidation_price is not None else None
        if inv_p_clean is not None:
            if direction == "BULLISH" and inv_p_clean >= ref:
                inv_p_clean = rp(max(0.01, ref * (1.0 - max(0.01, 0.4 * atr))))
            elif direction == "BEARISH" and inv_p_clean <= ref:
                inv_p_clean = rp(ref * (1.0 + max(0.01, 0.4 * atr)))
            # 文案中的失效价必须与写入白名单的价位表量化值一致，否则 lint 会把未量化价判为捏造价格。
            for old in {f"{float(invalidation_price):.2f}", f"{float(invalidation_price):.3f}", fp(invalidation_price)}:
                new = fp(inv_p_clean)
                if old != new and re.search(rf"(?<![\d.]){re.escape(old)}(?![\d])", invalidation_text):
                    invalidation_text = re.sub(rf"(?<![\d.]){re.escape(old)}(?![\d])", new, invalidation_text)
        step1_txt = f"进行中 {fp(ref)}" if is_active else f"触发 {fp(raw_trigger_price)}"

        return {
            "id": sc_id,
            "name": meta["name"],
            "direction": direction,
            "status": status,
            "status_cn": status_cn,
            "selection_role": "PRECONDITION_MET" if precondition_met else "CONDITIONAL_FALLBACK",
            "precondition_template": meta["precondition"],
            "precondition_met": precondition_met,
            "precondition_rationale": rationale,
            "applicability_score": round(applicability_score, 2),
            "trigger": trigger_text,
            "trigger_price": None if is_active else rp(raw_trigger_price),
            "reference_trigger_price": rp(raw_trigger_price),
            "trigger_distance_atr": trig_dist_atr,
            "target_price": wp3_price,
            "target_source_template": meta["target_source"],
            "target_source": target_source_detail,
            "invalidation": invalidation_text,
            "invalidation_template": meta["invalidation"],
            "invalidation_price": inv_p_clean,
            "waypoints_offset": [(0, start_p), (t1, wp1_price), (t2, wp2_price), (t3, wp3_price)],
            "step_labels": [(t1, step1_txt), (t2, mid_step_label)],
        }

    candidates: list[dict[str, Any]] = []

    # 1. TREND_CONTINUATION（趋势延续）
    channel_active = bool(struct.get("fitted") and st_code in ("inside", "above") and ref >= mid_rail)
    up_impulse = impulse is not None and impulse["direction"] == "up" and ref >= mid_rail
    if channel_active or up_impulse or (ref >= ma20 >= ma60 and ref >= mid_rail):
        strict_1 = bool(channel_active or up_impulse)
        ext_u = _money(max(u + max(0.0, su) * horizon, bull_base["target_price"], ref * (1.0 + 0.8 * atr)))
        if up_impulse and impulse is not None:
            tgt_1 = _money(max(float(impulse["wave3_1618_target"]), ref * (1.0 + 0.8 * atr)))
            src_1 = f"{impulse['label']} · 1.618×浪1 ({fp(tgt_1)})"
            rat_1 = f"{impulse['label']}，收盘 {fp(ref)} 位于通道中轨 {fp(mid_rail)} 之上"
        else:
            tgt_1 = ext_u
            src_1 = f"通道上轨外推 {fp(ext_u)}（基准目标 {fp(bull_base['target_price'])}）"
            rat_1 = f"{pat_name}进行中，收盘 {fp(ref)} 站稳通道中轨 {fp(mid_rail)} 与 MA20 ({fp(ma20)}) 之上"
        inv_1 = _money(min(l, s1, ref * (1.0 - 0.5 * atr)))
        candidates.append(
            build_item(
                sc_id="TREND_CONTINUATION",
                direction="BULLISH",
                precondition_met=strict_1,
                applicability_score=88.0 if strict_1 else 62.0,
                rationale=rat_1,
                raw_trigger_price=max(mid_rail, ma20) if strict_1 else _money(max(r1, ref * (1.0 + 0.55 * atr))),
                pending_trigger_text=f"日线收盘站稳通道中轨 {fp(mid_rail)} 之上并突破 {fp(r1)}",
                active_trigger_text=f"已在进行中（收盘 {fp(ref)} 位于通道中轨 {fp(mid_rail)} 之上，沿通道推进）",
                mid_waypoint_price=max(ref, r1),
                mid_step_label=f"推进 {fp(max(ref, r1))}",
                target_price=tgt_1,
                target_source_detail=src_1,
                invalidation_price=inv_1,
                invalidation_text=f"收盘跌破通道下轨 {fp(inv_1)}",
                already_active=strict_1,
            )
        )

    # 2. BREAKOUT_RETEST（突破回踩）
    broken_up_candidates: list[float] = []
    if st_code == "above" and status_run_bars <= 5 and not struct["expired"] and ref >= u:
        broken_up_candidates.append(u)
    if len(sview) >= 20 and ref >= prev20_hi * 0.998 and min(float(b["close"]) for b in recent5) < prev20_hi:
        broken_up_candidates.append(min(ref, prev20_hi))
    for ph in piv_hs[-3:]:
        if ref >= ph * 0.998 and min(float(b["close"]) for b in recent5) < ph:
            broken_up_candidates.append(min(ref, ph))
    broke_up_recent = bool(broken_up_candidates)
    near_breakout = (not broke_up_recent) and (abs(r1 / ref - 1.0) <= max(0.035, 1.8 * atr) or abs(u / ref - 1.0) <= 1.5 * atr)
    strict_2 = bool(broke_up_recent)
    rail_ref = rp(_money(max(broken_up_candidates)) if broke_up_recent else _money(max(r1, ref * (1.0 + 0.55 * atr))))
    meas_target = _money(rail_ref + (min(height, ref * 3.0 * atr) if height > 0 else ref * 2.2 * atr))
    tgt_2 = rp(_money(max(meas_target, float(bull_base["target_price"]), ref * (1.0 + 0.8 * atr))))
    inv_2 = rp(_money(min(rail_ref - 0.5 * atr_abs, ref - 0.4 * atr_abs)))
    candidates.append(
        build_item(
            sc_id="BREAKOUT_RETEST",
            direction="BULLISH",
            precondition_met=strict_2,
            applicability_score=92.0 if strict_2 else (72.0 if near_breakout else 55.0),
            rationale=(
                f"近 5 根 K 线内已收盘站上轨道/颈线 {fp(rail_ref)}（{struct['status_short']}）"
                if strict_2
                else f"现价距上方突破位 {fp(r1)}（{r1_src}）约 {abs(r1 / ref - 1.0) / max(atr, 1e-6):.1f} 个 ATR"
            ),
            raw_trigger_price=rail_ref,
            pending_trigger_text=bull_base["trigger"],
            active_trigger_text=f"已在进行中（已突破 {fp(rail_ref)}，回踩守住 {fp(inv_2)} 即确认有效）",
            mid_waypoint_price=float(bull_base["retest_hold"]),
            mid_step_label=f"回踩 {fp(bull_base['retest_hold'])}",
            target_price=tgt_2,
            target_source_detail=f"形态高度测量 / {bull_base['target_source']}",
            invalidation_price=inv_2,
            invalidation_text=f"收盘跌回轨道内超过 0.5 个 ATR（跌破 {fp(inv_2)}）",
            already_active=strict_2,
        )
    )

    # 3. FALSE_BREAKOUT_REVERSAL（假突破回落）
    poked_above = st_code != "below" and ref >= mid_rail - 0.3 * atr_abs and (
        (recent_hi10 >= u * 0.997 and ref < u and not struct["expired"])
        or (recent_hi10 >= prev20_hi * 0.998 and ref < prev20_hi)
        or any(recent_hi10 >= ph * 0.998 and ref < ph for ph in piv_hs[-3:])
    )
    low_vol_break = (st_code == "above" or ref >= prev20_hi * 0.998) and rvol_now < 1.15
    strict_3 = bool(poked_above or low_vol_break)
    near_upper_resist = ref >= mid_rail and abs(u - ref) <= 1.6 * atr_abs
    if strict_3 or near_upper_resist:
        cand_dn = [
            x
            for x in (l if not struct["expired"] else None, s1, float(bear_base["target_price"]))
            if x is not None and x <= ref * (1.0 - 0.5 * atr)
        ]
        other_rail = rp(max(cand_dn) if cand_dn else min(float(bear_base["target_price"]), max(0.01, ref * (1.0 - 0.8 * atr))))
        tested_resist = u if (not struct["expired"] and abs(u - ref) <= 2.5 * atr_abs) else r1
        brk_cap = rp(max(recent_hi5, min(r1, tested_resist) if strict_3 else tested_resist, ref + 0.4 * atr_abs))
        trig_3 = rp(
            min(
                u if not struct["expired"] else float(bull_base["retest_hold"]),
                float(bull_base["retest_hold"]),
                ref * (1.0 - 0.55 * atr),
            )
        )
        fell_back_inside = bool(poked_above and st_code != "above")
        fb_Fall_lvl = u if (st_code == "above" and not struct["expired"]) else float(bull_base["retest_hold"])
        candidates.append(
            build_item(
                sc_id="FALSE_BREAKOUT_REVERSAL",
                direction="BEARISH",
                precondition_met=strict_3,
                applicability_score=86.0 if strict_3 else 64.0,
                rationale=(
                    f"近期冲高至 {fp(recent_hi5)} 后量能不足（RVOL20={rvol_now:.2f}x）或收盘跌回轨道内 {fp(u)}"
                    if strict_3
                    else f"现价逼近上轨/前高阻力 {fp(r1)}，若冲高缺量跌回 {fp(bull_base['retest_hold'])} 下方则触发假突破"
                ),
                raw_trigger_price=brk_cap if fell_back_inside else trig_3,
                pending_trigger_text=f"冲高受阻于 {fp(brk_cap if st_code == 'above' else r1)} 且日线收盘跌回 {fp(fb_Fall_lvl)} 下方",
                active_trigger_text=f"已在进行中（冲高 {fp(recent_hi5)} 后{'已跌回轨道内' if ref < u else '受阻回落'}，受制于 {fp(brk_cap)}）",
                mid_waypoint_price=mid_rail if mid_rail < ref else float(neut_base["range"][0]),
                mid_step_label=f"回落 {fp(mid_rail if mid_rail < ref else neut_base['range'][0])}",
                target_price=other_rail,
                target_source_detail=f"轨道另一侧 {fp(other_rail)}",
                invalidation_price=brk_cap,
                invalidation_text=f"再次收盘站上突破位 {fp(brk_cap)}",
                already_active=fell_back_inside,
            )
        )

    # 4. RANGE_OSCILLATION（区间震荡）
    dist_u_atr = (max(u, r1) - ref) / atr_abs
    dist_l_atr = (ref - min(l, s1)) / atr_abs
    strict_4 = bool(st_code == "inside" and dist_u_atr >= 1.0 and dist_l_atr >= 1.0)
    neut_trig_raw = float(neut_base["target_price"])
    if not strict_4 and abs(neut_trig_raw / ref - 1.0) < min_trig_dist:
        neut_trig_raw = _money(ref * (1.0 + 0.55 * atr) if neut_trig_raw >= ref else ref * (1.0 - 0.55 * atr))
    candidates.append(
        build_item(
            sc_id="RANGE_OSCILLATION",
            direction="NEUTRAL",
            precondition_met=strict_4,
            applicability_score=84.0 if strict_4 else 58.0,
            rationale=(
                f"收盘位于 {pat_name} 内部，距上方 {fp(max(u, r1))} 约 {dist_u_atr:.1f} ATR、距下方 {fp(min(l, s1))} 约 {dist_l_atr:.1f} ATR"
                if strict_4
                else f"条件备选（前提未成立）：当前距轨道/支撑边界不足 1.0 ATR 或已越出轨道，仅当回到 {fp(neut_base['range'][0])} ~ {fp(neut_base['range'][1])} 区间内才按震荡处理"
            ),
            raw_trigger_price=neut_trig_raw,
            pending_trigger_text=neut_base["condition"],
            active_trigger_text=f"已在进行中（{neut_base['condition']}，在 {fp(neut_base['range'][0])} ~ {fp(neut_base['range'][1])} 内运行）",
            mid_waypoint_price=float(neut_base["range"][1]),
            mid_step_label=f"区间上沿 {fp(neut_base['range'][1])}",
            target_price=float(neut_base["target_price"]),
            target_source_detail=f"区间两端 {fp(neut_base['range'][0])} ~ {fp(neut_base['range'][1])}（中枢 {fp(neut_base['target_price'])}）",
            invalidation_price=s1,
            invalidation_text=f"收盘越出任一侧（站上 {fp(r1)} 或跌破 {fp(s1)}）",
            already_active=strict_4,
        )
    )

    # 5. BREAKDOWN_DECLINE（破位下行）
    broken_dn_candidates: list[float] = []
    if st_code == "below" and not struct["expired"] and ref <= l:
        broken_dn_candidates.append(l)
    if len(sview) >= 20 and ref <= prev20_lo * 1.002 and max(float(b["close"]) for b in recent5) > prev20_lo:
        broken_dn_candidates.append(max(ref, prev20_lo))
    for pl in piv_ls[-2:]:
        if ref <= pl * 1.002 and max(float(b["close"]) for b in recent5) > pl:
            broken_dn_candidates.append(max(ref, pl))
    broke_dn_recent = bool(broken_dn_candidates)
    near_support = ref <= mid_rail or abs(ref / s1 - 1.0) <= max(0.035, 1.8 * atr)
    strict_5 = bool(broke_dn_recent)
    dn_ref = rp(_money(min(broken_dn_candidates)) if broke_dn_recent else _money(min(s1, ref * (1.0 - 0.55 * atr))))
    inv_5 = rp(_money(max(dn_ref + 0.4 * atr_abs, ref + 0.4 * atr_abs)))
    tgt_5 = rp(_money(min(float(bear_base["target_price"]), max(0.01, ref * (1.0 - 0.8 * atr)))))
    candidates.append(
        build_item(
            sc_id="BREAKDOWN_DECLINE",
            direction="BEARISH",
            precondition_met=strict_5,
            applicability_score=91.0 if strict_5 else (68.0 if near_support else 52.0),
            rationale=(
                f"收盘已跌破下轨/前低支撑 {fp(dn_ref)}（{struct['status_short']}）"
                if strict_5
                else f"现价距下方支撑位 {fp(s1)}（{s1_src}）约 {abs(ref / s1 - 1.0) / max(atr, 1e-6):.1f} 个 ATR"
            ),
            raw_trigger_price=dn_ref,
            pending_trigger_text=bear_base["trigger"],
            active_trigger_text=f"已在进行中（已收盘跌破 {fp(dn_ref)}，反抽受制于 {fp(inv_5)}）",
            mid_waypoint_price=float(bear_base["rebound_cap"]),
            mid_step_label=f"反抽 {fp(bear_base['rebound_cap'])}",
            target_price=tgt_5,
            target_source_detail=f"形态高度 / 前低（{bear_base['target_source']}）",
            invalidation_price=inv_5,
            invalidation_text=f"收盘收回破位位 {fp(inv_5)} 上方",
            already_active=strict_5,
        )
    )

    # 6. C_WAVE_REVERSAL（C 浪末端反转）
    all_wave_cands = [wc for wc in ([scenario_abc] + list(wave_counts or [])) if wc is not None]
    c_cand = None
    for wc_item in all_wave_cands:
        cr = float(wc_item["c_progress"]["c_over_a"])
        cd = str(wc_item["direction"])
        exh = (cd == "down" and (rsi_now <= 44.0 or float(sview[-1]["close"]) >= float(sview[-1]["open"]))) or (
            cd == "up" and (rsi_now >= 56.0 or float(sview[-1]["close"]) <= float(sview[-1]["open"]))
        )
        if 0.72 <= cr <= 1.62 and exh:
            c_cand = wc_item
            break
    if c_cand is None and all_wave_cands:
        c_cand = min(all_wave_cands, key=lambda wc_item: abs(float(wc_item["c_progress"]["c_over_a"]) - 1.0))
    if c_cand is not None:
        c_ratio = float(c_cand["c_progress"]["c_over_a"])
        c_dir = str(c_cand["direction"])
        b_end_p = float(c_cand["b_end"]["price"])
        a_s_p = float(c_cand["a_start"]["price"])
        a_e_p = float(c_cand["a_end"]["price"])
        a_span = abs(a_e_p - a_s_p)
        near_100 = 0.72 <= c_ratio <= 1.62
        exhaustion = (c_dir == "down" and (rsi_now <= 44.0 or float(sview[-1]["close"]) >= float(sview[-1]["open"]))) or (
            c_dir == "up" and (rsi_now >= 56.0 or float(sview[-1]["close"]) <= float(sview[-1]["open"]))
        )
        strict_6 = bool(near_100 and exhaustion)
        if strict_6 or (0.65 <= c_ratio <= 1.75):
            rev_dir = "BULLISH" if c_dir == "down" else "BEARISH"
            lvl_1618 = (
                _money(min(b_end_p - 1.618 * a_span, ref * (1.0 - 0.5 * atr)))
                if c_dir == "down"
                else _money(max(b_end_p + 1.618 * a_span, ref * (1.0 + 0.5 * atr)))
            )
            tgt_6 = (
                _money(max(b_end_p, ref * (1.0 + 0.8 * atr)))
                if rev_dir == "BULLISH"
                else _money(min(b_end_p, max(0.01, ref * (1.0 - 0.8 * atr))))
            )
            trig_6 = rp(_money(ref * (1.0 + 0.55 * atr)) if rev_dir == "BULLISH" else _money(ref * (1.0 - 0.55 * atr)))
            raw_trig_6 = rp(
                r1
                if (rev_dir == "BULLISH" and r1 >= ref * (1.0 + 0.55 * atr) and abs(r1 / ref - 1.0) <= 1.5 * atr)
                else (
                    s1
                    if (rev_dir == "BEARISH" and s1 <= ref * (1.0 - 0.55 * atr) and abs(s1 / ref - 1.0) <= 1.5 * atr)
                    else trig_6
                )
            )
            candidates.append(
                build_item(
                    sc_id="C_WAVE_REVERSAL",
                    direction=rev_dir,
                    precondition_met=strict_6,
                    applicability_score=89.0 if strict_6 else 65.0,
                    rationale=f"{c_cand['label']} C/A 已达 {c_ratio:.2f} 倍（接近 1.0×A），RSI14={rsi_now:.1f}",
                    raw_trigger_price=raw_trig_6,
                    pending_trigger_text=(
                        f"C 浪在 1.0×A 附近企稳并放量站上 {fp(raw_trig_6)}"
                        if rev_dir == "BULLISH"
                        else f"C 浪在 1.0×A 附近受阻并收盘跌破 {fp(raw_trig_6)}"
                    ),
                    active_trigger_text=f"已在进行中（C/A={c_ratio:.2f} 已在 1.0×A 附近出现反转K线）",
                    mid_waypoint_price=_money(0.5 * (ref + tgt_6)),
                    mid_step_label=f"反抽 {fp(0.5 * (ref + tgt_6))}",
                    target_price=tgt_6,
                    target_source_detail=f"B 浪终点 {fp(tgt_6)} ({c_cand['b_end']['day'][5:]})",
                    invalidation_price=lvl_1618,
                    invalidation_text=(
                        f"C 浪继续创新低超过 1.618×A（跌破 {fp(lvl_1618)}）"
                        if c_dir == "down"
                        else f"C 浪继续创新高超过 1.618×A（站上 {fp(lvl_1618)}）"
                    ),
                    already_active=bool(strict_6 and abs(c_ratio - 1.0) <= 0.12),
                )
            )

    # 7. OVERSOLD_BOUNCE（超跌反弹）
    dev_ma20_atr = (ma20 - ref) / atr_abs
    strict_7 = bool(rsi_now < 30.0 or dev_ma20_atr >= 2.0 or (rsi_now <= 35.0 and dev_ma20_atr >= 1.5))
    if strict_7 or (rsi_now <= 40.0 and dev_ma20_atr >= 1.1):
        fib_382 = next((lv["price"] for lv in fibs.get("retracements", []) if abs(lv["ratio"] - 0.382) < 0.01), None)
        cand_bounce = [x for x in (ma20, fib_382, r1) if x is not None and x > ref * (1.0 + 0.5 * atr)]
        bounce_tgt = rp(_money(min(cand_bounce) if cand_bounce else max(ma20, r1, ref * (1.0 + 1.2 * atr))))
        inv_7 = rp(_money(min(recent_lo5, ref * (1.0 - 0.8 * atr))))
        trig_7 = rp(_money(min(r1, ref * (1.0 + 0.8 * atr)) if r1 >= ref * (1.0 + 0.55 * atr) else ref * (1.0 + 0.55 * atr)))
        candidates.append(
            build_item(
                sc_id="OVERSOLD_BOUNCE",
                direction="BULLISH",
                precondition_met=strict_7,
                applicability_score=93.0 if strict_7 else 66.0,
                rationale=f"RSI14={rsi_now:.1f}，收盘低于 MA20 ({fp(ma20)}) 达 {dev_ma20_atr:.2f} 个 ATR",
                raw_trigger_price=trig_7,
                pending_trigger_text=f"超跌止跌并日线收阳站上 {fp(trig_7)}",
                active_trigger_text=f"已在进行中（超跌偏离 {dev_ma20_atr:.1f} ATR 后日线已出现企稳阳线）",
                mid_waypoint_price=_money(0.5 * (ref + bounce_tgt)),
                mid_step_label=f"修复 {fp(0.5 * (ref + bounce_tgt))}",
                target_price=bounce_tgt,
                target_source_detail=f"MA20 ({fp(ma20)}) / 0.382 反弹位",
                invalidation_price=inv_7,
                invalidation_text=f"再创新低（收盘跌破 {fp(inv_7)}）",
                already_active=bool(strict_7 and float(sview[-1]["close"]) > float(sview[-1]["open"])),
            )
        )

    # 8. APEX_BREAKOUT（收敛末端变盘）
    is_converging = any(k in pat_name for k in ("三角", "楔形", "收敛"))
    strict_8 = bool(
        is_converging
        and not struct.get("expired")
        and apex_ahead is not None
        and 0.0 <= float(apex_ahead) <= 10.0
    )
    if strict_8 or (is_converging and not struct.get("expired") and apex_ahead is not None and 0.0 <= float(apex_ahead) <= 18.0):
        span_h = height if height > 0 else 2.5 * atr_abs
        up_apex_tgt = rp(_money(max(r1 + span_h * 0.65, float(bull_base["target_price"]))))
        dn_apex_tgt = rp(_money(min(max(0.01, s1 - span_h * 0.65), float(bear_base["target_price"]))))
        pref_up = ref >= mid_rail
        apex_note = f"距收敛顶点 {apex_ahead} 根 K 线" if apex_ahead is not None else "已抵近收敛顶点"
        apex_item = build_item(
            sc_id="APEX_BREAKOUT",
            direction="BIDIRECTIONAL",
            precondition_met=strict_8,
            applicability_score=90.0 if strict_8 else 67.0,
            rationale=f"{pat_name}进入末端（{apex_note}），通道宽度已收窄至 {abs(u - l) / atr_abs:.1f} 个 ATR",
            raw_trigger_price=r1 if pref_up else s1,
            pending_trigger_text=f"双向变盘：放量上破 {fp(r1)} 看至 {fp(up_apex_tgt)}，或下破 {fp(s1)} 看至 {fp(dn_apex_tgt)}",
            active_trigger_text=f"已在进行中（{apex_note}，现价正在争夺变盘边界 {fp(r1 if pref_up else s1)}）",
            mid_waypoint_price=r1 if pref_up else s1,
            mid_step_label=f"变盘 {fp(r1 if pref_up else s1)}",
            target_price=up_apex_tgt if pref_up else dn_apex_tgt,
            target_source_detail=f"双向，形态起点高度（上向 {fp(up_apex_tgt)} / 下向 {fp(dn_apex_tgt)}）",
            invalidation_price=float(neut_base["target_price"]),
            invalidation_text="—",
            already_active=False,
        )
        apex_item["alt_target_price"] = dn_apex_tgt if pref_up else up_apex_tgt
        candidates.append(apex_item)

    # 筛选规则：优先输出所有 precondition_met=True 的情景（按 applicability_score 降序，最多 5 个）；
    # 若满足前提的情景不足 2 个，仅补充 1 个最高分的待触发条件备选情景（保留 precondition_met=False，严禁篡改为 True）。
    met_list = sorted(
        [c for c in candidates if c["precondition_met"]],
        key=lambda x: x["applicability_score"],
        reverse=True,
    )
    if len(met_list) >= 2:
        selected = met_list[:5]
    else:
        rest = sorted(
            [c for c in candidates if not c["precondition_met"]],
            key=lambda x: x["applicability_score"],
            reverse=True,
        )
        selected = (met_list + rest)[:2]

    return {
        "scenarios": selected,
        "scenario_count": len(selected),
        "precondition_met_count": len(met_list),
        "scenario_signature": [s["id"] for s in selected],
        "impulse_wave": impulse,
    }
