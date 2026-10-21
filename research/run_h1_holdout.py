#!/usr/bin/env python3
"""留出集 H1 一次性验收（回测与验收口径调整方案第 6、7 步）。

运行前逐项校验 research/preregistration/preregistration_v2.json：冻结文件的 sha256、H1 股票池与行情清单的
sha256、冻结文件与预注册文件在 git 中无未提交改动。任何一项不符就拒绝运行。

只能成功运行一次：结果文件已存在时拒绝运行；启动时写锁文件，锁存在而结果缺失（中途崩溃）时，
需要显式加 --restart-after-crash 才能重跑。`--check` 只做校验，不评估任何 H1 数据。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from common import ART, ROOT

PREREG = ROOT / "research" / "preregistration" / "preregistration_v2.json"
RESULT = ART / "h1_result.json"
LOCK = ART / "h1_started.json"
LEDGER = ART / "trial_ledger.jsonl"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_preregistration(prereg_path: Path = PREREG, check_git: bool = True) -> tuple[dict, list[str]]:
    """返回 (预注册内容, 不符项列表)。列表为空才允许运行 H1。"""
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for rel, digest in prereg["frozen_files_sha256"].items():
        f = ROOT / rel
        if not f.exists():
            problems.append(f"冻结文件缺失：{rel}")
        elif _sha256(f) != digest:
            problems.append(f"冻结文件已改动：{rel}")
    for rel, digest in prereg["frozen_data_sha256"].items():
        f = ROOT / rel
        if not f.exists() or _sha256(f) != digest:
            problems.append(f"冻结数据不符：{rel}")
    if check_git:
        paths = [str(prereg_path.relative_to(ROOT)), *prereg["frozen_files_sha256"]]
        dirty = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.strip()
        if dirty:
            problems.append(f"冻结文件或预注册文件有未提交改动：{dirty.splitlines()[:5]}")
        tracked = subprocess.run(["git", "log", "-1", "--format=%H", "--", str(prereg_path.relative_to(ROOT))],
                                 cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        if not tracked:
            problems.append("预注册文件尚未提交到 git，不能视为已冻结")
    return prereg, problems


def judge(seg: dict, gates: list[str]) -> dict:
    """按预注册门槛判定一个市场；T（命中率 >= 70%）只报告，不参与判定（方案 D1=b）。"""
    crit = seg["criteria_diagnostic"]
    names = {"P1": "P1_mean_net_positive", "P2a": "P2a_rate_excess", "P2b": "P2b_mean_excess", "P3": "P3_sample"}
    checks = {g: bool(crit[names[g]]) for g in gates}
    return {"checks": checks, "T_rate_ge_70_report_only": bool(crit["T_rate_ge_70"]), "pass": all(checks.values())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验预注册与运行条件，不评估任何 H1 数据")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--restart-after-crash", action="store_true")
    args = ap.parse_args()

    prereg, problems = verify_preregistration()
    if problems:
        print("预注册校验未通过，拒绝运行：\n- " + "\n- ".join(problems))
        sys.exit(2)
    if RESULT.exists():
        print(f"H1 已经评估过（{RESULT.name} 已存在），按预注册只能运行一次，拒绝重跑")
        sys.exit(3)
    if LOCK.exists() and not args.restart_after_crash:
        print(f"发现锁文件 {LOCK.name} 但没有结果：上次运行可能中途崩溃。确认未查看任何 H1 结果后，加 --restart-after-crash 重跑")
        sys.exit(4)
    print("预注册校验通过" + ("（仅校验，未评估 H1）" if args.check else ""))
    if args.check:
        return

    import run_dev_region_v2 as v2

    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    LOCK.write_text(json.dumps({"started_at_utc": started, "preregistration_sha256": _sha256(PREREG)}), encoding="utf-8")
    v2.H1_AUTHORIZED = True
    spec = prereg["acceptance"]
    result: dict = {
        "started_at_utc": started,
        "preregistration_sha256": _sha256(PREREG),
        "code_commit": v2._git_head(),
        "in_sample_note": "留出集 H1，一次性评估",
        "markets": {},
    }
    for market in prereg["h1"]["markets"]:
        res = v2.run_market(market, args.workers, mode="h1")
        seg = res["segments"]["all"]
        role = "acceptance" if market in spec["acceptance_markets"] else "diagnostic"
        result["markets"][market] = {"role": role, "verdict": judge(seg, spec["gates"]), **res}
        fl = seg["fill_layer"]
        print(f"[{market} {role}] episodes={fl.get('n_episodes')} days={fl.get('n_signal_days')} "
              f"rate={fl.get('net_positive_rate_pct')}% mean={fl.get('mean_net_return_pct')}% "
              f"verdict={result['markets'][market]['verdict']}", flush=True)
    result["finished_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": result["finished_at_utc"], "script": "research/run_h1_holdout.py",
                             "code_commit": result["code_commit"], "preregistration_sha256": result["preregistration_sha256"],
                             "verdicts": {m: v["verdict"] for m, v in result["markets"].items()}},
                            ensure_ascii=False) + "\n")
    print(f"wrote {RESULT}")


if __name__ == "__main__":
    main()
