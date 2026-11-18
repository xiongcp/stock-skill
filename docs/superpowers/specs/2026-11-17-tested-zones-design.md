# 多次测试价区与结构识别（动态 N 重底 / 顶、头肩、平台）：设计方案

- 日期：2026-11-17
- 状态：已确认（2026-11-17）。第 14 节 D1~D3 均采用推荐项：D1=a、D2=a、D3=a。编写实施计划时用参考实现在真实行情上重跑了全部固定样例，补充了第 3.7、4、5、6、8 节的细则（均标"补充"），并新增 D4（`tested_zones` 体积上限），D4 于 2026-11-17 确认采用推荐项 a
- 实施计划：`docs/superpowers/plans/2026-11-17-tested-zones.md`
- 范围：`docs/superpowers/plans/2026-11-17-session-brief-work-plan.md` 的阶段 1；同时是 `2026-11-15-regime-attribution-and-market-layer-design.md` 路线图阶段 2（统一多假设结构引擎）中"平台型、双底双顶（含破底翻）"的部分
- 参考实现：临时原型脚本（未入库）。第 11.2 节的固定样例数值均由它在真实行情上算出，已全部写入本文，实现时以本文为准

---

## 1. 背景与目标

### 1.1 问题

- `patterns.detect_geometric_patterns` 只比较**最近两个**已确认低点（高点），N 写死为 2。第三个、第四个触点被忽略，识别不出三重底、多次测试的平台。
- 结构层只有一个 ZigZag 级别（2×ATR%，夹在 3%~8%），几天内的小级别回踩看不到。
- 双底只有"结构 / 突破"两态，没有"测试中、守住、破位、假破位"，也没有"与昨天相比变了什么"。

用户要求：不预设"三重底"再去找脚，而是由数据动态识别是几重底、几重顶，或者是什么结构，判断要更细、更灵活。

### 1.2 目标

1. 在两个级别上，把价格相近的拐点聚成价区，触点数就是"几重"。
2. 每个价区给出状态、确认价、失效价与颈线，失效价**只由已确认触点决定**，不随新低移动。
3. 另外识别头肩底 / 头肩顶，以及由一个支撑价区和一个压力价区组成的平台。
4. 与上一根已收盘K线的结果比较，列出变化，供报告和简报按变化组织文字。

### 1.3 前提

| 编号 | 内容 |
| --- | --- |
| A1 | 只用标的自身的已收盘日线与已确认拐点；不新增数据源 |
| A2 | 全部为描述性实验字段，不参与规则分、评级、否决与计划价位 |
| A3 | 不输出概率、胜率或频率；"测试次数"只是计数，不代表支撑更强或更弱 |
| A4 | 常量在本文写定，实现时不按结果调参。第 2.3 节注明了每个常量的来源 |

### 1.4 不在本文范围

- 盘前 / 午间 / 收盘简报（工作计划阶段 2~4）。
- 周线级别的价区、旗形、三角旗等其他形态。
- 历史回放快照（看板时间滑块）中的价区。
- 让价区参与评分或决策，以及任何有效性检验。

---

## 2. 通用量

### 2.1 ATR

`atr_abs = structure.atr_pct(hist, 20) × 最新收盘价`，即结构层已有的"近 20 日平均真实波幅占昨收比例"乘以最新收盘价。价区与 ZigZag 阈值用同一个 ATR。

现有双底形态用的是 ATR14（Wilder），两者数值不同（上证指数 2026-11-17：结构层 52.46，ATR14 50.87），这是有意的：价区属于结构层。

### 2.2 两个级别

| 级别 | ZigZag 反转阈值 | 说明 |
| --- | --- | --- |
| 主级别 `MAJOR` | 结构层现有的 `zz_thr`（2×ATR%，夹在 3%~8%，拐点不足时逐步放宽到 2%） | 与轨道、ABC、阶梯共用同一组拐点 |
| 次级别 `MINOR` | `min(4%, max(1.5%, ceil(ATR% ÷ 0.5%) × 0.5%))` | 按 0.5% 取整，减少阈值逐日漂移带来的拐点变化；不小于主级别阈值时跳过本级别，写 `skipped_reason` |

两个级别都在 `hist`（最近 420 根已收盘K线）上跑 `structure.zigzag`，只取下标落在结构窗口（最近 120 根）内的拐点。ZigZag 末端尚未被反向走势确认的极值记为"未确认拐点"。

### 2.3 常量

