# 更新日志

格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号与 `kline-structure-chart/SKILL.md`、`pyproject.toml`、`assets/analysis.schema.json`、`scripts/analysis.py` 四处保持同步。

## [Unreleased]

### 新增

- 一键安装：`install.sh`（macOS / Linux）与 `install.ps1`（Windows，尚未实测）。默认克隆到 `~/.local/share/stock-skill`，自动探测 Claude Code、Codex、Gemini CLI、Cursor、OpenCode 并软链到各自的技能目录；支持 `--agents`、`--project`、`--copy`、`--dry-run`、`--update`、`--uninstall`、`--no-test`、`--install-uv`。卸载只删除脚本自己创建的内容。
- Claude Code 插件市场清单 `.claude-plugin/marketplace.json`，支持 `/plugin marketplace add xiongcp/stock-skill`；README 写明 `npx skills add xiongcp/stock-skill`。
- `scripts/resolve_symbol.py`：中文名称、拼音或不带后缀的代码通过腾讯证券搜索接口转为本技能代码；多个结果列出候选，找不到以 `[INVALID_ARGUMENT]` 退出。新增录制类别 `tencent_search` 及其真实 fixture。
- `scripts/doctor.py`：环境自检（Python、uv、依赖、中文字体、新浪 / Yahoo / 腾讯连通、技能软链），逐项 ✅/❌ 并给出修复建议。
- 文档：`references/output-fields.md`（结果与回测字段字典）、`references/interpretation-guide.md`（解读指南）；三个市场手册补充交易时段与竞价、节假日来源、科创板与北交所细则、港股碎股、A 股融券限制、港股卖空规则与美股 Reg SHO，每条带生效日期与来源。
- `examples/`：用真实行情（截至 2026-10-16）生成的贵州茅台、腾讯控股、英伟达示例产物与标准回复。
- CI：`.github/workflows/ci.yml` 在 Ubuntu 上运行 pytest、跨窗口不变性检查与安装脚本演练，只用录制数据。

### 变更

- `SKILL.md`：新增"名称转代码"第一步、"问题类型 → 命令参数"决策表、"对外表述与概率规则"与正反回复示例。
- 概率表述规则（AGENTS.md 铁律 5、`SKILL.md`、`references/failure-modes.md`）：只允许带样本数与 95% 区间的"历史条件频率"和锚定历史频率、有界调整并标注的"主观研判概率"两类，且对应 JSON 字段存在后才能输出；目前两类字段都还没有，仍不得给出概率数字。
- README：去掉 `<仓库地址>` 占位符，改为一行命令安装，补充 agent 对照表、更新与卸载、其他安装方式、常见问题与真实截图。

## [5.1.0]

- 当前发布版本。此前的修复记录见 `SKILL_REVIEW_CHECKLIST.md`。
