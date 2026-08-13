# 2025 Capital Bikeshare 定价政策分析方法

## 1. 业务问题

Capital Bikeshare 于 2025 年 8 月 1 日调整价格。本分析关注：调价后，`casual` 用户的 classic-bike 日需求相对 `member` 是否发生额外变化；若 2024 全年数据完整，再判断该变化是否超过正常年份的季节性差异。

classic bike 是主分析对象。electric bike 仅报告 2025 年调价前后日均量变化，用于补充描述，不延伸 DID/DDD。

## 2. 数据处理与日级表

`analysis/pricing_policy_analysis.py` 按 250,000 行分块读取月度文件，复用主项目的核心清洗口径：

1. 解析 `started_at`、`ended_at`，以开始时间确定年份和日期；
2. 计算 `duration_min`；
3. 对跨文件 `ride_id` 去重，保留首次出现的记录；
4. 删除时间无法解析、时长不大于 0、时长超过 24 小时或开始年份不符的记录；
5. 仅保留 `member`/`casual` 与 `classic_bike`/`electric_bike` 四个分析分组。

输出 `data/aggregates/pricing_daily_segments.csv`，粒度为：

```text
year, date, month, weekday, member_casual, rideable_type,
ride_count, mean_duration_min, median_duration_min
```

日表以原始文件中实际观察到的日期为边界，并在每个观察日补齐四个分组；某分组当日确无有效骑行时，`ride_count` 记为 0。没有来源记录的日期不会被误补为零需求。脚本另外核对完整日历：2025 缺任一日期则不运行 DID；2024 不完整则不运行 DDD。

## 3. 时间与描述性口径

两个年份均按相同月日构造日历窗口：

```text
早期窗口 = January 1 through July 31
后期窗口 = August 1 through December 31
```

仅 2025 年可把两个窗口称为调价前/后。2024 年不存在本项目研究的价格调整；2024 年 8 月 1 日只作为历史同期日历切点，因此统一称为“8 月 1 日前/后历史同期”（historical pre/post calendar period）。描述性汇总分别报告 2024/2025、用户类型和车辆类型在两个窗口的日均骑行量及变化率。它提供业务背景，不替代模型估计。

## 4. Standardized Price Exposure

价格暴露只使用清洗后的 2025 年 1 月 1 日至 7 月 31 日 `casual + classic_bike` 骑行。它固定这些调价前已发生骑行的真实 `duration_min`，不使用调价后时长来定义主要价格冲击。按照 Capital Bikeshare 2025 年价格更新公告和 Single Ride 帮助页，计费分钟向上取整：

```text
billed_minutes = ceil(duration_min)
old_cost = $1 + billed_minutes × $0.05
new_cost = $1 + billed_minutes × $0.15
```

脚本以整数美分计算后再转换为美元，汇总 old/new cost、绝对差额和逐笔百分比差额，并给出 10、15、20、30 分钟示例。该指标统一称为 **Single-Ride-equivalent standardized ride cost（按 Single Ride 公示价格估算的标准化骑行成本）**。它是固定骑行行为下的挂牌费率情景计算，不是实际成交价、客单价、ARPU 或真实收入；公开 trip history 无法识别 casual 用户购买的是 Single Ride、Day Pass 还是其他短期产品，且情景金额不含销售税。