| 常量 | 值 | 来源 |
| --- | --- | --- |
| 价区宽度上限 `W_TOL` | 0.65 ATR | 沿用现有双底"两低点差 ≤ 0.65×ATR" |
| 破位距离 `BRK` | 0.5 ATR | 沿用情景库"收盘跌回轨道内超过 0.5 个 ATR"与 `MIN_TRIGGER_ATR` |
| 颈线最小高度 `NECK_MIN` | 1.2 ATR | 沿用现有双底"颈线 − 底 ≥ 1.2×ATR" |
| 假破位窗口 `FALSE_BARS` | 3 根 | 新增：破位后 3 根内收回才算假破位 |
| 近期窗口 `RECENT` | 20 根 | 沿用可引用价位"最近 20 根K线"的口径。补充：本文所有"最近 20 根内"都指K线下标 ≥ n − 20（n 为已收盘K线根数），即包含最新一根在内的 20 根 |
| 每级别每侧最多输出 | 3 个价区 | 新增：控制 `result.json` 体积 |
| 头肩颈线斜率上限 | 两颈线点之差 ≤ 0.5 × 形态高度 | 新增：原型在 2025-06-05 上证指数上识别出一个跨越 4 月暴跌、颈线陡斜的"头肩顶"，加此约束排除；属于先验结构约束，不是拟合 |
| 触点列表上限 | 8 个 | 新增：超过时列第 1 个与最后 7 个，`touches_truncated = true` |

---

## 3. 价区识别

### 3.1 聚类（决定 N）

对每个级别、每一侧（支撑用低点 `L`，压力用高点 `H`）分别处理。按时间顺序遍历结构窗口内的同类拐点（含末端未确认拐点）。对每个拐点 `p`，在已有价区中找候选：

1. 把 `p` 并入后，该价区全部触点的最高价与最低价之差 ≤ `W_TOL`；
2. 从该价区最后一个触点的下一根到 `p` 的前一根之间，没有收盘价越过该价区已确认触点的边缘 `BRK` 以上（支撑：低于已确认触点最低价 − 0.5 ATR；压力：高于已确认触点最高价 + 0.5 ATR）。

满足两条的候选中，选并入后宽度最小的；宽度相同选最后触点最近的。没有候选就新建价区。

只输出满足以下条件的价区：触点数 ≥ 2，第一个触点已确认，且颈线高度 ≥ `NECK_MIN`。

### 3.2 未确认触点

- 只有 ZigZag 末端的未确认拐点可能成为未确认触点，因此每个价区最多一个，且一定是最后一个。
- 它可能被之后更低（更高）的极值取代。若新极值仍在宽度上限内，就更新为新的未确认触点；若超出上限，该价区回到只有已确认触点的状态。所以 N 可能因此减 1，价区也可能因此消失（见第 6 节 `ZONE_DISSOLVED`）。
- **确认价**：支撑为未确认低点 ×（1 + 本级别阈值），压力为未确认高点 ×（1 − 本级别阈值）。之后任一根K线的最高价（最低价）达到确认价，该触点即被 ZigZag 确认。

### 3.3 几何量

以支撑价区为例，压力价区镜像：

| 字段 | 定义 |
| --- | --- |
| `zone_low_price` / `zone_high_price` | **已确认**触点的最低价 / 最高价 |
| `neckline_price` | 第一个触点与最后一个触点（含未确认）之间K线的最高价 |
| `height_abs` / `height_atr` | 颈线 − `zone_low_price`，及其 ATR 倍数 |
| `measured_move_price` | 颈线 + 高度（突破颈线后的测量位，不是目标价承诺） |
| `break_price` | `zone_low_price` − 0.5 ATR，即失效价 |
| `confirm_price` | 第 3.2 节的确认价；没有未确认触点时为 `null` |

失效价只在新的触点被确认时才可能变化，变化幅度受宽度上限约束；未确认的新低不会把失效价往下带。逐日只会因 ATR 变化而有极小差异（上证指数 11-16 → 11-17：3547.98 → 3548.04）。

### 3.4 命名

| N | 支撑 | 压力 |
| --- | --- | --- |
| 2 | 双底 | 双顶 |
| 3 | 三重底 | 三重顶 |
| ≥ 4 | N 次测试的支撑区 | N 次测试的压力区 |

N 包含未确认触点；有未确认触点时名称后缀"（第 N 次测试进行中，未确认）"。

### 3.5 触点序列

- `sequence`：`RISING`（逐次抬高）、`FALLING`（逐次降低）、`MIXED`。
- `first_last_atr`：最后一个触点减第一个触点，以 ATR 计。

### 3.6 状态

从最后一个**已确认**触点的下一根起，按时间扫描收盘价：

