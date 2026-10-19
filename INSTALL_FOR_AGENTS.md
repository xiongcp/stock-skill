# 给 AI 助手的安装指引：kline-structure-chart

> 本文写给**正在替用户安装本技能的 AI 助手**（Claude Code、Codex、Gemini CLI、Cursor、OpenCode、Hermes Agent 等）。
> 用户只需在对话里说一句"请按 https://raw.githubusercontent.com/xiongcp/stock-skill/main/INSTALL_FOR_AGENTS.md 安装 kline-structure-chart 技能"，你按下面的步骤执行即可。

## 0. 安全边界（先读）

- 只执行本文列出的命令，每一步都把命令和结果告诉用户。
- **不使用 `sudo`，不修改系统 Python。** 需要安装 uv、Python 或中文字体等系统级工具时，先问用户，用户同意后才执行，或把命令交给用户自己执行。
- 目标位置已有同名目录或软链时，**先问用户**，不得直接删除或覆盖；用户同意后改名备份为 `kline-structure-chart.bak.<时间>`。
- 本技能只做技术分析，不会下单。安装过程中不需要、也不得索要任何券商账号、密码或 API Key。

## 1. 确定技能目录

按你自己的身份选择技能目录（全局安装）。不确定时问用户，或使用通用目录 `~/.agents/skills`。

| 你是 | 技能目录 | `install.sh --agents` 取值 |
| --- | --- | --- |
| Claude Code | `~/.claude/skills` | `claude` |
| Codex（及读取 `~/.agents/skills` 的工具） | `~/.agents/skills` | `codex` |
| Hermes Agent | `~/.hermes/skills` | `hermes` |
| Gemini CLI | `~/.gemini/skills` | `gemini` |
| Cursor | `~/.cursor/skills` | `cursor` |
| OpenCode | `~/.config/opencode/skills` | `opencode` |

用户只想装到当前项目时，Claude Code 用 `<项目>/.claude/skills`，其余用 `<项目>/.agents/skills`（`install.sh` 加 `--project <项目目录>`）。

## 2. 检查前置条件

```bash
git --version
python3 --version
uv --version
```

- `git` 必需。缺失时请用户安装（macOS：`xcode-select --install`）。
- 运行期只需要 **Python ≥ 3.12 + Pillow**。`uv` 是推荐工具，不是必需：它能不借助 sudo 自动下载 Python 3.12 并按 `uv.lock` 安装锁定版本的依赖。
- 有 `uv` → 走第 3 节方式 A；没有 `uv`：问用户是否允许安装 uv（官方安装器，只写入用户目录）。同意 → 方式 A 并加 `--install-uv`；不同意 → 方式 B。

## 3. 方式 A：一键脚本（macOS / Linux，推荐）

把 `<agent>` 换成第 1 节表格里的取值：

```bash
curl -fsSL https://raw.githubusercontent.com/xiongcp/stock-skill/main/install.sh | bash -s -- --agents <agent>
```

- 用户同意安装 uv 时追加 `--install-uv`（你运行的是非交互 shell，脚本不会弹出询问；不加该参数且缺 uv 会直接退出）。
- 想先看会做什么：追加 `--dry-run`，把输出给用户确认后再去掉该参数重跑。
- 脚本会：克隆到 `~/.local/share/stock-skill`（已存在则 `git pull --ff-only`）→ `uv sync --frozen --python 3.12` → 软链到技能目录 → 离线自检与 `doctor`。重复执行即更新。

Windows（PowerShell，脚本尚未在 Windows 实测）：

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/xiongcp/stock-skill/main/install.ps1))) -Agents <agent>
```

## 4. 方式 B：不用 uv（venv + pip）

需要 Python ≥ 3.12。`python3 --version` 低于 3.12 时，请用户安装 Python 3.12+（macOS：`brew install python@3.12`；Linux：发行版包管理器；Windows：python.org），或改走方式 A。下面把 `python3.12` 换成用户机器上 3.12 及以上的解释器名，`<技能目录>` 换成第 1 节的目录：

```bash
git clone --depth 1 https://github.com/xiongcp/stock-skill.git ~/.local/share/stock-skill
```

```bash
cd ~/.local/share/stock-skill/kline-structure-chart && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
mkdir -p <技能目录> && ln -s ~/.local/share/stock-skill/kline-structure-chart <技能目录>/kline-structure-chart
```

- `requirements.txt` 只有 Pillow（版本与 `uv.lock` 一致）；所有指标与风控计算只用 Python 标准库。
- 方式 B 安装后，`SKILL.md` 里的 `uv run python` 一律换成 `.venv/bin/python`（Windows：`.venv\Scripts\python`），工作目录仍是技能目录。`SKILL.md`「运行环境」一节写明了这条替换规则，之后使用技能的会话会照此执行。

## 5. 验证

在技能目录（`~/.local/share/stock-skill/kline-structure-chart`）执行，方式 B 把 `uv run python` 换成 `.venv/bin/python`：

```bash
uv run python scripts/doctor.py
```

- 逐项显示 ✅/❌ 与修复建议。数据源几项需要联网；Linux 缺中文字体时按提示请用户安装（需要 sudo，由用户执行）。
- 可选：联网跑一次真实分析，确认退出码为 0 且生成 `*_report.md` 等文件：

```bash
uv run python scripts/analyze_and_forecast.py --market CN --symbol 600519.SH --output-dir artifacts/smoke
```

## 6. 完成后告诉用户

1. 安装位置（代码目录与技能目录软链）和 `doctor` 结果，未通过项原样转述修复建议。
2. **需要新开一个会话**，技能才会被加载；之后直接问"帮我分析茅台的 K 线"即可。
3. 更新：重新执行同一条安装命令（方式 B：`git -C ~/.local/share/stock-skill pull --ff-only`）。
4. 卸载：`bash ~/.local/share/stock-skill/install.sh --uninstall`（方式 B：删除技能目录下的软链与 `~/.local/share/stock-skill`，删除前先向用户确认）。

Claude Code 用户也可以自己在会话里执行 `/plugin marketplace add xiongcp/stock-skill` 和 `/plugin install kline-structure-chart@stock-skill`（斜杠命令需要用户亲自输入）。
