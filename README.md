# kline-structure-chart：A股 / 港股 / 美股 K 线数学结构、三重屏障风控与保形预测技能包

`kline-structure-chart` 是一个符合 [Agent Skills 开放规范](https://agentskills.io/specification) 的金融量化与 K 线数学结构分析技能包。装进 Claude Code、Codex、Hermes Agent、Cursor、Gemini CLI、OpenCode 等 AI 助手之后，你只需用自然语言提问（例如："帮我看看贵州茅台的 K 线结构和支撑压力，账户 50 万，带回测"），助手就会自动完成从**真实行情拉取 → 确定性数学结构识别 → 历史条件频率统计 → 三重屏障风控与保形预测 → 离线交互看板与高清图生成 → 受约束研判校验**的完整投研闭环。

![kline-structure-chart 架构与三层分析流水线总览](examples/architecture_overview.png)

---

## 0. 我们究竟解决什么问题？（为什么不能让大模型直接看图猜 K 线）

在传统的大模型看图或直接对话问股中，普遍存在四大致命痛点：

1. **视觉幻觉与价格编造**：让多模态模型（VLM）直接看 K 线截图识别形态或支撑阻力位，极易读错坐标轴、编造不存在的价格或画错趋势线。
2. **前视偏差（Lookahead Bias）与事后诸葛亮**：传统 ZigZag 拐点、双底/双顶、ABC 浪型往往在事后用未来 K 线重绘历史极值，回测看似完美，实盘完全无法复现。
3. **把主观打分伪装成"上涨概率"**：许多工具将几个指标的简单加权分直接宣称为"上涨概率 75%"，既无历史样本支撑，也未经样本外校准检验。
4. **忽视大盘环境与微观交易制度**：在大盘空头崩跌时仍激进推荐个股突破追高；或忽略 A 股 T+1、科创/创业板及双创 ETF ±20% 涨跌幅、港股动态价位表与双边印花税、美股盘前盘后与 VIX 波动放大等真实交易约束。

为此，`kline-structure-chart` 建立了**六大工程铁律（Hard Invariants）**与**三层渐进式量化架构（L1 → L2 → L3）**：

```mermaid
flowchart TD
    A["用户自然语言提问 / CLI 调用<br/>（如：分析茅台 / 00700.HK / NVDA.US）"] --> B["0. 名称解析与多市场真实行情网关<br/>resolve_symbol.py · data_gateway.py"]
    B --> C["盘中未收盘K线剥离 + 点位时间前复权(PIT) + 大盘基准配对<br/>market_rules.py（A股沪深300 / 港股恒指 / 美股SPY）"]
    C --> D["L1 确定性数学结构与多周期策略层<br/>单边因果NW核平滑 · 因果ZigZag · 轨道候选打分拟合<br/>主+备选ABC浪型 · 推动5浪硬规则 · 8类标准情景筛选<br/>蜡烛/双底双顶/RSI背离 · SMC(FVG/OB/BOS) · Volume Profile"]
    D --> E["L2 逐点无前视历史条件频率层<br/>base_rates.py：标准化ATR三重屏障事件研究<br/>观测频率 k/n + 95% Wilson区间 + n&lt;30 Beta-Binomial向基准收缩"]
    D --> F["Triple-Barrier 风控与 Split-Conformal 保形区间层<br/>risk.py · forecast.py · evaluation.py<br/>左右侧入场区/盘中硬止损/+0.90R移动保本/持仓防守/港美做空<br/>鞅中心15日50%&amp;80%保形扇形 + 线上计划逐日无前视回测"]
    E --> G["同源核心数据契约：*_result.json + *_judgment_pack.json<br/>（通过 assets/analysis.schema.json 严苛校验）"]
    F --> G
    G --> H1["*_preview.png<br/>1920×1160 2×SSAA高清结构图"]
    G --> H2["*_dashboard.html<br/>零依赖9图层离线交互终端"]
    G --> H3["*_report.md<br/>零失误中文投研报告"]
    G --> H4["L3 受约束主观研判闭环<br/>validate_judgment.py 校验<br/>score_judgments.py 到期打分<br/>lint_response.py 回复守门"]
```

### 核心架构与六大不可变铁律

1. **数据真实性（Data Authenticity）优先于一切**：只使用新浪、Yahoo、腾讯实时/录制真实行情或用户提供的本地真实行情文件。取不到行情时立即以 `[DATA_UNAVAILABLE]` 非零退出，**严禁回退、估算、补数或生成任何合成行情**。当日未收盘 K 线（按交易所时区与半日市/提前收市规则判定）严格拆出，绝不混入已收盘序列。
2. **100% 算图分离（Compute-Render Separation）**：所有均线、指标、拐点、轨道、ABC 浪型、SMC、Volume Profile、三重屏障点位、15 日保形分位区间全部由 Python 从 OHLCV 序列计算一次并写入 JSON；PNG 与 HTML 渲染器只读取同一份计算结果，**严禁调用视觉模型看图猜数或调用图像生成模型画 K 线**。
3. **先大盘、后个股（Market-First Hierarchy）**：分析任何个股或 ETF 前必须同步加载对应市场大盘基准（A 股 `000300.SH`、港股 `HSI.HK`、美股 `SPY.US`），计算大盘状态分与 20 日相对强弱（RS Line）。大盘极弱（状态分 `< 35`）或缺失基准时，独立风控官自动触发一票否决（`risk_officer_veto = true`）。
4. **严格单边因果与零前视泄漏（Zero Lookahead Bias）**：核平滑采用单边因果 Nadaraya-Watson；每个波段拐点同时记录极值发生日 `day` 与反向达标确认日 `confirmed_at_day`；点位复权（PIT）只消费截至当时已生效的分红送转；回测与历史回放截面按当时截止日重算大盘状态与复权，并经追加 5 倍极端未来价格冲击的因果回放审计验证零泄漏。
5. **跨展示窗口不变性（Cross-Window Invariance）**：`--days N`（60~500）只改变主图展示根数，底层取数固定 760 根、指标在固定 420 根历史上预热、结构在固定 120 根窗口上识别，切换展示天数绝不改变任何指标数值、结构分类或交易点位。
6. **概率与术语边界铁律**：规则分（`rule_score`）是确定性条件加权分，绝不称为"胜率"或"上涨概率"；对外只允许两类受检验的概率数字——**(1) L2 历史条件频率**（附样本数 `n`、95% Wilson 区间与低样本收缩说明）与 **(2) L3 受约束主观研判概率**（锚定 L2、引用 `E1…En` 证据编号、受调整上限约束并记入日志待到期按 Brier 分数检验）。

> 本技能只做规则化技术分析与量化研究验证，不构成任何投资建议，也不会替你下单。

---

## 1. 能分析什么（三市场全品种覆盖与真实图例展示）

| 市场 | 代码格式 | 示例 | 行情来源 | 默认大盘基准 |
| --- | --- | --- | --- | --- |
| A股 | 6 位代码 + `.SH` / `.SZ` / `.BJ` | `600519.SH`、`000002.SZ`、`830799.BJ`、`510300.SH`（ETF）、`000001.SH`（上证指数） | 新浪日线（不复权）+ Yahoo 除权除息事件 | 沪深300 `000300.SH` |
| 港股 | 5 位代码 + `.HK` | `00700.HK`、`01810.HK`、`02800.HK`（ETF）、`HSI.HK`、`HSTECH.HK` | Yahoo 日线；恒生科技指数用腾讯指数日线 | 恒生指数 `HSI.HK` |
| 美股 | 代码 + `.US` | `NVDA.US`、`SPY.US`、`QQQ.US`、`BRK.B.US` | Yahoo 日线 + `^VIX` 恐慌指数 | `SPY.US` |

证券简称、A 股 ST/\*ST 风险警示标识和港股每手股数（Board Lot）会自动从腾讯行情快照获取；常见科创/创业板 20% 涨跌幅 ETF、A 股跨境/债券/黄金/货币 T+0 ETF 以及港交所豁免印花税的 ETF/ETP 代码段均支持自动识别。

### 三市场真实行情生成的高清结构推演图展示

以下三张 `1920×1160` 结构图全部由本技能脚本基于真实行情自动生成（数据截至 2026-10-15，完整报告、JSON 与离线交互看板见 [examples/](examples/README.md)）：

#### (1) A 股示例：贵州茅台（`600519.SH` · 红涨绿跌 · 扩散喇叭形 + 三情景推演 + 15 日保形扇形）

![贵州茅台 600519.SH 结构推演图](examples/600519.SH/600519.SH_preview.png)

#### (2) 港股示例：腾讯控股（`00700.HK` · 红涨绿跌 · 破位下行与 C 浪末端反转推演）

![腾讯控股 00700.HK 结构推演图](examples/00700.HK/00700.HK_preview.png)

#### (3) 美股示例：英伟达（`NVDA.US` · 绿涨红跌 · 超跌反弹与破位下行推演）

![英伟达 NVDA.US 结构推演图](examples/NVDA.US/NVDA.US_preview.png)

---

## 2. 环境要求

- macOS、Linux 或 Windows（Windows 安装脚本尚未实测，见第 10 节）。
- `git` 与 [uv](https://docs.astral.sh/uv/)：uv 用来安装 Python 3.12 和锁定版本的依赖。缺 uv 时安装脚本会询问是否代为安装。
- 网络：安装时需要访问 GitHub 与 PyPI；每次分析需要访问新浪、Yahoo、腾讯的行情接口。没有网络的沙箱环境无法使用（会直接报 `[DATA_UNAVAILABLE]`）。
- 中文字体（画 PNG 用）：macOS 与 Windows 自带；Linux 需要文泉驿或 Noto CJK，安装脚本发现缺字体时会打印对应发行版的安装命令（不会自动 sudo）。

---

## 3. 安装

### 3.1 一行命令安装（推荐）

macOS / Linux：

```bash
curl -fsSL https://raw.githubusercontent.com/xiongcp/stock-skill/main/install.sh | bash
```

Windows（PowerShell）：

```powershell
irm https://raw.githubusercontent.com/xiongcp/stock-skill/main/install.ps1 | iex
```

脚本依次完成：

1. 检查 `git` 和 `uv`（缺 uv 时询问是否用官方安装器安装；非交互环境需要加 `--install-uv`）。
2. 把代码克隆到 `~/.local/share/stock-skill`；已存在时执行 `git pull --ff-only`，所以**重复执行同一条命令就是更新**。
3. 执行 `uv sync --frozen --python 3.12`，按 `uv.lock` 安装依赖。
4. Linux 下检查中文字体。
5. 自动探测已安装的 `claude`、`codex`、`gemini`、`cursor-agent`、`opencode`，把技能目录**软链**到对应的技能目录（见下表）。目标位置已有同名的普通目录时先备份为 `*.bak.<时间>`。
6. 运行离线快速自检（文档契约与工具测试）和环境自检 `doctor`，最后打印下一步。

| Agent | 探测命令 | 全局技能目录 | `--project` 时的目录 |
| --- | --- | --- | --- |
| Claude Code | `claude` | `~/.claude/skills/` | `<项目>/.claude/skills/` |
| Codex（以及读取 `~/.agents/skills` 的其他工具） | `codex` | `~/.agents/skills/` | `<项目>/.agents/skills/` |
| Hermes Agent | `hermes` | `~/.hermes/skills/` | `<项目>/.hermes/skills/` |
| Gemini CLI | `gemini` | `~/.gemini/skills/` | `<项目>/.agents/skills/` |
| Cursor | `cursor-agent` | `~/.cursor/skills/` | `<项目>/.agents/skills/` |
| OpenCode | `opencode` | `~/.config/opencode/skills/` | `<项目>/.agents/skills/` |

目录约定与 [vercel-labs/skills](https://github.com/vercel-labs/skills) 一致。一个都没探测到时，默认注册到 Claude Code 和 `~/.agents/skills/`。

安装完成后**新开一个会话**，试问"帮我分析 600519.SH"或"看看茅台的 K 线"。

### 3.2 安装选项

通过管道运行时用 `bash -s --` 传参，例如：

```bash
curl -fsSL https://raw.githubusercontent.com/xiongcp/stock-skill/main/install.sh | bash -s -- --agents claude,codex --dry-run
```

| 选项（`install.sh`） | Windows（`install.ps1`） | 说明 |
| --- | --- | --- |
| `--dir <目录>` | `-Dir` | 代码目录，默认 `~/.local/share/stock-skill`（也可用环境变量 `STOCK_SKILL_DIR`） |
| `--agents claude,codex` | `-Agents claude,codex` | 只注册到指定 agent；`all` 表示全部五个 |
| `--project <目录>` | `-Project` | 只注册到某个项目（项目级技能目录），不动全局目录 |
| `--copy` | `-Copy` | 复制而不是软链（更新代码后需重新执行脚本） |
| `--install-uv` | `-InstallUv` | 缺 uv 时直接安装，不询问 |
| `--no-test` | `-NoTest` | 跳过安装后的自检 |
| `--dry-run` | `-DryRun` | 只打印将要执行的操作，不做任何修改 |
| `--source <地址或路径>` | `-Source` | 从其他 Git 地址或本地检出安装（开发测试用） |
| `--update` | `-Update` | 更新，等同重新执行安装 |
| `--uninstall` | `-Uninstall` | 卸载 |

### 3.3 更新与卸载

更新：重新执行 3.1 的安装命令即可（软链安装会随 `git pull` 自动生效；`--copy` 安装会重新复制）。

卸载：

```bash
bash ~/.local/share/stock-skill/install.sh --uninstall
```

卸载只删除**本脚本创建的**软链、带安装标记的复制目录和代码目录；其他技能、你自己放在同名位置的目录（会提示"跳过"）以及安装时生成的 `*.bak.*` 备份都不会被删除。安装时用过 `--dir` 或 `--project` 的，卸载时请带上同样的参数。

### 3.4 其他安装方式

**Claude Code 插件市场**：在 Claude Code 里执行

```text
/plugin marketplace add xiongcp/stock-skill
/plugin install kline-structure-chart@stock-skill
```

插件市场清单在 [.claude-plugin/marketplace.json](.claude-plugin/marketplace.json)，直接指向 `kline-structure-chart/` 目录。插件方式不会运行 `uv sync`，首次分析时 `uv run` 会自动安装依赖。

**npx skills（vercel-labs/skills）**：会自动识别已安装的 agent。

```bash
npx skills add xiongcp/stock-skill
```

常用参数：`-g` 安装到用户目录、`-a claude-code` 指定 agent、`--copy` 复制而不软链。

**手动安装**：

```bash
git clone https://github.com/xiongcp/stock-skill.git ~/.local/share/stock-skill
```

```bash
cd ~/.local/share/stock-skill && uv sync --frozen --python 3.12 --directory kline-structure-chart
```

```bash
mkdir -p ~/.claude/skills && ln -sfn ~/.local/share/stock-skill/kline-structure-chart ~/.claude/skills/kline-structure-chart
```

其他 agent 把最后一步的 `~/.claude/skills` 换成 3.1 表格里的目录。

### 3.5 验证安装

以下命令在代码目录（默认 `~/.local/share/stock-skill`）执行。先跑环境自检，逐项显示 ✅/❌ 和修复建议（断网时数据源几项为 ❌，其余可离线通过）：

```bash
uv run --directory kline-structure-chart python scripts/doctor.py
```

再跑离线测试（不访问网络，全部使用录制下来的真实行情回放）：

```bash
uv run --directory kline-structure-chart pytest -q
```

最后联网跑一次真实分析：

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market CN --symbol 600519.SH --output-dir artifacts/smoke
```

失败时脚本以非零码退出：`[DATA_UNAVAILABLE]` 表示取不到真实行情，`[INVALID_ARGUMENT]` 表示代码或参数有误（见第 8 节）。成功时退出码为 0，`kline-structure-chart/artifacts/smoke/` 下会出现 `600519.SH_report.md`、`600519.SH_result.json`、`600519.SH_preview.png`、`600519.SH_dashboard.html` 四个文件。

---

## 4. 在 AI 助手里使用

直接用自然语言提问即可，名称、拼音、代码都可以：

- "帮我分析一下茅台的 K 线结构和支撑压力。"
- "腾讯 00700.HK 未来两周大概的波动区间是多少？带上历史回测。"
- "NVDA 现在的风控计划是什么？我的账户 10 万美元，单笔最多亏 1%。"
- "看看上证指数和创业板指现在的结构。"

助手按技能里的规则工作：

1. 只说了名称时，先用 `scripts/resolve_symbol.py` 通过腾讯证券搜索查代码；匹配到多个证券（比如"腾讯"）时会请你选择，不会凭记忆猜。
2. 按问题类型选择参数并运行脚本（比如给了账户净值才计算股数，问回测才加 `--backtest`）。
3. 先检查数据质量：`UNAVAILABLE` 不给任何价位；`PARTIAL` 把告警放在回答最前面。
4. 以生成的 `*_report.md` 为骨架回答，顺序为：大盘 → 结构与条件推演 → 风控计划 → 未来区间 → 回测 → 文件路径 → 免责声明。报告中的数字不得改动。
5. 风控否决了做多时，否决原因放在最前面。不会给出"上涨概率"之类的数字。

完整示例（真实行情生成的报告、图和标准回复）见 [examples/](examples/README.md)。

小提示：

- 想看回测就说"带回测"。
- 想要建议股数，就告诉它账户净值和单笔风险比例。
- 基本面、新闻、财报不在本技能范围内。

---

## 5. 直接用命令行

所有命令都在仓库根目录执行。

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market HK --symbol 01810.HK --account-equity 300000 --output-dir artifacts/hk --backtest
```

命令执行完会在终端打印一段 JSON 摘要，产物写入 `kline-structure-chart/<--output-dir>/`。

### 5.1 参数

| 参数 | 说明 |
| --- | --- |
| `--symbol`（必填） | 标的代码，格式见第 1 节 |
| `--output-dir`（必填） | 产物目录，相对于 `kline-structure-chart/` |
| `--market` | `CN` / `HK` / `US`；代码带后缀时可以省略 |
| `--asset-type` | `index` / `equity` / `etf`。默认按代码段推断：A 股 51/56/58（沪）、15（深）开头及港交所 ETF/ETP 代码段（02800~02849、03000~03199 等）自动识别为 ETF |
| `--days` | 主图显示多少根 K 线，取值 60~500，默认 120。只影响显示，不影响任何计算结果 |
| `--horizon-bars` | 预测天数，1~60，默认 15 |
| `--as-of` | 只用截至某日（`YYYY-MM-DD`）的数据，用于复盘历史 |
| `--adjustment` | `raw` / `split` / `total_return`。默认个股和 ETF 用前复权（含分红送转），指数不复权 |
| `--account-equity`、`--max-risk-pct` | 账户净值与单笔风险比例（默认 0.01，即 1%）。不传账户净值时不计算股数 |
| `--name`、`--st` | 手动指定证券简称、手动标记 ST。正常情况下会自动获取，无需填写 |
| `--ipo-no-limit` | 标记上市前 5 个交易日、不设涨跌幅的新股 |
| `--color-scheme` | `auto` / `cn_red_up` / `us_green_up`。默认 A 股和港股红涨绿跌，美股绿涨红跌 |
| `--benchmark` | 替换默认大盘基准 |
| `--backtest`、`--backtest-step` | 生成历史计划回放回测；`--backtest-step` 设为大于 1 可以加快速度 |
| `--input`、`--offline` | 使用本地真实行情文件，并且不联网（见 5.2） |

### 5.2 使用本地行情文件

本地文件是一个 JSON 数组，每个元素是一根日 K 线：

```json
[{"day": "2026-10-14", "open": 1270.1, "high": 1286.75, "low": 1266.0, "close": 1286.0, "volume": 2377273}]
```

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market CN --symbol 600519.SH --name 贵州茅台 --input /path/to/bars.json --offline --output-dir artifacts/local
```

对文件的要求和行为：

- 至少需要 130 根 K 线。
- 本地文件没有大盘数据，所以数据质量为 `PARTIAL`，风控会否决做多确认。
- 本地文件也没有除权除息事件，价格按原样使用。

### 5.3 名称转代码与环境自检

只知道名称或拼音时，先查代码（数据源为腾讯证券搜索接口）：

```bash
uv run --directory kline-structure-chart python scripts/resolve_symbol.py 茅台
```

- 唯一匹配时 `status` 为 `RESOLVED`，并给出可直接运行的分析命令；匹配到多个（如"腾讯"、"000001"）时为 `AMBIGUOUS`，列出候选；找不到时以 `[INVALID_ARGUMENT]` 退出。
- 可用 `--market HK`、`--asset-type index` 缩小范围。北交所股票目前搜不到，请直接用 `.BJ` 代码。

环境有问题时运行 `scripts/doctor.py`（见 3.5 节），加 `--offline` 可跳过数据源连通检查。

---

## 6. 输出文件

| 文件 | 内容 |
| --- | --- |
| `*_report.md` | 中文分析报告，依次为：数据说明、风控否决、结论、大盘、结构与条件推演、风控计划、形态证据、未来区间、回测、文件路径、免责声明。数字全部取自 `*_result.json` |
| `*_result.json` | 全部计算结果（通过 `assets/analysis.schema.json` 契约校验） |
| `*_judgment_pack.json` | L3 受约束研判证据包（含可溯源证据编号 `E1…En`、冲突信号 `C1…Cm`、L2 情景基准频率与价格白名单） |
| `*_preview.png` | 1920×1160 结构推演图；数据有问题时带红色告警和水印 |
| `*_dashboard.html` | 离线交互看板：9 个图层开关、悬停查看数值、相对强弱视图、历史回放滑块、导出 PNG |
| `*_backtest.json` | 历史计划回放回测与无前视审计（传 `--backtest` 时生成） |

每个字段的含义、单位和能否为空见 [references/output-fields.md](kline-structure-chart/references/output-fields.md)。完整产物示例见 [examples/](examples/README.md)。

![产物视觉导读：PNG 五大分区与 HTML 九大图层](examples/chart_reading_guide.png)

---

## 7. 怎么读结果

### 7.1 三层量化输出体系（L1 结构 → L2 历史条件频率 → L3 受约束研判）

| 层级 | 核心产出 | 统计口径与对外表述规范 |
| --- | --- | --- |
| **L1 确定性数学结构层** | 轨道拟合形态、主+备选 ABC 浪型、推动 5 浪、8 类标准情景（选 2~5 个活跃情景）、蜡烛/双底双顶/RSI 背离、SMC（FVG/订单块/BOS）、24 桶成交分布（POC/VAH/VAL）、多周期规则分（0~100） | 规则分是确定性条件加权分（均值回归体制下按反转规则调整），**严禁称为胜率或上涨概率**；Volume Profile 是 OHLCV 成交分布近似，不等于真实持仓筹码 |
| **L2 逐点无前视历史条件频率层** | 在标的自身历史上逐日无前视统计各情景触发后 `horizon_bars` 日内先触及标准化目标屏障（如 `+1.55×ATR`）而非失效屏障（如 `-1.05×ATR`）的实际观测频率 `k/n`、95% Wilson 置信区间与到达用时分位数 | 必须称**"历史条件频率"**并附样本数 `n` 与 95% 区间；当 `n < 30` 时标注"低样本"并通过 Beta-Binomial 向市场基准同情景频率收缩；统计的是 ATR 标准化事件而非图中目标价的到达概率 |
| **L3 受约束主观研判层** | 基于 `*_judgment_pack.json`（含 `E1…En` 证据溯源、`C1…Cm` 冲突信号与价格白名单），以 L2 归一化频率为锚点做有界调整（`n>=30` 最多 ±15%，`n<30` 最多 ±10%，互斥情景概率和 = 100%） | 必须通过 `validate_judgment.py` 校验并标注**"主观研判，已记录待检验"**；未改写的 `--auto-init` 预填模板标记为 `AUTO_INIT_TEMPLATE`，不计入 LLM 技能分 |

### 7.2 8 类标准情景库速查（见 [references/scenario-playbook.md](kline-structure-chart/references/scenario-playbook.md)）

每次分析会根据当前截面的轨道位置、量能、RSI 与波段结构，从以下 8 类标准情景中动态筛选出 2~5 个适用情景（当触发位距最新收盘价不足 `0.5×ATR` 时自动归入**「已在进行中」**）：

1. `TREND_CONTINUATION`（趋势延续）：沿上升通道或推动第 3/5 浪顺势推进；
2. `BREAKOUT_RETEST`（突破回踩）：放量站上上轨/颈线/前高后回踩确认；
3. `FALSE_BREAKOUT_REVERSAL`（假突破回落）：冲高刺破上轨或前高后缺量跌回轨道内；
4. `RANGE_OSCILLATION`（区间震荡）：位于轨道内部且距上下边界均超过 `1.0×ATR`；
5. `BREAKDOWN_DECLINE`（破位下行）：收盘跌破下轨、颈线或前低支撑；
6. `C_WAVE_REVERSAL`（C 浪末端反转）：ABC 调整浪中 `C/A` 接近 `1.0` 倍且出现背离或企稳；
7. `OVERSOLD_BOUNCE`（超跌反弹）：`RSI14 < 30` 或收盘偏离 `MA20` 超过 `2.0×ATR`；
8. `APEX_BREAKOUT`（收敛末端变盘）：收敛三角形/楔形距顶点剩余 `0~10` 根 K 线以内且未过期。

### 7.3 L3 受约束主观研判、到期打分与回复校验命令

```bash
# 1. 生成预填研判模板（供 Agent 在此基础上结合 E1..En 证据改写概率与理由）
uv run --directory kline-structure-chart python scripts/validate_judgment.py --result artifacts/smoke/600519.SH_result.json --judgment artifacts/smoke/600519.SH_judgment.json --auto-init

# 2. 校验 Agent 改写后的主观研判 JSON，通过后自动合入 result.json / report.md 并追加至 judgment_log.jsonl
uv run --directory kline-structure-chart python scripts/validate_judgment.py --result artifacts/smoke/600519.SH_result.json --judgment artifacts/smoke/600519.SH_judgment.json

# 3. 对历史已到期（满 horizon_bars 根 K 线）的主观研判进行真实行情回放打分（输出 Brier 分数与技能分 BSS）
uv run --directory kline-structure-chart python scripts/score_judgments.py --log artifacts/judgment_log.jsonl --bars-dir tests/fixtures/recorded/sina_daily

# 4. 对 Agent 最终输出的自然语言回复执行契约守门校验（拦截捏造价格、违规概率术语、漏报否决/免责声明）
uv run --directory kline-structure-chart python scripts/lint_response.py --result examples/600519.SH/600519.SH_result.json --response examples/600519.SH/reply.md
```

### 7.4 阅读报告与图表的核心要点

- **先看数据质量**（`quality.status`）：
  - `OK`：可以正常解读。
  - `PARTIAL`：有告警，比如缺少大盘基准，请先读告警。
  - `UNAVAILABLE`：结果不可用。
  - 盘中运行时，当天未收盘的 K 线会被排除，报告里会注明。
  - 收盘后不久运行时，数据源可能还没入库当天日线（实测北京时间 15:19 新浪仍只到前一交易日）。报告会注明"数据只到某日"，稍后重跑即可；脚本不会用其他来源补数。
- **风控否决优先**：出现"风控否决"时，所有点位只用于观察，不构成做多建议。
- **规则分（0~100）不是胜率**：它是多个技术条件的加权打分，不代表上涨概率。
- **未来区间只描述波动幅度，不判断方向**：
  - 中枢（q50）就是最新收盘价。
  - 50%、80% 区间宽度来自历史波动的分期限校准。
  - 报告同时给出历史样本外覆盖率和约等于多少个独立样本。独立样本很少时，覆盖率本身也不稳定。
  - 只有约 20 个以上独立样本且覆盖率接近名义值时，状态才是"已达标"，否则是"校准观察期"。审计覆盖全部已取到的历史，默认 15 日区间约有 24～42 个独立样本（视历史长度而定），能否达标取决于覆盖率是否接近名义值。
- **回测看样本量**：成交少于 30 笔时，胜率和收益统计参考价值很低，报告会明确提示。胜率后面括号里是 Wilson 95% 区间，样本越少区间越宽（例如 3 笔全亏时为 0%~56%）。
- 形态、浪型、市场状态的解读要点与常见误读见 [references/interpretation-guide.md](kline-structure-chart/references/interpretation-guide.md)。

---

## 8. 故障排查

| 现象 | 原因与处理 |
| --- | --- |
| `[DATA_UNAVAILABLE] ... 网络已禁用(--offline)` | 去掉 `--offline`，或者配合 `--input` 使用本地文件 |
| `[DATA_UNAVAILABLE] ... HTTPError 429` / `timed out` | 行情接口限流或网络不通，稍后重试 |
| `[DATA_UNAVAILABLE] ... Cannot derive ... symbol` | 代码格式不对，按第 1 节的格式补全后缀 |
| `[INVALID_ARGUMENT] Cannot resolve market` | 代码没有后缀，加上 `.SH` / `.SZ` / `.HK` / `.US` 或传 `--market` |
| `[INVALID_ARGUMENT] Ambiguous symbol '000001'` | 需要明确是 `000001.SH`（上证指数）还是 `000001.SZ`（平安银行） |
| `[INVALID_ARGUMENT] --days must be between 60 and 500` | `--days` 超出范围；`--horizon-bars` 同理（1~60） |
| `[DATA_UNAVAILABLE] ... 历史长度不足` | 上市时间太短，或 `--as-of` 太早 |
| `no CJK font found` | 没有中文字体，运行 `scripts/doctor.py` 查看对应发行版的安装命令 |
| `uv: command not found` | 先安装 uv：<https://docs.astral.sh/uv/>，或重新执行安装脚本并加 `--install-uv` |
| 新会话里看不到 `kline-structure-chart` | 运行 `scripts/doctor.py` 检查技能软链；确认注册到了正在使用的 agent 的目录（第 3.1 节表格），然后新开会话 |
| 安装时提示"已存在且不是 Git 仓库" | `--dir` 指向的目录里已有别的文件，换一个目录或先手动清理 |
| `resolve_symbol.py` 以 `[DATA_UNAVAILABLE]` 退出 | 腾讯证券搜索接口不可用，直接提供带后缀的完整代码 |
| 报告提示"数据只到某日：最近已收盘的交易日……尚未提供" | 数据源收盘后入库有延迟，或该标的停牌；稍后重跑 |
| 报告提示"港股每手股数未能获取" | 腾讯快照暂时不可用，股数按 100 股/手假设计算，下单前请向券商核对 |

---

## 9. 常见问题

**Q：能告诉我上涨概率吗？**
规则分（0~100）是技术条件的加权打分，不是概率；未来区间只描述波动幅度。本技能只提供两类受严格约束的频率/概率：(1) 脚本在标的自身历史上逐点无前视统计的「历史条件频率」（附样本数 n 与 95% Wilson 区间，n < 30 向市场基准收缩）；(2) 助手以历史条件频率为锚点、引用证据编号 `E1…En` 并通过 `validate_judgment.py` 校验的「主观研判概率」（已记入日志待到期按 Brier 分数检验）。

**Q：数据从哪里来，会不会用假数据？**
新浪、Yahoo、腾讯的公开行情接口。取不到就以 `[DATA_UNAVAILABLE]` 退出，绝不估算或生成数据；离线测试使用录制下来的真实行情。

**Q：会把我的数据或问题发给第三方吗？**
脚本只向行情接口请求标的代码对应的公开行情，不上传账户净值等任何个人信息。

**Q：怎么更新？会不会覆盖我的修改？**
重新执行安装命令即可，代码目录用 `git pull --ff-only` 更新；如果你在代码目录里改过文件导致无法快进合并，脚本会报错停止，不会覆盖。

**Q：可以只给某个项目安装吗？**
可以，加 `--project <项目目录>`，技能会注册到该项目的 `.claude/skills` 或 `.agents/skills`。

**Q：能分析北交所股票、港股 ETF 吗？**
可以。北交所需要直接给 `.BJ` 代码（名称搜索不到）；常见港交所 ETF/ETP 代码段（如 `02800.HK`、`03033.HK`）会自动识别为 `etf` 并免除印花税。

**Q：能做空吗？**
本技能不替你交易。港股、美股可以融券卖空但有严格限制，A 股融券受限更多，规则见 `references/` 下的三个市场手册。

---

## 10. 已知限制

- 行情接口是新浪、Yahoo、腾讯的公开网页接口，不是官方付费数据。它们可能限流或调整格式，届时会以 `[DATA_UNAVAILABLE]` 报错，不会给出错误数据。
- 交易日历：A 股覆盖到 2026-12-31（2027 年放假安排尚未公布），港股和美股覆盖到 2027-12-31。超出范围的未来日期按工作日估算，报告会注明。
- 北交所没有除权除息事件数据，按不复权价格计算并在报告中注明。
- ST 状态按当前简称识别，回测和历史区间审计会把它沿用到全部历史日期。
- 财报日数据暂无来源，财报窗口不会放大预测区间。
- Windows：`install.ps1` 与整体流程都还没有在 Windows 上实测。创建软链需要开发者模式或管理员权限，失败时脚本会自动改为复制；已加入微软雅黑字体路径。
- 名称转代码依赖腾讯证券搜索接口，搜不到北交所股票。

---

## 11. 开发者

- 工程规范、铁律和门禁命令见 [AGENTS.md](AGENTS.md)；审查记录见 [SKILL_REVIEW_CHECKLIST.md](SKILL_REVIEW_CHECKLIST.md)。
- 修改代码后必须跑通以下两项门禁：

```bash
uv run --directory kline-structure-chart pytest -v
```

```bash
uv run --directory kline-structure-chart python tests/check_days_invariance.py --data tests/fixtures/recorded/sina_daily --step 40
```

- 推送和 PR 时 [CI](.github/workflows/ci.yml) 会在 Ubuntu 上自动运行这两项门禁（只用录制数据，不联网）。
- 版本变化记录在 [CHANGELOG.md](CHANGELOG.md)。
- 从本地检出演练安装脚本时，务必使用临时 HOME，避免改动自己的技能目录：

```bash
HOME="$(mktemp -d)" bash install.sh --source "$(pwd)" --agents claude,codex
```

- 测试数据需要更新时，联网重新录制。可以用 `--only quotes`、`--only search` 这类参数只录某一类：

```bash
uv run --directory kline-structure-chart python tests/record_fixtures.py
```

---

## 免责声明

本技能输出的所有内容都是基于历史行情的规则化技术分析，仅供研究与学习参考，不构成任何投资建议。市场有风险，决策需独立判断。