| 状态 | 判定（支撑价区；压力价区镜像） | `state_cn`（支撑 / 压力） |
| --- | --- | --- |
| `BROKEN` | 某根收盘低于 `break_price`，此后没有收盘回到 `zone_low_price` 及以上 | 已破位 / 已突破 |
| `FALSE_BREAK` | 某根收盘低于 `break_price`，3 根内收盘回到 `zone_low_price` 及以上，且收回发生在最近 20 根内、之后没有再次破位 | 假跌破后收回 / 假突破后回落 |
| `NECKLINE_CROSSED` | 没有破位，最新收盘高于颈线 | 已突破颈线 / 已跌破颈线 |
| `TESTING` | 没有破位、未过颈线，有未确认触点 | 第 N 次测试进行中 |
| `HOLDING` | 没有破位、未过颈线，没有未确认触点 | 价区守住、未过颈线 / 价区压住、未破颈线 |
| `EXPIRED`（不输出） | 破位后超过 3 根才收回 | — |

假跌破收回已超过 20 根且之后没有再次破位时，按 `NECKLINE_CROSSED` / `TESTING` / `HOLDING` 重新判定。

`state_since_day` 为进入当前状态的那根K线日期；`TESTING` / `HOLDING` 为 `null`。`BROKEN` 只在破位后 20 根内输出（"支撑转为上方压力"），之后不再输出。

### 3.7 筛选

每个级别、每一侧：

1. 未破位（`TESTING` / `HOLDING` / `NECKLINE_CROSSED` / `FALSE_BREAK`）的价区里，离最新收盘最近的一个（收盘在价区内时距离为 0）；
2. 最后一个触点或 `state_since_day` 落在最近 20 根内的价区，按最后触点由近到远排列。

两者合并去重，最多 3 个。`selected_because` 记录入选原因（`NEAREST_ACTIVE`、`RECENT`）。

次级别价区若与某个已输出的主级别价区触点完全相同（日期、价格、是否确认都相同），且没有未确认触点，只输出 `{zone_id, same_as_zone_id}`，不重复全部字段。补充：有未确认触点时两个级别的确认价因阈值不同而不同（G1 中 11-16 主级别 3694.17、次级别 3640.37），照常输出全部字段。

### 3.8 文字摘要

每个价区带 `summary_cn`，由状态决定句式（数字全部来自本价区字段）：

| 状态 | 支撑价区的句式 |
| --- | --- |
| `TESTING` | "{名称}：{触点}；价区 {low}~{high}，颈线 {neck}。第 N 个低点 {价} 尚未确认：之后最高价达到 {confirm} 即确认；收盘跌破 {break} 判定破位。" |
| `HOLDING` | "…收盘站上颈线 {neck} 为突破，测量位 {measured}；收盘跌破 {break} 判定破位。" |
| `NECKLINE_CROSSED` | "…已于 {since} 收盘站上颈线 {neck}，测量位 {measured}；收盘跌破 {break} 判定破位。" |
| `BROKEN` | "…已于 {since} 收盘跌破失效价 {break}，支撑转为上方压力；收盘重新站上 {low} 视为收回。" |
| `FALSE_BREAK` | "…曾收盘跌破 {break}，{since} 收盘收回（3 根K线内，假跌破）；再次收盘跌破 {break} 判定破位。" |

---

## 4. 头肩底 / 头肩顶

每个级别、每一侧只看结构窗口内最后三个同类拐点 `S1, H, S2`（`S2` 可以是未确认拐点）。以头肩底为例：

1. 头部更低：`H < min(S1, S2) − 0.65 ATR`；
2. 两肩相近：`|S1 − S2| ≤ 0.65 ATR`；
3. 颈线：`S1` 与 `H` 之间的最高价点 `N1`、`H` 与 `S2` 之间的最高价点 `N2` 连线，按K线下标线性外推到今天；
4. 高度：颈线在头部那根K线处的值 − `H` ≥ 1.2 ATR；
5. 颈线斜率：`|N1 − N2| ≤ 0.5 × 高度`。

状态：`S2` 之后任一收盘低于 `H − 0.5 ATR` 为 `FAILED`（形态失败）；否则最新收盘高于今日颈线为 `NECKLINE_CROSSED`，不高于为 `FORMING`。`S2` 未确认时标"右肩形成中（未确认）"。

输出字段：三个点的日期与价格、`right_shoulder_confirmed`、两个颈线点、`neckline_today_price`、`height_abs`、`measured_move_price`（今日颈线 + 高度）、`state`、`state_cn`、`summary_cn`。每个级别每侧最多一个。

补充（字段名）：`name_cn`（头肩底 / 头肩顶）、`left_shoulder` / `head` / `right_shoulder`（均为 `{day, price}`）、`right_shoulder_confirmed`、`neckline_points`（两个 `{day, price}`）、`neckline_today_price`、`height_abs`、`measured_move_price`、`state`、`state_cn`、`summary_cn`。`summary_cn` 里的失败条件写作"收盘跌破头部 {头部价} 下方 0.5 个 ATR"，不另设价位字段。

