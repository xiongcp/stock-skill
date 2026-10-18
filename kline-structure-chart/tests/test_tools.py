"""安装配套工具：名称转代码（腾讯证券搜索录制回放）、环境自检 doctor、插件市场清单。全部离线。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import RECORDED_DIR

import doctor
import resolve_symbol

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = SKILL_DIR.parent


@pytest.fixture
def replay_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """用 tests/record_fixtures.py 录制的腾讯证券搜索原始响应回放。"""
    recorded = {}
    for path in (RECORDED_DIR / "tencent_search").glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        recorded[payload["query"]] = payload["raw"]

    def fake(query: str) -> str:
        if query not in recorded:
            raise RuntimeError(f"search not recorded: {query}")
        return recorded[query]

    monkeypatch.setattr(resolve_symbol, "fetch_tencent_search_raw", fake)


@pytest.mark.parametrize(
    ("query", "symbol", "asset_type"),
    [
        ("茅台", "600519.SH", "equity"),
        ("maotai", "600519.SH", "equity"),
        ("小米", "01810.HK", "equity"),
        ("平安银行", "000001.SZ", "equity"),
        ("nvda", "NVDA.US", "equity"),
    ],
)
def test_resolve_symbol_unique(replay_search, query: str, symbol: str, asset_type: str) -> None:
    out = resolve_symbol.resolve(query)
    assert out["status"] == "RESOLVED" and out["symbol"] == symbol and out["asset_type"] == asset_type
    assert f"--symbol {symbol}" in out["command"] and out["source"] == "TENCENT_SMARTBOX"


def test_resolve_symbol_ambiguous_lists_candidates(replay_search) -> None:
    out = resolve_symbol.resolve("000001")
    assert out["status"] == "AMBIGUOUS" and "symbol" not in out
    assert {c["symbol"] for c in out["candidates"]} == {"000001.SH", "000001.SZ"}
    tencent = resolve_symbol.resolve("腾讯")
    assert tencent["status"] == "AMBIGUOUS" and "00700.HK" in {c["symbol"] for c in tencent["candidates"]}
    # --market 过滤后仍可能多个（腾讯控股 / 腾讯音乐），必须让用户选
    assert all(c["market"] == "HK" for c in resolve_symbol.resolve("腾讯", market="HK")["candidates"])
    index = resolve_symbol.resolve("沪深300", asset_type="index")
    assert {c["symbol"] for c in index["candidates"]} == {"000300.SH", "399300.SZ"}


def test_resolve_symbol_filters_unsupported_listings(replay_search) -> None:
    """人民币柜台、场外粉单、权证牛熊证、场外基金不作为候选。"""
    for query in ("小米", "腾讯", "brk", "沪深300"):
        for c in resolve_symbol.resolve(query)["candidates"]:
            code = c["symbol"].split(".")[0]
            assert not (c["market"] == "HK" and code.startswith("8")), c
            assert c["vendor_type"] in resolve_symbol._TYPE_MAP, c
    assert "BRK.B.US" in {c["symbol"] for c in resolve_symbol.resolve("brk")["candidates"]}
    # 过滤深市 988xxx 外币镜像指数，并支持常见指数口语别名（沪指 / 恒生科技 / 标普500 / 纳指）
    raw_cyb = 'v_hint="sz~399006~创业板指~cybz~ZS^sz~988007~创业板指(美元)(CNH)~cybzmy~ZS";'
    cyb = resolve_symbol.resolve("创业板指", raw=raw_cyb)
    assert [c["symbol"] for c in cyb["candidates"]] == ["399006.SZ"]
    assert resolve_symbol.resolve("沪指", raw='v_hint="N";')["symbol"] == "000001.SH"
    assert resolve_symbol.resolve("恒生科技", raw='v_hint="N";')["symbol"] == "HSTECH.HK"
    assert resolve_symbol.resolve("标普500", raw='v_hint="N";')["symbol"] == "SPY.US"
    assert resolve_symbol.resolve("纳指", raw='v_hint="N";')["symbol"] == "QQQ.US"


def test_resolve_symbol_not_found_exits_invalid_argument(replay_search, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    with pytest.raises(ValueError, match="找不到"):
        resolve_symbol.resolve("zzqqxxnotexist")
    with pytest.raises(ValueError):
        resolve_symbol.resolve("x" * 40)
    monkeypatch.setattr(sys, "argv", ["resolve_symbol.py", "zzqqxxnotexist"])
    with pytest.raises(SystemExit) as exc:
        resolve_symbol.main()
    assert str(exc.value).startswith("[INVALID_ARGUMENT]")


def test_resolve_symbol_network_failure_exits_data_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["resolve_symbol.py", "茅台"])
    with pytest.raises(SystemExit) as exc:
        resolve_symbol.main()  # conftest 已断网
    assert str(exc.value).startswith("[DATA_UNAVAILABLE]")


# ---------------------------------------------------------------------------
# doctor：断网时数据源项为 ❌，其余项可离线通过
# ---------------------------------------------------------------------------
def test_doctor_offline_network_items_fail_others_pass(tmp_path: Path) -> None:
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / doctor.SKILL_NAME).symlink_to(SKILL_DIR)
    checks = doctor.run_checks(home=tmp_path)
    by_name = {c.name: c for c in checks}
    sources = [c for c in checks if c.name.startswith("数据源")]
    assert len(sources) == 4 and not any(c.ok for c in sources), "conftest 断网时数据源项必须为 ❌"
    assert all(c.fix for c in sources)
    for name in ("Python 版本", "uv", "Python 依赖", "中文字体", "技能注册：Claude Code"):
        assert by_name[name].ok, by_name[name]
    assert all(c.ok for c in doctor.run_checks(offline=True, home=tmp_path))


def test_doctor_reports_missing_and_broken_skill_links(tmp_path: Path) -> None:
    missing = doctor.check_skill_links(tmp_path)
    assert len(missing) == 1 and not missing[0].ok and "install.sh" in missing[0].fix
    agents = tmp_path / ".agents" / "skills"
    agents.mkdir(parents=True)
    (agents / doctor.SKILL_NAME).symlink_to(tmp_path / "gone")
    broken = doctor.check_skill_links(tmp_path)
    assert len(broken) == 1 and not broken[0].ok and "失效软链" in broken[0].detail
    project = tmp_path / "proj"
    (project / ".claude" / "skills").mkdir(parents=True)
    (project / ".claude" / "skills" / doctor.SKILL_NAME).symlink_to(SKILL_DIR)
    assert any(c.ok and "项目" in c.name for c in doctor.check_skill_links(tmp_path, project))


# ---------------------------------------------------------------------------
# 插件市场清单与安装脚本（仅源码仓库中检查）
# ---------------------------------------------------------------------------
def test_claude_plugin_marketplace_points_to_skill() -> None:
    path = REPO_DIR / ".claude-plugin" / "marketplace.json"
    if not path.exists():
        pytest.skip("marketplace.json 不随 skill 分发")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == "stock-skill" and data["owner"]["name"]
    (plugin,) = data["plugins"]
    assert plugin["name"] == SKILL_DIR.name
    assert plugin["source"] == "./" and plugin["strict"] is False
    assert plugin["skills"] == [f"./{SKILL_DIR.name}"]
    assert all((REPO_DIR / s / "SKILL.md").is_file() for s in plugin["skills"])


def test_install_sh_syntax_and_help() -> None:
    script = REPO_DIR / "install.sh"
    if not script.exists():
        pytest.skip("install.sh 不随 skill 分发")
    subprocess.run(["bash", "-n", str(script)], check=True)
    out = subprocess.run(["bash", str(script), "--help"], capture_output=True, text=True, check=True).stdout
    for flag in ("--dir", "--source", "--agents", "--project", "--copy", "--dry-run", "--uninstall", "--update", "--no-test", "--install-uv"):
        assert flag in out, flag
    for rel in ("~/.claude/skills", "~/.agents/skills", "~/.hermes/skills", "~/.gemini/skills", "~/.cursor/skills", "~/.config/opencode/skills"):
        assert rel in out, rel


def test_install_scripts_brace_variables_before_non_ascii_and_dry_run(tmp_path: Path) -> None:
    import re

    for fname in ("install.sh", "install.ps1"):
        script = REPO_DIR / fname
        if not script.exists():
            pytest.skip(f"{fname} 不随 skill 分发")
        text = script.read_text(encoding="utf-8")
        # 防止 macOS Bash 3.2 (set -u) 或 PowerShell 将紧邻中文全角字符的 $VAR 误解析为未定义变量
        bad = [
            f"{fname}:{i}: {m.group(0)}"
            for i, line in enumerate(text.splitlines(), 1)
            if not line.lstrip().startswith("#")
            for m in re.finditer(r"\$([A-Za-z_][A-Za-z0-9_]*)([^\x00-\x7F])", line)
        ]
        assert not bad, f"安装脚本中存在未加花括号的紧邻非 ASCII 变量引用: {bad}"

    sh_script = REPO_DIR / "install.sh"
    dry_install = subprocess.run(
        ["bash", str(sh_script), "--source", str(REPO_DIR), "--dir", str(tmp_path / "repo"), "--agents", "claude,codex", "--dry-run"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "演练模式" in dry_install and "检查核心脚本完整性" in dry_install
    dry_uninst = subprocess.run(
        ["bash", str(sh_script), "--dir", str(tmp_path / "repo"), "--agents", "claude,codex", "--uninstall", "--dry-run"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "卸载 kline-structure-chart" in dry_uninst
