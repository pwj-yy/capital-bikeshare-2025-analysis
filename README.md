# Capital Bikeshare 用户需求、运营与定价策略分析

本项目使用 Capital Bikeshare 公开骑行数据，连接三类业务问题：用户何时、为何使用共享单车；哪些站点与 OD 流向值得优先运营；2025 年价格调整后需求出现了怎样的政策相关变化，以及应如何通过随机实验进一步验证。

项目保留原有 2025 年用户、时段、站点与 OD 分析，并新增基于 2024–2025 年完整数据的 DID/DDD、标准化价格暴露和账户级 A/B Test 方案。原始及行级数据不进入仓库，仅提交代码、小型聚合表、文档和可视化。

## Business Question

1. **用户需求：** member 与 casual 的使用规模、时段和周末模式有何差异？
2. **运营效率：** 哪些站点面临潜在缺车或满桩压力，高频 OD 反映了哪些通勤与休闲场景？
3. **定价策略：** 2025-08-01 调价后，casual classic-bike 需求相对 member 如何变化，价格冲击的经济量级有多大？
4. **实验验证：** 如何把观察性结果转化为可执行、可评估的账户级价格激励实验？

## Data

| 分析范围 | 规模 |
| --- | ---: |
| 2025 原始骑行记录 | 6,662,647 |
| 2025 清洗后记录 | 6,657,903（保留率 99.93%） |
| 2024–2025 定价分析有效记录 | **12,765,973** |
| 2025 完整 station/OD 子集 | 4,541,427（占清洗后记录 68.21%） |
| 站点分析键 / 唯一 OD 组合 | 1,108 / 164,786 |

两个流水线均按 250,000 行分块读取月度文件，解析时间戳、计算骑行时长、跨文件去重，并删除非正时长、超过 24 小时及年份不符记录。定价分析另外要求完整自然年日期覆盖，避免把来源缺失日补成零需求。

- 主数据处理口径：[docs/methodology.md](docs/methodology.md)
- 字段说明：[docs/data_dictionary.md](docs/data_dictionary.md)
- 定价分析口径：[docs/pricing_policy_methodology.md](docs/pricing_policy_methodology.md)

## User and Station Insights

- **通勤与休闲时段清晰分化。** 工作日约 08:00 和 17:00 出现通勤双峰；周末需求向中午和下午移动。
- **member 构成需求基本盘。** 2025 年 member 贡献 4,729,273 次骑行，占 71.03%；casual 的日间与周末特征更明显。
- **高频节点集中在交通枢纽和中心城区。** Columbus Circle / Union Station 全年出发与到达合计 114,448 次，位居第一。
- **站点方向差可转化为调度线索。** departures 高于 arrivals 的站点适合优先监测缺车风险，反向站点应关注满桩和车辆积压；年度差值不是实时库存证明。
- **OD 同时覆盖通勤接驳和休闲环线。** Union Station 周边短距离线路频率高；Gravelly Point、National Mall 等同站还车线路时长更长。

![工作日与周末每小时骑行量](figures/01_hourly_weekday_weekend.png)

![member 与 casual 每小时使用模式](figures/04_user_hour_profile.png)

![站点出发到达差值](figures/06_station_balance.png)

![Top 50 OD 交互地图预览](figures/08_interactive_od_map.png)

完整交互地图见 [docs/interactive_od_map.html](docs/interactive_od_map.html)。全部 8 张精选图表保留在 [`figures/`](figures/)；原有用户、时段、站点和 OD 分析代码均继续保留。

## Pricing Policy Analysis

Capital Bikeshare 于 **2025-08-01** 调整价格。项目使用 2024–2025 年历史数据，以 member 作为相对参照，通过 DID/DDD 分析 casual classic-bike 的政策相关需求变化，并用 2024 同历日窗口控制通常的用户组季节性差异。2024-08-01 仅是历史同期日历切点，不代表 2024 存在价格政策。

DDD 的点估计方向为负（约 -17.0%），但区间包含零，统计证据不足，因此不能表述为已确认的因果效应。观察性结果只说明调价时点附近存在值得进一步实验验证的相对需求变化。

### Standardized Price Exposure

对 **511,765 次**调价前 casual classic-bike 骑行固定其实际时长，并分别套用新旧 Single Ride 公示费率：

| 标准化单次成本 | Mean |
| --- | ---: |
| Old-price equivalent | **$2.40** |
| New-price equivalent | **$5.19** |

该指标是 **Single-Ride-equivalent standardized ride cost**：它衡量固定调价前行为下的挂牌费率暴露，不是实际订单价格、客单价、ARPU 或实际收入。公开 trip history 无法识别 casual 用户实际购买的票种。