---

## 5. 平台

在同一级别已筛选的价区里，取一个支撑价区 S 和一个压力价区 R，要求：

1. 两者状态都是 `TESTING` / `HOLDING` / `FALSE_BREAK`；
2. 时间重叠：`[S 首触点, S 末触点]` 与 `[R 首触点, R 末触点]` 有交集；
3. 间距：`R.zone_low_price − S.zone_high_price ≥ 1.2 ATR`。

多对满足时，取两者末触点中较早的那个最近的一对。输出：`lower_price = S.zone_low_price`、`upper_price = R.zone_high_price`、两侧的 `zone_id` 与触点数（补充字段名：`lower_zone_id`、`upper_zone_id`、`lower_touch_count`、`upper_touch_count`）、`gap_atr`、`since_day`（两者首触点中较早者）、`summary_cn`。平台不单独判定状态：任一侧破位或越过颈线后，平台不再成立（第 6 节记为 `PLATFORM_ENDED`）。

---

## 6. 与上一根已收盘K线相比的变化

用截至上一根已收盘K线的数据（`completed[:-1]`，同样取最近 420 根）按本文全部规则再算一次，阈值与 ATR 都按那一天重新计算。按 `zone_id = {级别}-{SUPPORT|RESISTANCE}-{首触点日期}` 匹配价区，头肩按三个点的日期匹配，平台按两侧 `zone_id` 匹配。

| `change` | 含义 |
| --- | --- |
| `ZONE_NEW` | 新出现的价区（N 达到 2，或首次通过高度条件） |
| `TOUCH_ADDED` | 新增一个未确认触点 |
| `TOUCH_UPDATED` | 未确认触点被更远的极值取代，仍在宽度上限内 |
| `TOUCH_CONFIRMED` | 未确认触点被确认 |
| `TOUCH_LEFT_ZONE` | 未确认极值越出宽度上限，不再计入该价区 |
| `STATE_CHANGED` | 状态改变，带 `from` / `to` |
| `ZONE_DISSOLVED` | 价区不再输出：未确认触点被取代后 N < 2、`EXPIRED`、破位超过 20 根、或首触点移出结构窗口（`reason` 区分） |
| `HS_NEW` / `HS_STATE_CHANGED` / `HS_ENDED` | 头肩形态的出现、状态改变与消失 |
| `PLATFORM_NEW` / `PLATFORM_CHANGED` / `PLATFORM_ENDED` | 平台的出现、上下沿或触点数变化与结束 |

级别阈值与上一天不同时，该级别的每条变化都带 `threshold_changed = true`，报告写明"阈值由 x% 变为 y%，按新阈值重算"，不把重算说成结构变化。变化最多输出 12 条，主级别优先。

补充（实施细则）：

- **范围**：只对当天或上一天**输出过**的价区记变化。次级别价区在两天里都只以"与主级别相同"（`same_as_zone_id`）的形式出现，或只在一天出现且那天是引用形式时，不单独记变化。
- **`reason` 取值**：`ZONE_NEW` 为 `FORMED`（上一天不是有效价区）或 `SELECTED`（上一天已是有效价区，只是没入选）；`ZONE_DISSOLVED` 为 `TOUCH_REPLACED`（触点不足 2 个）、`HEIGHT`（颈线高度不足）、`EXPIRED`、`BROKEN_STALE`（破位超过 20 根）、`LEFT_WINDOW`（首触点移出结构窗口）、`PIVOT_GONE`（首触点不再是本级别拐点，通常因阈值变化）、`NOT_SELECTED`（价区仍有效，只是不再入选）、`SCALE_SKIPPED`（次级别当天跳过）。其余变化 `reason = null`。
- **`from` / `to`**：`STATE_CHANGED`、`HS_STATE_CHANGED` 为前后状态代码；`ZONE_NEW` 的 `to` 与 `ZONE_DISSOLVED`、`HS_ENDED` 的 `from` 为状态代码；其余为 `null`。
- **`detail_cn` 只写当天的价位**（以及日期）：上一天才有的价位可能已不在 `quotable_prices` 里，写进报告会被 `lint_response.py` 拦下。阈值变化时在句末追加"（阈值由 x% 变为 y%，按新阈值重算）"。
- **触点变化判定**：上一天的未确认触点今天被确认 → `TOUCH_CONFIRMED`；今天仍有未确认触点但日期变了 → `TOUCH_UPDATED`；今天没有未确认触点、上一天的也没被确认 → `TOUCH_LEFT_ZONE`；上一天没有、今天有 → `TOUCH_ADDED`。以上都不能解释已确认触点的变化时（例如 ATR 变化使边界上的拐点改归别的价区），记一条 `TOUCH_UPDATED`，`detail_cn` 写"触点按当日数据重算为……"。

