# 前向跟踪 H2 运行手册（Hermes agent · Linux 2 核 4GB）

本手册面向在服务器上每天执行 H2 前向跟踪的 Hermes agent 与维护者。规则来源：`docs/superpowers/specs/2026-10-20-backtest-acceptance-revision.md` 第 12 节；冻结文件：`research/preregistration/preregistration_h2.json`。

## 1. 纪律（必须遵守）

- **不改代码，不升级代码。** 服务器只运行 `preregistration_h2.json` 所在的那个提交。任何冻结文件改动后，所有命令都会拒绝运行。不要 `git pull`，不要 `git checkout` 到别的提交。
- **不补跑。** 每次只处理每个市场最近一个已收盘交易日。错过的交易日会自动记进 `gaps.jsonl`，不要手动补。
- **不看收益。** `status` 只报告数量。不要读取或计算 `outcomes.jsonl` 里的收益，也不要把它们发给任何人。
- **不改日志。** `artifacts/long_backtest/h2/` 下的 `*.jsonl` 只能追加，不要编辑或删除。
- **`evaluate` 只运行一次**，并且由用户决定何时运行：`status` 显示 `ready_for_evaluation: true` 时，通知用户即可。

## 2. 服务器要求

| 项目 | 要求 |
| --- | --- |
| 系统 | Linux，Python 3.12+，git |
| CPU / 内存 | 2 核 / 4GB 足够：计算峰值实测约 170MB（父进程加 2 个子进程） |
| 磁盘 | ≥ 2GB（行情缓存约 410MB，日志每年约几十 MB） |
| 网络 | 能访问 `money.finance.sina.com.cn` 与 `query1.finance.yahoo.com` |
| 时区 | 任意；调度用 UTC 表达 |

## 3. 部署（一次性）

```bash
git clone git@github.com:xiongcp/stock-skill.git && cd stock-skill
# 检出冻结 H2 预注册的提交（不要用分支最新提交）
git checkout "$(git log -1 --format=%H -- research/preregistration/preregistration_h2.json)"
# 只安装 H2 需要的依赖：Pillow 与 NumPy，版本与 uv.lock 一致。dev 组的 ta-lib 需要 C 库，这里用不到
python3.12 -m venv kline-structure-chart/.venv
kline-structure-chart/.venv/bin/pip install pillow==12.3.0 numpy==2.5.4
mkdir -p artifacts/long_backtest/h2   # 日志目录（每日任务的输出重定向到这里）
```

从维护者的 Mac 同步研究数据到服务器（只需一次，路径保持不变）：

```bash
rsync -a artifacts/long_backtest/cache artifacts/long_backtest/universe.csv \
  artifacts/long_backtest/pit_universe.json  <server>:<repo>/artifacts/long_backtest/
```

部署完成后自检，应输出 `H2 预注册校验通过`：

```bash
cd research && ../kline-structure-chart/.venv/bin/python h2_forward.py check
```

## 4. 每日任务

- **时间**：每个工作日 UTC 23:00（北京时间周二至周六 07:00，此时 CN、HK、US 前一交易日都已收盘）。cron 写法为 `0 23 * * 1-5`。节假日照常运行，脚本会识别"没有新交易日"并跳过。
- **命令**：

```bash
cd <repo>/research && ../kline-structure-chart/.venv/bin/python h2_forward.py daily --workers 2 --threads 4 \
  >> ../artifacts/long_backtest/h2/daily.log 2>&1
```

- **参数怎么定的**：`--workers 2` 等于 CPU 核数，计算部分只占全部用时的一两分钟。`--threads 4` 用于抓数，抓数受两个站点各自的请求间隔限制（新浪 0.15 秒、Yahoo 0.25 秒），加线程不会更快，4 个线程足以让两个站点同时满速。不要调高 `--workers`：2 核机器上开更多进程只会互相抢 CPU。
- **耗时**：
  - 平时只刷新三个市场股票池的 4,500 只成分股和基准，约 6,000 次请求，约 20~25 分钟；
  - 调仓日（每 60 个交易日一次）要刷新全部约 1.26 万只股票，约 50~60 分钟；
  - 建议把任务超时设为 3 小时。
- **成功标志**：日志最后一行是 `status (counts only): {...}`。

## 5. 失败处理

| 现象 | 处理 |
| --- | --- |
| `H2 预注册校验未通过，拒绝运行` | 代码或冻结数据被改动。不要修，通知用户，并附上报错中列出的文件 |
| 个别标的 `refresh ...: error:...` | 只记录并跳过，不补数。若同一标的连续多日出错，在报告里列出 |
| 整次运行失败（例如断网） | 不要重跑过去的日期。下一个调度会照常处理当天，缺失日会自动记录 |
| `ALREADY_LOGGED` | 当天已处理过，属正常（例如节假日或重复触发） |

## 6. 每周给用户的报告（只报数量）

```bash
cd <repo>/research && ../kline-structure-chart/.venv/bin/python h2_forward.py status
```

把输出原样转给用户：每个市场的已记账天数、缺失天数、信号数、已到期 episode 数、信号日数，以及 `ready_for_evaluation`。不要附任何收益、命中率或均值。

## 7. 给 Hermes agent 的任务提示（可直接粘贴）

> 每个工作日 UTC 23:00，在 `<repo>/research` 目录执行 `../kline-structure-chart/.venv/bin/python h2_forward.py daily --workers 2 --threads 4 >> ../artifacts/long_backtest/h2/daily.log 2>&1`，超时 3 小时。只执行这条命令，不修改仓库里的任何文件，不执行 git pull 或 checkout，不补跑过去的日期，不读取 `outcomes.jsonl`。命令非零退出或日志里出现"预注册校验未通过"时，把日志最后 50 行发给用户并停止。每周一执行一次 `h2_forward.py status`，把输出原样发给用户。当 `ready_for_evaluation` 变为 `true` 时通知用户；不要自行运行 `evaluate`。
