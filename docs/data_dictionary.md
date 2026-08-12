# 数据字典

## 官方原始字段

| 字段 | 类型/示例 | 说明 |
| --- | --- | --- |
| `ride_id` | string | 骑行记录标识，用于重复 ID 审计。 |
| `rideable_type` | string | 车辆类型，如 `classic_bike`、`electric_bike`。 |
| `started_at` | datetime text | 骑行开始时间；作为年份、日期和小时口径。 |
| `ended_at` | datetime text | 骑行结束时间。 |
| `start_station_name` | string/null | 起点站名称。 |
| `start_station_id` | string/null | 起点站 ID，优先用于站点匹配。 |
| `end_station_name` | string/null | 终点站名称。 |
| `end_station_id` | string/null | 终点站 ID，优先用于站点匹配。 |
| `start_lat` | float | 起点纬度。 |
| `start_lng` | float | 起点经度。 |
| `end_lat` | float | 终点纬度。 |
| `end_lng` | float | 终点经度。 |
| `member_casual` | category | `member`（会员）或 `casual`（临时用户）。 |

## 清洗脚本派生字段

| 字段 | 类型 | 计算/用途 |
| --- | --- | --- |
| `source_file` | string | 原始月度文件名，保留行级来源。 |
| `source_month` | string | 文件名中的 `YYYYMM`。 |
| `duration_min` | float | `(ended_at - started_at)`，单位为分钟。 |
| `date` | date text | 从 `started_at` 提取的 `YYYY-MM-DD`。 |
| `month` | integer | 从 `started_at` 提取的月份。 |
| `weekday` | integer | ISO 星期：周一为 1，周日为 7。 |
| `hour` | integer | 开始小时，0-23。 |
| `is_weekend` | boolean | 周六或周日为 `True`。 |
| `station_pair_complete` | boolean | 起终点 ID 均完整，或起终点名称均完整。 |
| `start_end_pair_id` | string/null | OD 分析键：优先 ID 组合，缺失时回退到名称组合。 |
| `start_end_pair_name` | string/null | 展示用的“起点 -> 终点”文本。 |
| `same_station` | boolean/null | 是否同站还车；优先按 ID 判断。 |

## 聚合表

聚合表不是原始明细数据。各文件含义见 [`data/README.md`](../data/README.md)。