---

## 7. 盘中标注

存在未收盘K线时，价区、头肩与平台全部只用已收盘K线计算，未确认触点不因盘中价更新（铁律 0）。仅在每个价区上附 `intraday_note`：

- 盘中最低（最高）已越过 `break_price`："盘中最低 X 已低于失效价 Y（未收盘，不作确认）"；
- 盘中价落在价区内："盘中正在测试该价区（未收盘，不作确认）"；
- 其余为 `null`。

与现有 ABC 的 `intraday_note` 口径一致。

---

## 8. 输出契约

### 8.1 位置与形状

在 `result.json` 的 `structure` 下新增 `tested_zones`（`x-stability: experimental`）：

```text
structure.tested_zones
├── atr_abs, atr_basis ("STRUCTURE_ATR20")
├── prev_bar_day
├── scales[]                         # 依次为 MAJOR、MINOR
│   ├── scale, scale_cn, zigzag_threshold_pct, skipped_reason
│   ├── zones[]                      # 第 3 节；次级别重复价区只有 zone_id 与 same_as_zone_id
│   │   ├── zone_id, side, side_cn, name_cn, touch_count, confirmed_touch_count
│   │   ├── touches[] {day, price, confirmed, confirmed_at_day}, touches_truncated
│   │   ├── zone_low_price, zone_high_price, neckline_price, height_abs, height_atr
│   │   ├── measured_move_price, break_price, confirm_price
│   │   ├── sequence, sequence_cn, first_last_atr
│   │   ├── state, state_cn, state_since_day, selected_because[]
│   │   └── intraday_note, summary_cn
│   ├── head_shoulders[]             # 第 4 节
│   └── platform                     # 第 5 节，或 null
└── changes[] {scale, object_type, object_id, change, change_cn, from, to, reason, threshold_changed, detail_cn}
```

- 价格按标的价位表取整（与结构层其他价格相同）；比例与 ATR 倍数保留 2 位。补充：观察到的价格（触点、价区上下沿、颈线、头肩三点与颈线点、`height_abs`、`atr_abs`）与结构层拐点同口径，用 `structure.money`；推算出的价位（`break_price`、`confirm_price`、`measured_move_price`、`neckline_today_price`）与情景库同口径，用价位表取整 `round_to_tick`。
- 不含 `NaN` / `Infinity`。
- 现有 `structure.support_zone` / `resistance_zone`（stable）与 `structure.patterns` 里的双底双顶保持不变（6.x 只新增，不改语义）。

### 8.2 可引用价位

`*_price` 字段经 `collect_price_whitelist` 自动进入 `quotable_prices`；`height_abs`、`atr_abs`、`first_last_atr` 不进入（键名不含价格词或含 `atr`）。实现时用测试确认这一点。

### 8.3 体积

`result.json` 总体仍不超过 80 KB。超出时先按 v6 规格第 3.4 节裁剪基准结构摘要；仍超出则依次去掉次级别的 `BROKEN` 价区（连同指向它们的变化条目）、次级别的 `summary_cn`；每步之后按裁剪后的内容重算 `quotable_prices.prices`，并在 `quality.notes` 写明裁了什么。

补充（D4，已确认采用 a）：原稿写的"`tested_zones` 不超过 12 KB"是没有实测就定的。参考实现在 17 个录制标的（不含 VIX）、656 个截面上实测（单独序列化，`indent=2`）：中位数 9.6 KB，p90 15.1 KB，p99 19.0 KB，最大 23.2 KB；12 KB 会在 27.3% 的截面上超出。放进 `result.json` 后多两层缩进，体积约再增 11%。

对总预算的影响（编写实施计划时实测）：九个录制配置截至 2026-10-15 的 `result.json` 现为 39.0~64.7 KB，加上价区后为 46.7~87.5 KB，只有腾讯（00700.HK）超出 80 KB；它裁掉基准结构摘要的两步（备选浪型计数、远端关键价位）后为 81 912 字节（79.99 KB），刚好在预算内，价区未裁。按各标的这一天的基础体积估算，7 个个股或指数的 469 个历史截面中约 5.1% 会超出 80 KB、需要裁剪。推荐去掉 12 KB 这一条，保留 80 KB 总预算与上面的裁剪顺序，测试改为"`tested_zones` ≤ 32 KB"（防止数量上限被误删）；选项与代价见第 14 节 D4。

---

## 9. 呈现

