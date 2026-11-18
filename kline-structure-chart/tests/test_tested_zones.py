"""多次测试价区（tested_zones）：固定样例、性质与边界（只用录制的真实行情，测试断网）。"""
from __future__ import annotations

import hashlib
import json

import pytest

from conftest import RECORDED_DIR, load_recorded

CASE = "sina_daily_cases/sh000001_2026-11-17.json"
SH = "sina_daily/sh000001.json"
MOUTAI = "sina_daily/sh600519.json"
BYD = "sina_daily/sz002594.json"


def _bars(rel: str, upto: str = "9999-12-31") -> list[dict]:
    """录制行情截至 upto（含），只保留结构层用到的字段。"""
    return [{k: r[k] for k in ("day", "open", "high", "low", "close")} for r in load_recorded(rel) if r["day"] <= upto]


# ---------------------------------------------------------------------------
# 任务 1：固定截止日的真实行情录制
# ---------------------------------------------------------------------------


def test_case_fixture_is_recorded_real_data() -> None:
    manifest = json.loads((RECORDED_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    entry = manifest["files"][CASE]
    text = (RECORDED_DIR / CASE).read_text(encoding="utf-8")
    assert entry["source"].startswith("SINA getKLineData symbol=sh000001")
    assert entry["last_day"] == "2026-11-17" and entry["rows"] >= 500
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == entry["sha256"]
    lows = {b["day"]: b["low"] for b in json.loads(text)[-3:]}
    assert lows == {"2026-11-13": 3623.841, "2026-11-16": 3586.568, "2026-11-17": 3565.59}


# ---------------------------------------------------------------------------
# 任务 2：聚类、几何量、状态、筛选、头肩与平台（scan_zones 的内部结果，价格未取整）
# ---------------------------------------------------------------------------


def _scan(rel: str, upto: str) -> tuple[dict, list[dict]]:
    from structure import HISTORY_BARS
    from tested_zones import scan_zones

    hist = _bars(rel, upto)[-HISTORY_BARS:]
    return scan_zones(hist), hist


def _touches(z: dict) -> list[tuple[str, float]]:
    return [(t["day"], t["price"]) for t in z["touches"]]


def test_minor_threshold_rounds_up_to_half_percent_and_skips_when_not_finer() -> None:
    from tested_zones import minor_threshold

    assert minor_threshold(0.0101, 0.03) == pytest.approx(0.015)  # 下限 1.5%
    assert minor_threshold(0.0151, 0.03) == pytest.approx(0.02)  # 1.51% 向上取整到 2.0%
    assert minor_threshold(0.0200, 0.04) == pytest.approx(0.02)  # 恰好 2.0% 不再进位
    assert minor_threshold(0.0420, 0.084) == pytest.approx(0.04)  # 上限 4%
    assert minor_threshold(0.0220, 0.02475) is None  # 2.5% 不小于主级别放宽后的 2.475%：跳过本级别


def test_g2_sh_2026_10_15_four_touch_support_broken_five_touch_resistance_crossed() -> None:
    scan, hist = _scan(SH, "2026-10-15")
    minor, major = scan["scales"]["MINOR"], scan["scales"]["MAJOR"]
    sup = minor["zones"]["MINOR-SUPPORT-2026-07-20"]
    assert _touches(sup) == [("2026-07-20", 3741.11), ("2026-07-30", 3767.5), ("2026-10-09", 3754.14), ("2026-10-13", 3741.53)]
    assert sup["state"] == "BROKEN" and hist[sup["since_idx"]]["day"] == "2026-10-15"
    res = minor["zones"]["MINOR-RESISTANCE-2026-07-15"]
    assert len(res["touches"]) == 5 and res["touches"][-1]["day"] == "2026-09-22"
    assert res["state"] == "NECKLINE_CROSSED" and hist[res["since_idx"]]["day"] == "2026-10-15"
    assert res["neck"] == pytest.approx(3741.11, abs=0.006)
    top = major["zones"]["MAJOR-RESISTANCE-2026-08-18"]
    assert _touches(top) == [("2026-08-18", 3994.18), ("2026-09-01", 3995.18), ("2026-09-22", 3967.68)]
    assert top["state"] == "NECKLINE_CROSSED" and hist[top["since_idx"]]["day"] == "2026-09-28"
    assert {"MINOR-SUPPORT-2026-07-20", "MINOR-RESISTANCE-2026-07-15"} <= {zid for zid, _ in minor["selected"]}


def test_g3_moutai_double_bottom_reclaimed_too_late_is_expired() -> None:
    scan, _ = _scan(MOUTAI, "2026-10-15")
    major = scan["scales"]["MAJOR"]
    assert major["rejected"]["MAJOR-SUPPORT-2026-07-27"] == "EXPIRED"  # 07-27 1279.58 / 08-24 1270.33
    assert "MAJOR-SUPPORT-2026-07-27" not in major["zones"]
    top = major["zones"]["MAJOR-RESISTANCE-2026-08-03"]
    assert _touches(top) == [("2026-08-03", 1363.35), ("2026-08-10", 1359.97)] and top["state"] == "HOLDING"
    assert [zid for zid, _ in major["selected"]] == ["MAJOR-RESISTANCE-2026-08-03"]


def test_g4_byd_platform_and_old_four_touch_resistances_not_selected() -> None:
    scan, hist = _scan(BYD, "2026-10-15")
    pf = scan["scales"]["MAJOR"]["platform"]
    assert pf["support"]["zone_id"] == "MAJOR-SUPPORT-2026-09-11"
    assert [t["day"] for t in pf["support"]["touches"]] == ["2026-09-11", "2026-10-08"]
    assert pf["resistance"]["zone_id"] == "MAJOR-RESISTANCE-2026-09-22"
    assert [t["day"] for t in pf["resistance"]["touches"]] == ["2026-09-22", "2026-10-12"]
    assert (pf["support"]["low"], pf["resistance"]["high"]) == (81.51, 87.2) and round(pf["gap_atr"], 2) == 1.91
    minor = scan["scales"]["MINOR"]
    selected = {zid for zid, _ in minor["selected"]}
    for zid, first, last, since in (
        ("MINOR-RESISTANCE-2026-05-21", "2026-05-21", "2026-06-02", "2026-08-04"),
        ("MINOR-RESISTANCE-2026-07-17", "2026-07-17", "2026-08-03", "2026-08-31"),
    ):
        z = minor["zones"][zid]
        assert len(z["touches"]) == 4 and (z["touches"][0]["day"], z["touches"][-1]["day"]) == (first, last)
        assert z["state"] == "NECKLINE_CROSSED" and hist[z["since_idx"]]["day"] == since
        assert zid not in selected


def test_g5_minor_head_and_shoulders_bottom() -> None:
    scan, _ = _scan(SH, "2025-06-05")
    hs = scan["scales"]["MINOR"]["hs"]
    (b,) = [h for h in hs if h["kind"] == "L"]
    assert [(p["day"], p["price"]) for p in (b["s1"], b["head"], b["s2"])] == [
        ("2025-03-24", 3340.24), ("2025-04-07", 3040.69), ("2025-05-27", 3332.49)
    ]
    assert b["s2"]["confirmed"] and b["state"] == "FORMING"
    assert (b["n1"]["day"], b["n2"]["day"]) == ("2025-03-27", "2025-05-14")
    assert (b["n1"]["price"], b["n2"]["price"]) == pytest.approx((3394.04, 3417.31), abs=0.006)
    assert not [h for h in hs if h["kind"] == "H"]
    assert scan["scales"]["MAJOR"]["hs"] == []


def test_g5_steep_neck_top_is_rejected_only_by_the_slope_constraint(monkeypatch: pytest.MonkeyPatch) -> None:
    """03-27 / 05-14 / 06-05 的"头肩顶"跨越 4 月暴跌、颈线陡斜：放开斜率约束才会出现，说明上一条测试不是空转。"""
    import tested_zones

    monkeypatch.setattr(tested_zones, "HS_NECK_SLOPE_MAX", 100.0)
    scan, _ = _scan(SH, "2025-06-05")
    tops = [h for h in scan["scales"]["MINOR"]["hs"] if h["kind"] == "H"]
    assert [(h["s1"]["day"], h["head"]["day"], h["s2"]["day"]) for h in tops] == [("2025-03-27", "2025-05-14", "2025-06-05")]


def test_g6_major_head_and_shoulders_top() -> None:
    scan, _ = _scan(SH, "2026-06-26")
    (t,) = [h for h in scan["scales"]["MAJOR"]["hs"] if h["kind"] == "H"]
    assert [(p["day"], p["price"]) for p in (t["s1"], t["head"], t["s2"])] == [
        ("2026-03-03", 4197.23), ("2026-05-14", 4258.86), ("2026-06-23", 4175.35)
    ]
    assert (t["n1"]["day"], t["n2"]["day"]) == ("2026-03-23", "2026-06-08")
    assert (t["n1"]["price"], t["n2"]["price"]) == pytest.approx((3794.68, 3927.85), abs=0.006)
    assert t["state"] == "FORMING"


def test_zone_with_shallow_neck_is_not_output() -> None:
    scan, _ = _scan(SH, "2025-04-29")
    minor = scan["scales"]["MINOR"]
    assert minor["rejected"]["MINOR-SUPPORT-2025-01-24"] == "HEIGHT"
    assert "MINOR-SUPPORT-2025-01-24" not in minor["zones"]


def test_output_zones_obey_count_height_and_unconfirmed_rules_across_history() -> None:
    from structure import HISTORY_BARS
    from tested_zones import NECK_MIN_ATR, scan_zones

    bars = _bars(SH)
    seen = 0
    for cut in range(450, len(bars) + 1, 20):
        scan = scan_zones(bars[:cut][-HISTORY_BARS:])
        for sc in scan["scales"].values():
            for z in sc["zones"].values():
                seen += 1
                assert len(z["touches"]) >= 2 and z["touches"][0]["confirmed"]
                assert z["height"] >= NECK_MIN_ATR * scan["atr"] - 1e-9
                assert [t for t in z["touches"] if not t["confirmed"]] in ([], [z["touches"][-1]])  # 未确认触点至多一个且在最后
                assert all(t["confirmed_at_day"] <= scan["day"] for t in z["touches"] if t["confirmed"])
                assert z["state"] != "EXPIRED"
    assert seen > 100


def test_confirmed_geometry_ignores_bars_after_the_cut() -> None:
    """同一阈值与 ATR 下，截至截面日已确认的触点与价区几何量在追加放大 5 倍的未来K线后不变。"""
    from structure import HISTORY_BARS, STRUCT_DAYS, atr_pct
    from tested_zones import confirmed_geometry

    bars = _bars(SH)
    nonempty = 0
    for cut in range(500, len(bars) - 5, 37):
        sub = bars[:cut][-HISTORY_BARS:]
        ext = sub + [{**b, **{k: b[k] * 5 for k in ("open", "high", "low", "close")}} for b in bars[cut : cut + 25]]
        atr = atr_pct(sub) * sub[-1]["close"]
        for thr in (0.03, 0.015):
            geo = confirmed_geometry(sub, thr, atr, sub[-STRUCT_DAYS]["day"], sub[-1]["day"])
            assert geo == confirmed_geometry(ext, thr, atr, sub[-STRUCT_DAYS]["day"], sub[-1]["day"]), sub[-1]["day"]
            nonempty += bool(geo)
    assert nonempty > 10


# ---------------------------------------------------------------------------
# 任务 3：对外输出（取整、命名、摘要、盘中标注）与逐日变化
# ---------------------------------------------------------------------------

SUP, RES = "SUPPORT-2026-10-23", "RESISTANCE-2026-10-27"


def _tz(rel: str, upto: str, live: dict | None = None) -> dict:
    from tested_zones import compute_tested_zones

    return compute_tested_zones(_bars(rel, upto), live)


def _scale(tz: dict, scale: str) -> dict:
    return next(s for s in tz["scales"] if s["scale"] == scale)


def _zone(tz: dict, scale: str, zid: str) -> dict:
    return next(z for z in _scale(tz, scale)["zones"] if z["zone_id"] == zid)


def _brief(z: dict) -> list[tuple[str, float, bool]]:
    return [(t["day"], t["price"], t["confirmed"]) for t in z["touches"]]


def _changes(tz: dict) -> dict[tuple[str, str], dict]:
    return {(c["object_id"], c["change"]): c for c in tz["changes"]}


def test_g1_2026_11_13() -> None:
    tz = _tz(CASE, "2026-11-13")
    assert (tz["prev_bar_day"], tz["atr_basis"]) == ("2026-11-12", "STRUCTURE_ATR20")
    db = _zone(tz, "MAJOR", "MAJOR-" + SUP)
    assert db["name_cn"] == "双底" and _brief(db) == [("2026-10-23", 3574.27, True), ("2026-11-03", 3580.38, True)]
    assert (db["state"], db["neckline_price"], db["break_price"], db["confirm_price"]) == ("HOLDING", 3708.2, 3547.69, None)
    dt = _zone(tz, "MAJOR", "MAJOR-" + RES)
    assert _brief(dt) == [("2026-10-27", 3708.2, True), ("2026-11-05", 3701.59, False)]
    assert (dt["name_cn"], dt["state"], dt["confirm_price"]) == ("双顶（第 2 次测试进行中，未确认）", "TESTING", 3590.54)
    pf = _scale(tz, "MAJOR")["platform"]
    assert (pf["lower_price"], pf["upper_price"]) == (3574.27, 3708.2)
    md = _zone(tz, "MINOR", "MINOR-SUPPORT-2026-11-06")
    assert _brief(md) == [("2026-11-06", 3610.2, True), ("2026-11-13", 3623.84, False)] and md["state"] == "TESTING"
    tt = _zone(tz, "MINOR", "MINOR-" + RES)
    assert tt["name_cn"] == "三重顶" and [t["day"] for t in tt["touches"]] == ["2026-10-27", "2026-11-05", "2026-11-12"]
    assert (tt["sequence"], tt["state"]) == ("FALLING", "HOLDING")
    minor = _scale(tz, "MINOR")
    assert {"zone_id": "MINOR-" + SUP, "same_as_zone_id": "MAJOR-" + SUP} in minor["zones"]
    assert (minor["platform"]["lower_price"], minor["platform"]["upper_price"]) == (3610.2, 3708.2)


def test_g1_2026_11_16_and_changes_from_11_13() -> None:
    tz = _tz(CASE, "2026-11-16")
    tb = _zone(tz, "MAJOR", "MAJOR-" + SUP)
    assert tb["name_cn"] == "三重底（第 3 次测试进行中，未确认）" and tb["touches"][-1]["price"] == 3586.57
    assert (tb["state"], tb["confirm_price"], tb["break_price"]) == ("TESTING", 3694.17, 3547.98)
    dt = _zone(tz, "MAJOR", "MAJOR-" + RES)
    assert dt["state"] == "HOLDING" and dt["touches"][-1]["confirmed"]
    mtb = _zone(tz, "MINOR", "MINOR-" + SUP)  # 有未确认触点：两级确认价不同，次级别照常输出全部字段
    assert _brief(mtb) == _brief(tb) and mtb["confirm_price"] == 3640.37
    minor = _scale(tz, "MINOR")
    assert all(z["zone_id"] != "MINOR-SUPPORT-2026-11-06" for z in minor["zones"])
    pf = minor["platform"]
    assert (pf["lower_price"], pf["upper_price"], pf["lower_touch_count"], pf["upper_touch_count"]) == (3574.27, 3708.2, 3, 3)
    ch = _changes(tz)
    assert ch[("MAJOR-" + SUP, "TOUCH_ADDED")]["detail_cn"] == "三重底新增第 3 个低点 2026-11-16 3586.57（未确认）"
    assert (ch[("MAJOR-" + SUP, "STATE_CHANGED")]["from"], ch[("MAJOR-" + SUP, "STATE_CHANGED")]["to"]) == ("HOLDING", "TESTING")
    assert ("MAJOR-" + RES, "TOUCH_CONFIRMED") in ch
    assert (ch[("MAJOR-" + RES, "STATE_CHANGED")]["from"], ch[("MAJOR-" + RES, "STATE_CHANGED")]["to"]) == ("TESTING", "HOLDING")
    gone = ch[("MINOR-SUPPORT-2026-11-06", "ZONE_DISSOLVED")]
    assert (gone["reason"], gone["threshold_changed"]) == ("TOUCH_REPLACED", False)
    scales = [c["scale"] for c in tz["changes"]]
    assert scales == sorted(scales) and len(scales) <= 12  # 主级别在前


def test_g1_2026_11_17_break_price_does_not_follow_the_unconfirmed_low() -> None:
    tz, prev = _tz(CASE, "2026-11-17"), _tz(CASE, "2026-11-16")
    tb = _zone(tz, "MAJOR", "MAJOR-" + SUP)
    assert _brief(tb) == [("2026-10-23", 3574.27, True), ("2026-11-03", 3580.38, True), ("2026-11-17", 3565.59, False)]
    assert (tb["sequence"], tb["state"], tb["zone_low_price"], tb["zone_high_price"]) == ("MIXED", "TESTING", 3574.27, 3580.38)
    assert (tb["neckline_price"], tb["measured_move_price"], tb["break_price"], tb["confirm_price"]) == (3708.2, 3842.13, 3548.04, 3672.56)
    assert _zone(tz, "MINOR", "MINOR-" + SUP)["confirm_price"] == 3619.07
    dt = _zone(tz, "MAJOR", "MAJOR-" + RES)
    assert (dt["state"], dt["break_price"]) == ("HOLDING", 3734.43)
    tt = _zone(tz, "MINOR", "MINOR-" + RES)
    assert (tt["name_cn"], tt["state"]) == ("三重顶", "HOLDING")
    pf = _scale(tz, "MINOR")["platform"]
    assert (pf["lower_touch_count"], pf["upper_touch_count"]) == (3, 3)
    # 新低 3565.59 低于价区下沿但未确认：失效价只随 ATR 变化（0.5 × ATR 之差），不跟着新低往下移
    prev_tb = _zone(prev, "MAJOR", "MAJOR-" + SUP)
    assert tb["break_price"] - prev_tb["break_price"] == pytest.approx(-0.5 * (tz["atr_abs"] - prev["atr_abs"]), abs=0.011)
    assert [(c["object_id"], c["change"]) for c in tz["changes"] if c["scale"] == "MAJOR"] == [("MAJOR-" + SUP, "TOUCH_UPDATED")]
    assert tz["changes"][0]["detail_cn"] == "三重底的未确认低点更新为 2026-11-17 3565.59"


def test_summary_cn_is_built_from_the_zone_own_fields() -> None:
    z = _zone(_tz(CASE, "2026-11-17"), "MAJOR", "MAJOR-" + SUP)
    assert z["summary_cn"] == (
        "三重底（第 3 次测试进行中，未确认）：2026-10-23 3574.27、2026-11-03 3580.38、2026-11-17 3565.59（未确认）；"
        "价区 3574.27~3580.38，颈线 3708.20。第 3 个低点 3565.59 尚未确认：之后最高价达到 3672.56 即确认；收盘跌破 3548.04 判定破位。"
    )


def test_head_shoulders_output_fields() -> None:
    hs = _scale(_tz(SH, "2026-06-26"), "MAJOR")["head_shoulders"]
    (top,) = [h for h in hs if h["name_cn"] == "头肩顶"]
    assert (top["left_shoulder"], top["head"], top["right_shoulder"]) == (
        {"day": "2026-03-03", "price": 4197.23}, {"day": "2026-05-14", "price": 4258.86}, {"day": "2026-06-23", "price": 4175.35}
    )
    assert top["neckline_points"] == [{"day": "2026-03-23", "price": 3794.68}, {"day": "2026-06-08", "price": 3927.85}]
    assert top["right_shoulder_confirmed"] and top["state"] == "FORMING"
    assert top["measured_move_price"] == pytest.approx(top["neckline_today_price"] - top["height_abs"], abs=0.02)


def test_threshold_change_is_flagged_not_reported_as_structure_change() -> None:
    tz = _tz(MOUTAI, "2026-10-15")
    major = _scale(tz, "MAJOR")
    assert major["zigzag_threshold_pct"] == 3.11
    flagged = [c for c in tz["changes"] if c["scale"] == "MAJOR"]
    assert flagged and all(c["threshold_changed"] for c in flagged)
    assert all(c["detail_cn"].endswith("（阈值由 3.00% 变为 3.11%，按新阈值重算）") for c in flagged)
    assert [z["zone_id"] for z in major["zones"]] == ["MAJOR-RESISTANCE-2026-08-03"]


def test_minor_scale_skipped_when_not_finer_than_major(monkeypatch: pytest.MonkeyPatch) -> None:
    import tested_zones

    both = _tz(CASE, "2026-11-17")
    monkeypatch.setattr(tested_zones, "minor_threshold", lambda atr_frac, major_thr: None)
    tz = _tz(CASE, "2026-11-17")
    minor = _scale(tz, "MINOR")
    assert minor["skipped_reason"].startswith("次级别阈值") and minor["zones"] == [] and minor["platform"] is None
    assert _scale(tz, "MAJOR") == _scale(both, "MAJOR")
    assert all(c["scale"] == "MAJOR" for c in tz["changes"])


def _no_intraday(tz: dict) -> dict:
    return {**tz, "scales": [{**s, "zones": [{k: v for k, v in z.items() if k != "intraday_note"} for z in s["zones"]]} for s in tz["scales"]]}


def test_intraday_bar_only_annotates_and_never_moves_zones() -> None:
    """铁律 0：盘中K线只进 intraday_note。用录制行情里 2025-03-14 那根真实K线充当盘中K线。"""
    from tested_zones import compute_tested_zones

    bars = _bars(SH)
    i = next(k for k, b in enumerate(bars) if b["day"] == "2025-03-14")
    closed, live = compute_tested_zones(bars[:i]), compute_tested_zones(bars[:i], bars[i])
    assert _no_intraday(live) == _no_intraday(closed)
    z = _zone(live, "MINOR", "MINOR-RESISTANCE-2025-02-27")
    assert z["state"] == "HOLDING"
    assert z["intraday_note"] == "盘中最高 3421.23 已高于失效价 3410.01（未收盘，不作确认）"
    assert _zone(closed, "MINOR", "MINOR-RESISTANCE-2025-02-27")["intraday_note"] is None


def test_short_history_outputs_valid_structure() -> None:
    from tested_zones import compute_tested_zones

    for n in (30, 60):
        tz = compute_tested_zones(_bars(MOUTAI)[:n])
        assert [s["scale"] for s in tz["scales"]] == ["MAJOR", "MINOR"]
        json.dumps(tz, allow_nan=False)


def test_zone_prices_are_quotable_but_heights_and_atr_are_not() -> None:
    from price_text import collect_price_whitelist

    tz = _tz(CASE, "2026-11-17")
    prices = set(collect_price_whitelist({"structure": {"tested_zones": tz}}, 2, [])["prices"])
    z = _zone(tz, "MAJOR", "MAJOR-" + SUP)
    assert {z["break_price"], z["confirm_price"], z["neckline_price"], z["measured_move_price"], z["zone_low_price"]} <= prices
    assert {t["price"] for t in z["touches"]} <= prices
    assert z["height_abs"] not in prices and tz["atr_abs"] not in prices


def test_tested_zones_size_stays_under_hard_bound() -> None:
    """D4：数量上限（每侧 3 个价区、8 个触点、12 条变化）给出硬上界；实测最大 23.2 KB，这里守护 32 KB。"""
    from tested_zones import compute_tested_zones

    for rel in (SH, MOUTAI, BYD, CASE):
        bars = _bars(rel)
        for cut in range(450, len(bars) + 1, 25):
            tz = compute_tested_zones(bars[:cut])
            assert len(json.dumps(tz, ensure_ascii=False, indent=2).encode("utf-8")) <= 32 * 1024


# ---------------------------------------------------------------------------
# 任务 4：接入结构层、回测与因果审计
# ---------------------------------------------------------------------------


def test_structure_layer_carries_tested_zones_and_can_skip_them() -> None:
    from structure import compute_structure_layer
    from tested_zones import compute_tested_zones

    bars = load_recorded(SH)
    layer = compute_structure_layer(bars, None, 120)
    assert layer["tested_zones"] == compute_tested_zones(layer["completed"], None, decimals=layer["price_decimals"])
    assert compute_structure_layer(bars, None, 120, with_tested_zones=False)["tested_zones"] is None


def test_derived_prices_follow_the_tick_table_on_hk() -> None:
    """港股价位表按价格分档（20~50 港元 0.05、200~500 港元 0.2）：推算出的价位必须落在价位表上。"""
    from market_rules import round_to_tick
    from structure import compute_structure_layer

    bars = load_recorded("yahoo_daily/0700.HK.json")
    layer = compute_structure_layer(bars, None, 120, round_price=lambda p, day: round_to_tick(p, "HK", "equity", day))
    day = layer["completed"][-1]["day"]
    derived = [
        z[k] for s in layer["tested_zones"]["scales"] for z in s["zones"]
        for k in ("break_price", "confirm_price", "measured_move_price") if z.get(k) is not None
    ]
    assert derived
    assert all(p == round_to_tick(p, "HK", "equity", day) for p in derived)


def test_decisions_do_not_depend_on_tested_zones(replay_recorded) -> None:
    """开与关多次测试价区：除 tested_zones 外，内部报告（规则分、评级、动作、否决、计划价位）逐项相同。"""
    import data_gateway
    from analysis import analyze_bundle

    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "structure_bars": 120, "horizon_bars": 15}
    full = data_gateway.load_bundle(req)
    bars, bench = full["target"]["bars"], full["benchmark"]
    for t in range(len(bars) - 200, len(bars), 25):
        cut_day = bars[t]["day"]
        bundle = {
            **full,
            "target": {**full["target"], "bars": bars[: t + 1], "intraday": None},
            "benchmark": {**bench, "bars": [b for b in bench["bars"] if b["day"] <= cut_day]},
        }
        on = analyze_bundle(bundle, req, include_replay=False, include_forecast=False, include_tested_zones=True)
        off = analyze_bundle(bundle, req, include_replay=False, include_forecast=False, include_tested_zones=False)
        assert on["mathematical_indicators"]["structure_levels"].pop("tested_zones") is not None
        assert off["mathematical_indicators"]["structure_levels"].pop("tested_zones") is None
        assert on == off, cut_day


def test_causal_replay_audit_covers_tested_zones() -> None:
    from evaluation import run_causal_replay_audit

    audit = run_causal_replay_audit(load_recorded(SH), check_steps=4)
    assert "tested_zones" in audit["modules_audited"]
    assert audit["passed"], audit["mismatches"]
