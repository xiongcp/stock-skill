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