- **`report.md` 第 5 节**：在第 5 节末尾（支撑压力阶梯之后）新增"多次测试价区"小节，按主级别、次级别逐条列出 `summary_cn`，再列平台与头肩，最后列"与上一交易日相比"的变化（无变化时写"与上一交易日相比无变化"）。
- **`report.md` 第 6 节**：原"波段双底 / 双顶"保留，后面加一句："波段双底 / 双顶只比较最近两个已确认低点（高点）；同一区域的全部触点见第 5 节多次测试价区。"（只在报告列出了非蜡烛类的双底或双顶时出现）
- **`dashboard.html`**：新增图层开关"多次测试价区"（默认显示主级别，可切换次级别）：价区画成半透明横带，触点按序编号，未确认触点空心，颈线与失效价虚线，标签为 `name_cn` + `state_cn`。时间回放时不显示。
- **`preview.png`**：只画主级别已筛选价区（横带、编号、颈线，以及"名称 · 状态"标签；标签与拐点徽标用同一套避让逻辑），颜色随市场配色；不画次级别，避免拥挤。
- **`SKILL.md` 回复契约第 5 条**：增加"多次测试价区先说主级别，再说次级别；未确认触点必须说'未确认'；失效价引用 `break_price`；测试次数只是描述，不得说成支撑更强、更可靠或更容易破"。
- **`lint_response.py`**：拦截"测试次数越多……""越测越……""多次测试（更可靠|更坚固|必破|易破）""三重底概率"等说法。

---

## 10. 因果性、不变性与决策隔离

1. **零前视**：只读已收盘K线；`run_causal_replay_audit` 新增一项——在每个截面、同一阈值与 ATR 下，截至截面日已确认的触点、价区几何量（不含状态）在追加被放大的未来K线后不变。
2. **未确认只在末端**：每个价区至多一个未确认触点，且是最后一个；已确认触点的 `confirmed_at_day` 不晚于截止日。
3. **窗口不变性**：结果只依赖 `hist` 与结构窗口，与 `--days` 无关；`check_days_invariance.py` 的 `STRUCT_KEYS` 增加 `tested_zones`。补充：上一交易日要在同样 420 根上重算，至少需要 421 根已收盘K线。主流程固定取 760 根，不受影响；v4.1 命令行 `render_structure_chart.datalen_for` 在 `--days 60` 时只取 420 根，取数下限改为 421 根（实测不改也通过 420 次检查，这里改成保证）。
4. **决策隔离**：规则分、评级、动作、否决条件与计划价位不读取 `tested_zones`；`compute_structure_layer` 增加参数 `with_tested_zones`（默认 `True`），回测逐日回放传 `False`，与大盘层同样处理。

---

## 11. 测试

全部用真实录制行情，测试断网；不生成任何合成序列。新测试放在 `tests/test_tested_zones.py`。

### 11.1 新增录制

`tests/record_fixtures.py` 新增 `--only cases`：把上证指数截至 2026-11-17 的新浪日线另存为 `tests/fixtures/recorded/sina_daily_cases/sh000001_2026-11-17.json`，写入 `MANIFEST.json`（录制时间、来源、sha256）。现有 `sina_daily/sh000001.json` 不动，避免牵动其他测试与键路径快照。

### 11.2 固定样例（数值来自参考原型；实现若不一致，须先改本节并说明原因）

**G1 上证指数，新录制，逐日：**

| 截止日 | 级别 | 期望 |
| --- | --- | --- |
| 2026-11-13 | 主 | 双底 10-23 3574.27、11-03 3580.38，`HOLDING`，颈线 3708.20，失效价 3547.69；双顶 10-27 3708.20、11-05 3701.59（未确认），`TESTING`，确认价 3590.54；平台 3574.27 ~ 3708.20 |
| 2026-11-13 | 次 | 双底 11-06 3610.20、11-13 3623.84（未确认），`TESTING`；三重顶 10-27 3708.20、11-05 3701.59、11-12 3699.58，`FALLING`，`HOLDING`；平台 3610.20 ~ 3708.20 |
| 2026-11-16 | 主 | 三重底 10-23、11-03、11-16 3586.57（未确认），`TESTING`，确认价 3694.17，失效价 3547.98；双顶 11-05 已确认，`HOLDING` |
| 2026-11-16 | 次 | 三重底同上，确认价 3640.37；11-06 起的双底消失；平台 3574.27 ~ 3708.20，下沿 3 次、上沿 3 次 |
| 2026-11-17 | 主 | 三重底 10-23、11-03、11-17 3565.59（未确认），`MIXED`，`TESTING`，价区 3574.27 ~ 3580.38，颈线 3708.20，测量位 3842.13，失效价 3548.04，确认价 3672.56；双顶 `HOLDING`，失效价 3734.43；平台 3574.27 ~ 3708.20 |
| 2026-11-17 | 次 | 三重底同上，确认价 3619.07；三重顶同 11-13；平台下沿 3 次、上沿 3 次 |

