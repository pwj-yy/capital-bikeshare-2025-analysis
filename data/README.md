# 数据目录说明

本仓库不提交 Capital Bikeshare 原始或清洗后的行级数据。

## 本地目录

- `raw/`：用户自行从官方数据页下载的 12 个 2025 月度 ZIP/CSV；已被 `.gitignore` 排除。
- `processed/`：使用 `--write-row-level` 时生成的大型本地明细；已被 `.gitignore` 排除。
- `aggregates/`：公开提交的小型派生表和质量审计，用于复现仓库图表。

## aggregates 文件

| 文件 | 内容 |
| --- | --- |
| `pipeline_metrics.json` | 核心行数、站点数、OD 数和重复 ID 结果。 |
| `schema_check.csv` | 12 个输入文件的字段一致性检查。 |
| `monthly_quality_summary.csv` | 各月原始/清洗/删除和 station/OD 完整率。 |
| `cleaning_removed_records_summary.csv` | 按规则汇总的删除行数。 |
| `monthly_rides.csv` | 月度骑行量。 |
| `weekday_hour.csv` | 星期 x 小时骑行量。 |
| `hour_by_daytype.csv` | 工作日/周末 x 小时骑行量。 |
| `user_type_summary.csv` | member/casual 计数、占比和时长摘要。 |
| `duration_by_user_type.csv` | 用户类型时长分位数。 |
| `user_hour_share.csv` | 用户类型内部的每小时占比。 |
| `user_daytype_daily.csv` | 工作日/周末日均与周末强度指数。 |
| `station_usage.csv` | 站点 departures、arrivals 和总使用量。 |
| `station_balance.csv` | 完整 station/OD 子集中的方向性差值。 |
| `top_od_routes.csv` | 仓库展示用 Top 10 OD；完整重跑会输出 Top 100 的次数、时长和工作日占比。 |
| `pricing_daily_segments.csv` | 2024 历史同期参照与 2025 政策分析所用的日期 × 用户类型 × 车辆类型日级表。 |
| `pricing_model_results.csv` | 2025 DID、2024/2025 前期斜率及 `Year2025 × Time` 差异检验、以及（数据完整时）2024/2025 DDD 的核心系数。 |
| `pricing_analysis_metrics.json` | 定价分析实际输入目录、记录数、清洗删除项和日期覆盖审计。 |
| `pricing_exposure_summary.csv` | 2025 调价前 casual classic-bike 骑行在新旧 Single Ride 挂牌费率下的标准化情景成本汇总及典型时长示例；不是实际成交价。 |
| `duration_policy_summary.csv` | 2024 历史同期与 2025 调价前后 casual classic-bike 已发生骑行的时长摘要。 |
| `duration_bucket_share.csv` | 同一四个日历窗口的固定 duration bucket 骑行数与占比。 |

数据来源与许可请参阅项目根目录 README。本目录中的派生表仅作为非商业分析的可复现证据，不应重新包装为独立数据集分发。
