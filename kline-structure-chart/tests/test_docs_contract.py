"""文档契约：AGENTS.md / SKILL.md / references 与代码保持同步，可移植且不夸大。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = SKILL_DIR.parent
AGENTS = REPO_DIR / "AGENTS.md"
SKILL = SKILL_DIR / "SKILL.md"
DOCS = [SKILL, *sorted((SKILL_DIR / "references").glob("*.md"))]


def _agents_text() -> str:
    if not AGENTS.exists():
        pytest.skip("AGENTS.md 不随 skill 分发，仅在源码仓库中检查")
    return AGENTS.read_text(encoding="utf-8")


def test_no_machine_specific_absolute_paths() -> None:
    docs = DOCS + ([AGENTS] if AGENTS.exists() else [])
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        assert not re.search(r"(/Users/|/home/|[A-Za-z]:\\\\)", text), f"{doc.name} 含本机绝对路径"


def test_skill_md_references_existing_files() -> None:
    text = SKILL.read_text(encoding="utf-8")
    refs = set(re.findall(r"`((?:references|scripts|assets|tests)/[^`\s]+)`", text))
    refs |= set(re.findall(r"(?:python|--data) ((?:scripts|tests)/[\w./-]+)", text))
    assert refs, "SKILL.md 应引用技能内文件"
    missing = [r for r in refs if not (SKILL_DIR / r).exists()]
    assert not missing, f"SKILL.md 引用了不存在的文件: {missing}"


def test_skill_md_keeps_runtime_contract_self_contained() -> None:
    text = SKILL.read_text(encoding="utf-8")
    for must in ("DATA_UNAVAILABLE", "quality.status", "rule_score", "calibration_audit", "risk_officer_veto", "禁止合成数据"):
        assert must in text, f"SKILL.md 缺少运行期规则关键字 {must}"


def test_agents_md_has_authenticity_invariant_and_doc_split() -> None:
    text = _agents_text()
    assert "## 0. 文档分工" in text
    assert "数据真实性" in text and "DATA_UNAVAILABLE" in text
    # 运行期回复规则以 SKILL.md 为准，AGENTS.md 只引用
    assert "以 `SKILL.md`" in text


def test_agents_md_directory_tree_matches_code() -> None:
    text = _agents_text()
    tree = text.split("## 2.", 1)[1].split("## 3.", 1)[0]
    listed = set(re.findall(r"([\w]+\.py)", tree))
    actual = {p.name for p in (SKILL_DIR / "scripts").glob("*.py")} | {p.name for p in (SKILL_DIR / "tests").glob("*.py")}
    assert listed == actual, f"目录树与代码不同步：多列 {listed - actual}，漏列 {actual - listed}"


def test_agents_md_commands_use_relative_existing_paths() -> None:
    text = _agents_text()
    blocks = re.findall(r"```bash\n(.*?)```", text, re.S)
    assert blocks
    for block in blocks:
        for directory in re.findall(r"--directory (\S+)", block):
            assert (REPO_DIR / directory).is_dir(), directory
        for script in re.findall(r"python ((?:scripts|tests)/\S+\.py)", block):
            assert (SKILL_DIR / script).exists(), script
        for data_dir in re.findall(r"--data (\S+)", block):
            assert (SKILL_DIR / data_dir).is_dir(), data_dir


def test_fixture_naming_does_not_impersonate_real_sources() -> None:
    fixtures = SKILL_DIR / "tests" / "fixtures"
    assert not (fixtures / "sina_daily").exists(), "合成数据不得命名为 sina_daily"
    assert not (fixtures / "synthetic_daily").exists(), "禁止保留任何合成数据目录 synthetic_daily"
    assert (fixtures / "recorded" / "MANIFEST.json").exists()


def test_no_synthetic_generators_or_mock_bars_in_code_and_tests() -> None:
    """所有代码与测试必须使用真实行情，禁止存在合成序列生成器。"""
    py_files = [*(SKILL_DIR / "scripts").glob("*.py"), *(SKILL_DIR / "tests").glob("*.py")]
    forbidden = ("def generate_deterministic_series", "def _random_walk_bars", "def _synthetic_bundle", "allow_synthetic_fallback")
    hits = [
        f"{p.name}: {token}"
        for p in py_files
        if p.name != "test_docs_contract.py"
        for token in forbidden
        if token in p.read_text(encoding="utf-8")
    ]
    assert not hits, f"检测到禁止的合成数据构造器: {hits}"


# ---------------------------------------------------------------------------
# SKILL.md 规范（agentskills.io/specification）与宣称真实性
# ---------------------------------------------------------------------------
SPEC_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def _frontmatter() -> tuple[dict[str, object], str]:
    text = SKILL.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    assert m, "SKILL.md 缺少 YAML frontmatter"
    fields: dict[str, object] = {}
    current: str | None = None
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        if line.startswith("  ") and current == "metadata":
            key, _, val = line.strip().partition(":")
            fields["metadata"][key.strip()] = val.strip().strip('"')  # type: ignore[index]
            continue
        key, _, val = line.partition(":")
        current = key.strip()
        fields[current] = {} if current == "metadata" and not val.strip() else val.strip().strip('"')
    return fields, m.group(2)


def test_skill_frontmatter_follows_agent_skills_spec() -> None:
    fields, body = _frontmatter()
    assert set(fields) <= SPEC_FIELDS, f"非规范字段: {set(fields) - SPEC_FIELDS}"
    name = str(fields["name"])
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) and len(name) <= 64
    assert name == SKILL_DIR.name
    desc = str(fields["description"])
    assert 0 < len(desc) <= 1024
    assert "Use when" in desc and "Not for" in desc, "description 需写明何时使用与不适用场景"
    assert re.search(r"[一-鿿]", desc) and re.search(r"[A-Za-z]{20,}", desc.replace(" ", "")), "description 需中英双语"
    compat = str(fields["compatibility"])
    assert 0 < len(compat) <= 500 and "uv" in compat and "DATA_UNAVAILABLE" in compat
    meta = fields["metadata"]
    assert isinstance(meta, dict) and set(meta) >= {"author", "version"}
    assert all(isinstance(v, str) for v in meta.values())
    assert len(body.splitlines()) < 500


def test_skill_body_has_output_contract_and_failure_handling() -> None:
    _, body = _frontmatter()
    for heading in ("何时使用", "运行后必检", "回复契约", "失败处理", "参考文档"):
        assert heading in body, f"SKILL.md 缺少章节：{heading}"
    assert "不构成投资建议" in body


def test_versions_are_in_sync() -> None:
    import json

    fields, _ = _frontmatter()
    pyproject = (SKILL_DIR / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    assert fields["metadata"]["version"] == version  # type: ignore[index]
    schema = json.loads((SKILL_DIR / "assets" / "analysis.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["enum"] == [version]
    analysis_py = (SKILL_DIR / "scripts" / "analysis.py").read_text(encoding="utf-8")
    assert f'"schema_version": "{version}"' in analysis_py
    assert f'"engine_version": "{version}"' in analysis_py


# 已核实未实现或已移除的能力，任何文档、注释与界面文案都不得再宣称
OVERCLAIMS = (
    "Lo, Mamaysky", "头肩", "MACD 背离", "MACD底背离", "RSI/MACD", "CHoCH 特征", "BEARISH_CHoCH",
    "正交特征", "量价正交", "筹码峰", "公式证据。", "KERNEL_DOUBLE", "WLS_", "confidence_score",
    "Walk-Forward", "Meta-Labeling 过滤", "隔夜跳空波动率修正", "动态修正保形", "defaults.json",
)


def test_no_unimplemented_capability_claims() -> None:
    files = [SKILL, *(SKILL_DIR / "references").glob("*.md"), *(SKILL_DIR / "scripts").glob("*.py")]
    if AGENTS.exists():
        files.append(AGENTS)
    hits = [
        f"{f.name}: {phrase}"
        for f in files
        for phrase in OVERCLAIMS
        if phrase in f.read_text(encoding="utf-8")
    ]
    assert not hits, f"残留不实宣称: {hits}"