变化：11-13 → 11-16 主级别支撑价区 `TOUCH_ADDED` 且 `HOLDING → TESTING`，压力价区 `TOUCH_CONFIRMED` 且 `TESTING → HOLDING`；次级别 `MINOR-SUPPORT-2026-11-06` 为 `ZONE_DISSOLVED`。11-16 → 11-17 支撑价区 `TOUCH_UPDATED`（3586.57 → 3565.59），状态不变，失效价只因 ATR 变化而差 0.06。

**G2 上证指数，现有录制，截至 2026-10-15：** 次级别"4 次测试的支撑区"（07-20 3741.11、07-30 3767.50、10-09 3754.14、10-13 3741.53）为 `BROKEN`，自 10-15 起；次级别"5 次测试的压力区"（07-15 至 09-22）为 `NECKLINE_CROSSED`（已跌破颈线 3741.11），自 10-15 起；主级别三重顶（08-18 3994.18、09-01 3995.18、09-22 3967.68）为 `NECKLINE_CROSSED`，自 09-28 起。

**G3 贵州茅台，现有录制，截至 2026-10-15：** 07-27 1279.58 / 08-24 1270.33 的双底在 9 月被收盘跌破、3 根以后才收回，为 `EXPIRED`，不出现在输出中；主级别双顶 08-03 1363.35 / 08-10 1359.97 为 `HOLDING`。

**G4 比亚迪，现有录制，截至 2026-10-15：** 主级别平台 81.51 ~ 87.20（双底 09-11 / 10-08，双顶 09-22 / 10-12，间距 1.91 ATR）。次级别有两个"4 次测试的压力区"（05-21 至 06-02、07-17 至 08-03），均为 `NECKLINE_CROSSED`（自 08-04 与 08-31 起）；它们不是离现价最近的未破位价区，最后触点与状态变化也早于最近 20 根，不入选。

**G5 头肩底，现有上证录制，截至 2025-06-05，次级别：** 左肩 2025-03-24 3340.24、头 2025-04-07 3040.69、右肩 2025-05-27 3332.49（已确认），颈线点 03-27 3394.04 / 05-14 3417.31，`FORMING`。同一截面上 03-27 / 05-14 / 06-05 组成的"头肩顶"因颈线斜率约束**不得**被识别。

**G6 头肩顶，现有上证录制，截至 2026-06-26，主级别：** 左肩 03-03 4197.23、头 05-14 4258.86、右肩 06-23 4175.35，颈线点 03-23 3794.68 / 06-08 3927.85，`FORMING`。

### 11.3 性质测试

- 第 10 节的四条（因果审计、未确认只在末端、窗口不变性、决策隔离）。
- 失效价不随未确认触点移动：G1 中 11-16 与 11-17 的失效价之差等于 0.5 × ATR 之差。
- 边界：结构窗口内同类拐点不足 2 个时 `zones` 为空；次级别阈值不小于主级别时 `skipped_reason` 非空；颈线高度不足 1.2 ATR 的价区不输出；破位后超过 3 根才收回的价区不输出。
- 契约：schema 校验、五个键路径快照（`UPDATE_V6_KEYPATHS=1` 重新生成并检查 `git diff`）、`tested_zones` ≤ 32 KB（D4）、`result.json` ≤ 80 KB。
- 呈现：`report.md` 第 5 节含价区摘要与变化；HTML 图层开关可用且控制台零报错；`lint_response.py` 放行价区价位、拦截第 9 节列出的说法。

---

## 12. 同步清单