作为弱化的描述性补充，调价后 casual classic-bike 平均骑行时长小幅下降，但中位数基本稳定；该结果只条件于骑行已经发生，不作为核心因果发现。

- 完整结果与不确定性：[docs/pricing_policy_results.md](docs/pricing_policy_results.md)
- 模型、趋势与时长口径：[docs/pricing_policy_methodology.md](docs/pricing_policy_methodology.md)

## A/B Test Proposal

观察性结果进一步转化为一个账户级价格激励方案。**该 A/B Test 仅为实验设计，尚未实际在线执行。**

| 设计项 | 方案 |
| --- | --- |
| Randomization unit | eligible casual account |
| Treatment | targeted ride discount / credit |
| Control | current pricing |
| Primary metric | 7-day completed-ride conversion |
| Guardrails | revenue/user、promotion cost、full-price cannibalization |
| 显著性水平 / power | α = 0.05 / 80% |
| Illustrative MDE | 2 percentage points |
| Illustrative sample size | 约 **6,508 accounts / group** |

实验采用 ITT 原则；资格条件、观察窗口、主指标和样本量应在上线前冻结。完整方案见 [docs/ab_test_design.md](docs/ab_test_design.md)。

## Key Artifacts

| 类型 | 文件 |
| --- | --- |
| 分析脚本 | [analysis/pricing_policy_analysis.py](analysis/pricing_policy_analysis.py) |
| 结果文档 | [docs/pricing_policy_results.md](docs/pricing_policy_results.md) |
| 方法文档 | [docs/pricing_policy_methodology.md](docs/pricing_policy_methodology.md) |
| A/B Test 方案 | [docs/ab_test_design.md](docs/ab_test_design.md) |
| 日级分析表 | [data/aggregates/pricing_daily_segments.csv](data/aggregates/pricing_daily_segments.csv) |
| DID/DDD 与趋势结果 | [data/aggregates/pricing_model_results.csv](data/aggregates/pricing_model_results.csv) |
| 标准化价格暴露 | [data/aggregates/pricing_exposure_summary.csv](data/aggregates/pricing_exposure_summary.csv) |
| 时长汇总 | [data/aggregates/duration_policy_summary.csv](data/aggregates/duration_policy_summary.csv) |
| 时长分桶 | [data/aggregates/duration_bucket_share.csv](data/aggregates/duration_bucket_share.csv) |

其余 2025 用户、时段、站点与 OD 聚合表见 [data/README.md](data/README.md)。

## Reproduce

### 1. 准备数据

从 [Capital Bikeshare System Data](https://capitalbikeshare.com/system-data) 下载月度 trip history。将 2025 文件用于主运营分析；若要完整复现 DDD，同时准备 2024 和 2025 两个完整自然年的月度文件。可放入被忽略的 `data/raw/`，也可通过 `--input-dir` 指定外部目录。

### 2. 安装并运行

```bash
python -m pip install -r requirements.txt
python src/prepare_data.py
python analysis/run_all.py
python analysis/pricing_policy_analysis.py --input-dir <2024-2025-tripdata-directory> --strict-ddd
```

`src/prepare_data.py` 生成 2025 运营分析聚合表；`pricing_policy_analysis.py` 生成定价日表、模型结果、标准化价格暴露、时长汇总和覆盖审计。原始及行级数据由 `.gitignore` 排除。

## Data Source and Limitations

- 数据来源：[Capital Bikeshare System Data](https://capitalbikeshare.com/system-data)
- 数据许可：[Capital Bikeshare Data License Agreement](https://capitalbikeshare.com/data-license-agreement)
- station/OD 分析仅覆盖起终点站信息同时完整的 68.21% 清洗后记录。
- 天气、节假日、活动、车辆供给和同期运营变化仍可能影响观察性 DID/DDD；DDD 不自动构成因果证明。
- 公开数据不含账户、票种、订单金额、优惠、税费或实际调度信息。
- OD 连线表示起终点关系，不代表真实骑行轨迹；年度站点方向差不能直接证明某一时刻缺车或满桩。
- 本项目与 Capital Bikeshare、Lyft 或其成员辖区无隶属、赞助或背书关系，未使用官方 Logo。

## English Summary

This project combines 2025 Capital Bikeshare user-demand and operations analysis with a 2024–2025 pricing-policy study. It preserves the original temporal, user, station, and OD foundations, then adds observational DID/DDD estimates, a standardized Single Ride rate-card exposure, descriptive ride-duration summaries, and an account-level A/B test proposal. The negative DDD point estimate is statistically uncertain and is not presented as confirmed causality. Raw and row-level trip data are intentionally excluded.
