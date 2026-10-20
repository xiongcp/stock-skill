#!/usr/bin/env python3
"""按预注册修订 v1.1 出验收表：只在 Dev+Val 上判定，Test 仅披露（不出通过/失败结论）。

输入：artifacts/long_backtest/final_oos_metrics.json（已有，不重算、不改 Test）。
判定口径取自 preregistration.json 的 acceptance_criteria（未改动）：
  min_fills / min_independent_episodes / episode 净正率下限 70% / Wilson 95% 下界 / 单笔净均值 > 0。
"""
from __future__ import annotations

import json
from pathlib import Path

from common import ART

AMENDMENT = ART / "preregistration_amendment_v1_1.json"
METRICS = ART / "final_oos_metrics.json"
OUT = ART / "acceptance_dev_val.json"


def judge(split: dict, crit: dict) -> dict:
    f = split["fill_layer_limit_n5"]
    n_fill, n_ep = split["n_fills"], split["n_episodes_fill"]
    rate = f["episode_net_positive_rate_pct"]
    lo = f["episode_wilson_95ci"][0]
    mean = f.get("episode_mean_net_return_pct", f["mean_net_return_pct"])
    checks = {
        "min_fills": n_fill >= crit["min_fills"],
        "min_independent_episodes": n_ep >= crit["min_independent_episodes"],
        "episode_net_positive_rate_ge_70": rate >= crit["min_net_positive_rate_pct"],
        "wilson_lower_gt_50": lo > crit["min_wilson_95ci_lower_pct"],
        "mean_net_return_positive": mean > 0,
    }
    return {
        "n_fills": n_fill,
        "n_episodes": n_ep,
        "episode_net_positive_rate_pct": rate,
        "episode_wilson_95ci": f["episode_wilson_95ci"],
        "two_way_cluster_95ci": f.get("two_way_cluster_95ci"),
        "tp_first_rate_pct": f.get("episode_tp_first_rate_pct", f["tp_first_rate_pct"]),
        "mean_net_return_pct": mean,
        "checks": checks,
        "pass": all(checks.values()),
    }


def main() -> None:
    amend = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    crit = amend["acceptance_criteria_unchanged"]
    res = json.loads(METRICS.read_text(encoding="utf-8"))["reformed_high_conviction_results"]
    out: dict = {"basis": "Dev+Val only; Test disclosed, not judged", "criteria": crit, "markets": {}}
    for m, blk in res.items():
        splits = blk["splits"]
        entry = {name: judge(splits[name], crit) for name in ("dev", "val") if name in splits}
        t = splits.get("test")
        entry["test_disclosure_only"] = (
            {
                "n_fills": t["n_fills"],
                "n_episodes": t["n_episodes_fill"],
                "episode_net_positive_rate_pct": t["fill_layer_limit_n5"]["episode_net_positive_rate_pct"],
                "episode_wilson_95ci": t["fill_layer_limit_n5"]["episode_wilson_95ci"],
                "note": "已被上一轮读取，不是干净留出集；仅披露，不参与判定",
            }
            if t
            else None
        )
        out["markets"][m] = entry
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for m, e in out["markets"].items():
        for name in ("dev", "val"):
            j = e[name]
            failed = [k for k, v in j["checks"].items() if not v]
            print(
                f"{m} {name}: episodes={j['n_episodes']} rate={j['episode_net_positive_rate_pct']}% "
                f"wilson={j['episode_wilson_95ci']} mean={j['mean_net_return_pct']}% pass={j['pass']}"
                + (f" failed={failed}" if failed else "")
            )


if __name__ == "__main__":
    main()
