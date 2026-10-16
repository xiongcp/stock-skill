# kline-structure-chart：A股 / 港股 / 美股 K 线结构分析技能

`kline-structure-chart` 是一个符合 [Agent Skills 开放规范](https://agentskills.io/specification) 的技能包。装进 Claude Code、Codex、Cursor、Gemini CLI 等 AI 编程助手之后，你只要说"帮我看看贵州茅台的 K 线"，助手就会：

1. 运行脚本，从新浪、Yahoo、腾讯拉取**真实**日线行情；
2. 用确定性的 Python 公式计算指标、形态、支撑压力、风控点位、未来 15 个交易日的价格区间和历史回测；
3. 生成一份中文 Markdown 报告、一张结构图（PNG）、一个可离线打开的交互看板（HTML），并据此回答你。

设计原则：

- **只用真实数据**：取不到行情就报错退出（`[DATA_UNAVAILABLE]`），绝不估算、补数或生成假数据。
- **算图分离**：所有数字由脚本计算，图表只负责展示；不让 AI 看图猜价格。
- **先大盘、后个股**：分析个股时同时评估对应大盘，大盘太弱或缺少大盘数据时风控会否决做多。
- **不用未来数据**：形态、预测校准和回测都只使用当时已经收盘的 K 线。

> 本技能只做规则化技术分析，仅供研究参考，不构成投资建议，也不会替你下单。

---

## 1. 能分析什么

| 市场 | 代码格式 | 示例 | 行情来源 | 默认大盘基准 |
| --- | --- | --- | --- | --- |
| A股 | 6 位代码 + `.SH` / `.SZ` / `.BJ` | `600519.SH`、`000002.SZ`、`830799.BJ`、`510300.SH`（ETF）、`000001.SH`（上证指数） | 新浪日线（不复权）+ Yahoo 除权除息事件 | 沪深300 `000300.SH` |
| 港股 | 5 位代码 + `.HK` | `00700.HK`、`01810.HK`、`HSI.HK`、`HSTECH.HK` | Yahoo 日线；恒生科技指数用腾讯指数日线 | 恒生指数 `HSI.HK` |
| 美股 | 代码 + `.US` | `NVDA.US`、`SPY.US`、`BRK.B.US` | Yahoo 日线 | `SPY.US` |

证券简称、A 股 ST/\*ST 标识和港股每手股数，会自动从腾讯行情快照获取。

---

## 2. 环境要求

- macOS 或 Linux（Windows 见第 9 节的已知限制）。
- [uv](https://docs.astral.sh/uv/)：用来安装 Python 3.12 和依赖。
- 网络：首次安装需要访问 PyPI；每次分析需要访问新浪、Yahoo、腾讯的行情接口。没有网络的沙箱环境无法使用（会直接报 `[DATA_UNAVAILABLE]`）。
- 中文字体（画 PNG 用）：
  - macOS 自带，无需处理。
  - Linux 需要安装文泉驿或 Noto CJK，例如 `sudo apt install fonts-wqy-zenhei` 或 `sudo apt install fonts-noto-cjk`。

---

## 3. 安装

### 3.1 获取代码并安装依赖

```bash
git clone <仓库地址> stock-skill
```

```bash
cd stock-skill
```

```bash
uv sync --python 3.12 --directory kline-structure-chart
```

### 3.2 把技能注册到你的 AI 助手

AI 助手会到固定目录里查找技能。下面用软链接的方式注册，以后 `git pull` 更新代码，技能会同步更新。请在仓库根目录执行。

**Claude Code（对所有项目生效）**

```bash
mkdir -p ~/.claude/skills && ln -sfn "$(pwd)/kline-structure-chart" ~/.claude/skills/kline-structure-chart
```

**Claude Code（只对某个项目生效）**：把上面的 `~/.claude/skills` 换成那个项目里的 `.claude/skills`。

**Codex、Cursor、Gemini CLI、Copilot CLI 等**：这类工具通常会扫描 `~/.agents/skills/`（具体以各工具文档为准）。

```bash
mkdir -p ~/.agents/skills && ln -sfn "$(pwd)/kline-structure-chart" ~/.agents/skills/kline-structure-chart
```

注册后**新开一个会话**，技能列表里应该能看到 `kline-structure-chart`。

### 3.3 验证安装

先跑离线测试。测试不访问网络，全部使用录制下来的真实行情回放：

```bash
uv run --directory kline-structure-chart pytest -q
```

再联网跑一次真实分析：

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market CN --symbol 600519.SH --output-dir artifacts/smoke
```

失败时脚本以非零码退出：`[DATA_UNAVAILABLE]` 表示取不到真实行情，`[INVALID_ARGUMENT]` 表示代码或参数有误（见第 8 节）。成功时退出码为 0，`kline-structure-chart/artifacts/smoke/` 下会出现 `600519.SH_report.md`、`600519.SH_result.json`、`600519.SH_preview.png`、`600519.SH_dashboard.html` 四个文件。

---

## 4. 在 AI 助手里使用

直接用自然语言提问即可，例如：

- "帮我分析一下贵州茅台 600519.SH 的 K 线结构和支撑压力。"
- "腾讯 00700.HK 未来两周大概的波动区间是多少？带上历史回测。"
- "NVDA 现在的风控计划是什么？我的账户 10 万美元，单笔最多亏 1%。"
- "看看上证指数和创业板指现在的结构。"

助手按技能里的规则工作：

1. 运行脚本。代码写不全时，会请你补充交易所后缀。
2. 先检查数据质量：
   - `UNAVAILABLE`：不给任何价位。
   - `PARTIAL`：把告警放在回答最前面。
3. 以生成的 `*_report.md` 为骨架回答，顺序固定为：大盘 → 结构与条件推演 → 风控计划 → 未来区间 → 回测 → 文件路径 → 免责声明。报告中的数字不得改动。
4. 如果风控否决了做多，会把否决原因放在最前面。

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
| `--asset-type` | `index` / `equity` / `etf`。默认按代码段推断：A 股 51/56/58（沪）、15（深）开头识别为 ETF；**港股 ETF 需要手动传 `etf`** |
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

---

## 6. 输出文件

| 文件 | 内容 |
| --- | --- |
| `*_report.md` | 中文分析报告，依次为：数据说明、风控否决、结论、大盘、结构与条件推演、风控计划、形态证据、未来区间、回测、文件路径、免责声明。数字全部取自 `*_result.json` |
| `*_result.json` | 全部计算结果（通过 `assets/analysis.schema.json` 契约校验） |
| `*_preview.png` | 1920×1160 结构推演图；数据有问题时带红色告警和水印 |
| `*_dashboard.html` | 离线交互看板：7 个图层开关、悬停查看数值、相对强弱视图、历史回放滑块、导出 PNG |
| `*_backtest.json` | 历史计划回放回测与无前视审计（传 `--backtest` 时生成） |

---

## 7. 怎么读结果

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
| `no CJK font found` | 没有中文字体，按第 2 节安装 |
| `uv: command not found` | 先安装 uv：<https://docs.astral.sh/uv/> |
| 报告提示"数据只到某日：最近已收盘的交易日……尚未提供" | 数据源收盘后入库有延迟，或该标的停牌；稍后重跑 |
| 报告提示"港股每手股数未能获取" | 腾讯快照暂时不可用，股数按 100 股/手假设计算，下单前请向券商核对 |

---

## 9. 已知限制

- 行情接口是新浪、Yahoo、腾讯的公开网页接口，不是官方付费数据。它们可能限流或调整格式，届时会以 `[DATA_UNAVAILABLE]` 报错，不会给出错误数据。
- 交易日历：A 股覆盖到 2026-12-31（2027 年放假安排尚未公布），港股和美股覆盖到 2027-12-31。超出范围的未来日期按工作日估算，报告会注明。
- 北交所没有除权除息事件数据，按不复权价格计算并在报告中注明。
- ST 状态按当前简称识别，回测和历史区间审计会把它沿用到全部历史日期。
- 港股 ETF 无法从代码自动识别，请传 `--asset-type etf`（ETF 免印花税）。
- 财报日数据暂无来源，财报窗口不会放大预测区间。
- Windows 已加入微软雅黑字体路径，但整体流程未在 Windows 上实测；软链接需要管理员或开发者模式，也可以直接复制 `kline-structure-chart` 目录。

---

## 10. 开发者

- 工程规范、铁律和门禁命令见 [AGENTS.md](AGENTS.md)；审查记录见 [SKILL_REVIEW_CHECKLIST.md](SKILL_REVIEW_CHECKLIST.md)。
- 修改代码后必须跑通以下两项门禁：

```bash
uv run --directory kline-structure-chart pytest -v
```

```bash
uv run --directory kline-structure-chart python tests/check_days_invariance.py --data tests/fixtures/recorded/sina_daily --step 40
```

- 测试数据需要更新时，联网重新录制。可以用 `--only quotes` 这类参数只录某一类：

```bash
uv run --directory kline-structure-chart python tests/record_fixtures.py
```

---

## 免责声明

本技能输出的所有内容都是基于历史行情的规则化技术分析，仅供研究与学习参考，不构成任何投资建议。市场有风险，决策需独立判断。