官方来源：[2025 年价格更新公告](https://capitalbikeshare.com/blog/2025priceupdate)、[Single Ride 计价与分钟取整说明](https://help.capitalbikeshare.com/hc/en-us/articles/360039400651-Single-Ride)。

## 5. 2025 DID

主模型仅使用 classic bike。先对每天构造用户组对数需求差：

```text
log_gap_t = log(casual ride_count_t) - log(member ride_count_t)
```

再估计：

```text
log_gap_t = alpha + beta_DID * Post_t + weekday_t + error_t
```

该写法与在长表中允许 member/casual 拥有不同星期模式的两组 DID 核心交互项等价；`beta_DID` 表示调价后 casual 相对 member 的额外变化。模型加入星期控制，并使用 HAC(7) 标准误处理一周内的序列相关。输出系数、标准误、p 值和 95% 置信区间。

百分比解释使用：

```text
effect_pct = (exp(beta_DID) - 1) * 100
```

负值表示 casual classic-bike 日需求相对 member 下降。若任一 classic-bike 日级分组为零，`log(ride_count)` 无定义，脚本会停止并要求检查覆盖，而不是任意加常数。

## 6. 2024/2025 DDD

只有 2024 与 2025 都覆盖完整日历时，才估计：

```text
[(Casual - Member) 调价后减调价前] in 2025
- [(Casual - Member) 历史同期后期减早期窗口] in 2024
```

在日级对数差模型中，内部变量 `Post` 是“8 月 1 日及以后”的日历窗口指标；它只在 2025 年代表政策后，在 2024 年不代表政策暴露。核心项为 `Year2025 × Post`，等价于长表模型中的 `Year2025 × Casual × Post`。模型控制年份、日历窗口和星期效应，使用 HAC(7) 标准误。DDD 百分比同样按 `(exp(beta_DDD) - 1) × 100` 转换。

2024 日历不完整时，结果文件会明确标记 `not_estimated`，不把缺失日期当作零需求，也不发布不完整 DDD 系数。

## 7. DDD 前期趋势斜率比较

分别使用 2024 和 2025 年 1 月 1 日至 7 月 31 日数据，在每个年份内定义：

```text
TimeWeek = (date - 当年 January 1) / 7 days
```

联合模型为：

```text
log_gap = alpha + Year2025 + TimeWeek + Year2025 x TimeWeek
          + weekday + Year2025 x weekday + error
```

`TimeWeek` 是 2024 历史同期前期 slope，`TimeWeek + Year2025 × TimeWeek` 是 2025 政策前 slope，核心检验项 `Year2025 × TimeWeek` 是两者 slope difference。年份与星期交互使两个 slope 与分年回归保持同一口径。协方差使用按年份分段的 HAC-panel(7)，避免把 2024 年 7 月 31 日与 2025 年 1 月 1 日误当成相邻日期。该检验只比较简单线性趋势；不扩展为 event study、placebo 或其他稳健性分析，也不能证明平行趋势成立。

## 8. Ride Duration Response

时长分析仅使用 `casual + classic_bike` 已发生骑行，分别汇总 2024/2025 年的 1 月 1 日—7 月 31 日与 8 月 1 日—12 月 31 日窗口。2024 年两个窗口始终是历史同期前期/后期，8 月 1 日不代表政策。每个窗口报告 ride count、mean、median、P25 和 P75。

固定时长 buckets 直接基于未取整的 `duration_min`，采用左开右闭区间：

```text
(0, 10], (10, 20], (20, 30], (30, 45], (45, +∞)
```

因此 10.0 分钟进入 `0–10 min`，45.0 分钟进入 `30–45 min`；最后一档包括所有超过 45 分钟且通过 `duration <= 1440` 清洗的骑行。边界不根据结果调整。本部分只描述 conditional-on-riding 的 trip-level outcome，不建立新模型；公开数据没有账户标识，因此不能把它解释为已识别的用户级 intensive margin，也不把观察到的时长变化解释为价格导致。由时长机械构造的标准化价格暴露与时长响应也不能视为两份相互独立的证据。

## 9. 解释边界

DID 与 DDD 围绕 2025 政策时点及 2024 对齐的历史同期日历切点进行比较，但不自动构成因果证明。天气、节假日、大型活动、车辆供给、站点容量、同期运营变化及用户构成变化均可能影响结果；DDD 只能消除 2024 同期可反映的平均季节差异。结果应表述为“与调价时点一致的相对需求变化”，并同时报告不确定性与数据覆盖情况。