| 文件 | 内容 |
| --- | --- |
| `scripts/tested_zones.py`（新增，补充） | 次级别阈值、聚类、几何量、状态、筛选、头肩、平台、变化、盘中标注。`structure.py` 已有 1000 多行，按"一个文件一个职责"单独成文件；它只从 `structure` 引入 `zigzag` / `swing_pivots` / `atr_pct` / `money` 与窗口常量 |
| `scripts/structure.py` | `compute_structure_layer` 新增 `with_tested_zones`，在函数内延迟引入 `tested_zones`（避免循环引入） |
| `scripts/market_structure.py` | 基准结构层调用传 `with_tested_zones=False`（基准摘要不含价区） |
| `scripts/analysis.py`、`scripts/evaluation.py` | 透传结构层结果；回测传 `with_tested_zones=False`（`analyze_bundle` 新增 `include_tested_zones`）；历史回放快照传 `False`（第 1.4 节不在范围）；因果审计新增一项 |
| `scripts/contract_v6.py`、`assets/analysis.schema.json` | 投影与 schema（`x-stability: experimental`） |
| `scripts/render_report_md.py`、`scripts/render_html.py`、`scripts/render_structure_chart.py` | 第 9 节的呈现 |
| `scripts/lint_response.py` | 禁用说法 |
| `tests/test_tested_zones.py`、`tests/record_fixtures.py`、`tests/check_days_invariance.py`、`tests/fixtures/contract/*` | 第 11 节 |
| `tests/test_docs_contract.py`（补充） | "头肩"原在不实宣称清单 `OVERCLAIMS` 中；本文实现并由 G5、G6 守护后从清单移除，注明原因 |
| `references/math-patterns.md` | 第 2~6 节的公式与常量 |
| `references/output-fields.md` | 第 8 节的字段字典 |
| `references/interpretation-guide.md` | 解读要点：N 只是计数；未确认触点会被取代；主次级别 N 不同是正常的；测量位不是目标价；平台是两个价区的组合描述 |
| `SKILL.md`、`AGENTS.md`（测试目录树）、`CHANGELOG.md`（`[Unreleased]` 契约变更：新增）、`README.md` / `README.en.md`、`examples/` | 同步 |

---

## 13. 验收标准

1. 第 11.2 节六组固定样例全部通过。
2. 全量 `pytest -n auto` 与 `check_days_invariance.py` 通过。
3. 五个配置下，规则分、评级、动作、否决条件与计划价位在新增前后逐字段相同。
4. 上证指数、茅台、腾讯、英伟达各生成一次真实产物：`result.json` ≤ 80 KB；HTML 在浏览器中检查图层开关与控制台零报错；PNG 目视检查配色与标签。

---

## 14. 待决事项（已于 2026-11-17 确认，D1~D4 均采用推荐项 a）

| 编号 | 问题 | 选项 | 推荐 |
| --- | --- | --- | --- |
| D1 | 次级别阈值是否按 0.5% 取整 | a. 取整（本文写法）；b. 不取整，直接用 1×ATR% | a：阈值不逐日漂移，"昨天到今天的变化"更少来自阈值本身 |
| D2 | PNG 是否画次级别价区 | a. 只画主级别（本文写法）；b. 两级都画 | a：PNG 已有轨道、阶梯与推演，两级都画会拥挤；次级别在 HTML 里可切换 |
| D3 | 头肩的颈线斜率约束 | a. 采用"两颈线点之差 ≤ 0.5 × 形态高度"（本文写法）；b. 不加约束 | a：不加会把跨越暴跌、颈线陡斜的形态当成头肩；此约束是看到误判后补的先验条件，在第 2.3 节如实注明 |
| D4 | `tested_zones` 的体积上限，及它对 `result.json` 80 KB 总预算的影响 | a. 去掉 12 KB；保留 80 KB 总预算与第 8.3 节的裁剪顺序；测试守护 `tested_zones` ≤ 32 KB。代价：约 5% 的运行会触发裁剪（腾讯 2026-10-15 的录制样例就会，裁掉基准结构摘要的两步），裁剪时在 `quality.notes` 写明；b. 总预算从 80 KB 提到 104 KB，裁剪顺序保留作兜底。代价：与 v6 规格第 6.2 节、大盘层规格第 3.4 节"体积预算不放宽"的约定相反；c. 保留 80 KB，压缩价区输出（每侧最多 2 个价区、去掉 `summary_cn` 与可推导的中文标签）：腾讯 2026-10-15 仍有 78.4 KB，贴近上限，且少了价区与现成句子 | a：80 KB 是 v6 已确认、大盘层又重申过的约束；裁剪链本就为此设计，主级别价区与标的自身的决策字段都不裁 |

## 15. 风险

- **N 随级别和阈值变化**：同一区域主级别是双底、次级别是 4 次测试，这是级别不同，不是矛盾。报告两级并列，并在解读指南中说明。
- **N 被误读为强弱**：回复契约与 `lint_response.py` 拦截；解读指南写明"测试次数只是计数"。
- **未确认触点被取代导致价区消失**：属于规则本意，`changes` 用 `ZONE_DISSOLVED` 与 `reason` 写清原因，不静默消失。
- **体积**：最多 2 个级别 × 2 侧 × 3 个价区，另加头肩与平台；约 5% 的运行会超出 80 KB 而触发裁剪，实测与裁剪顺序见第 8.3 节与 D4。
- **常量是人为选的**：多数沿用现有口径（第 2.3 节）；新增的 4 个常量都写明来源，实现后不再调整。如果日后要让价区参与决策，必须先在研究分支预注册验收。
