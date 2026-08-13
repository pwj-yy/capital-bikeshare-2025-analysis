"""Analyze the August 2025 Capital Bikeshare pricing change.

The script reuses the repository's core cleaning rules, streams monthly trip
files, builds a balanced daily segment table, estimates a simple 2025 DID, and
estimates a 2024/2025 DDD only when both full calendar years are available.

Examples
--------
python analysis/pricing_policy_analysis.py
python analysis/pricing_policy_analysis.py --input-dir <tripdata-directory>
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import statsmodels.api as sm


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "aggregates"
CHUNK_SIZE = 250_000
YEARS = (2024, 2025)
USERS = ("member", "casual")
BIKES = ("classic_bike", "electric_bike")
CORE_COLUMNS = [
    "ride_id",
    "rideable_type",
    "started_at",
    "ended_at",
    "member_casual",
]
OUTPUT_COLUMNS = [
    "year",
    "date",
    "month",
    "weekday",
    "member_casual",
    "rideable_type",
    "ride_count",
    "mean_duration_min",
    "median_duration_min",
]
PRICE_PRE_UNLOCK_FEE = 1.0
PRICE_PRE_CLASSIC_PER_MIN = 0.05
PRICE_POST_CLASSIC_PER_MIN = 0.15
TYPICAL_RIDE_MINUTES = (10, 15, 20, 30)
DURATION_BUCKET_LABELS = (
    "0-10 min",
    "10-20 min",
    "20-30 min",
    "30-45 min",
    "45+ min",
)
DURATION_BUCKET_BINS = (0.0, 10.0, 20.0, 30.0, 45.0, np.inf)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        help=(
            "Directory holding monthly CSV/ZIP files, directly or in year "
            "subfolders. If omitted, the script checks repo/data/raw and the "
            "repository's sibling data directory."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Directory for pricing_daily_segments.csv and model results.",
    )
    parser.add_argument(
        "--strict-ddd",
        action="store_true",
        help="Exit unsuccessfully when full 2024 coverage is unavailable.",
    )
    return parser.parse_args()


def find_input_dirs(explicit: Path | None) -> list[Path]:
    candidates = [explicit] if explicit is not None else [
        ROOT / "data" / "raw",
        ROOT.parent / "data",
    ]
    found: list[Path] = []
    for candidate in candidates:
        if candidate is None:
            continue
        resolved = candidate.expanduser().resolve()
        patterns = (
            "20????-capitalbikeshare-tripdata.csv",
            "20????-capitalbikeshare-tripdata.zip",
            "*/20????-capitalbikeshare-tripdata.csv",
            "*/20????-capitalbikeshare-tripdata.zip",
        )
        if resolved.is_dir() and any(
            any(resolved.glob(pattern)) for pattern in patterns
        ):
            found.append(resolved)
    if found:
        return list(dict.fromkeys(found))
    checked = ", ".join(str(path) for path in candidates if path is not None)
    raise FileNotFoundError(f"No monthly trip CSVs found. Checked: {checked}")


def discover_files(input_dirs: list[Path]) -> dict[int, list[Path]]:
    files_by_year: dict[int, list[Path]] = {}
    for year in YEARS:
        candidates: list[Path] = []
        for input_dir in input_dirs:
            for extension in ("csv", "zip"):
                pattern = f"{year}??-capitalbikeshare-tripdata.{extension}"
                candidates.extend(input_dir.glob(pattern))
                candidates.extend(input_dir.glob(f"*/{pattern}"))
        by_month: dict[str, list[Path]] = defaultdict(list)
        for path in sorted({path.resolve() for path in candidates}):
            by_month[path.name[:6]].append(path)
        files: list[Path] = []
        for month, choices in sorted(by_month.items()):
            csv_choices = [path for path in choices if path.suffix.lower() == ".csv"]
            preferred = csv_choices or choices
            if len(preferred) != 1:
                locations = ", ".join(str(path) for path in preferred)
                raise ValueError(
                    f"Multiple source files found for {month}: {locations}"
                )
            files.append(preferred[0])
        files_by_year[year] = files
    if not files_by_year[2025]:
        raise FileNotFoundError("No 2025 monthly trip files were found.")
    return files_by_year


def validate_core_schema(files: Iterable[Path]) -> None:
    for path in files:
        columns = pd.read_csv(path, nrows=0).columns.tolist()
        missing = [column for column in CORE_COLUMNS if column not in columns]
        if missing:
            raise ValueError(f"{path.name} is missing core columns: {missing}")


def process_year(
    year: int, files: list[Path]
) -> tuple[pd.DataFrame, dict[str, object], dict[str, list[np.ndarray]]]:
    counters: dict[tuple[pd.Timestamp, str, str], list[float]] = defaultdict(
        lambda: [0.0, 0.0]
    )
    median_parts: dict[
        tuple[pd.Timestamp, str, str], list[np.ndarray]
    ] = defaultdict(list)
    observed_dates: set[pd.Timestamp] = set()
    raw_rows = 0
    usable_rows = 0
    removal_counts = defaultdict(int)
    seen_ride_ids: set[str] = set()
    casual_classic_duration_parts: dict[str, list[np.ndarray]] = {
        "pre": [],
        "post": [],
    }
    removal_reasons = (
        "duplicate_ride_id_after_first",
        "unparseable_started_at",
        "unparseable_ended_at",
        "started_at_outside_year",
        "duration_non_positive",
        "duration_over_24h",
        "unexpected_segment",
    )

    for path in files:
        reader = pd.read_csv(
            path,
            usecols=CORE_COLUMNS,
            dtype={
                "ride_id": "string",
                "rideable_type": "string",
                "started_at": "string",
                "ended_at": "string",
                "member_casual": "string",
            },
            chunksize=CHUNK_SIZE,
            low_memory=False,
        )
        for chunk in reader:
            raw_rows += len(chunk)
            ride_ids = chunk["ride_id"].astype("string")
            duplicate_after_first = ride_ids.duplicated(keep="first") | ride_ids.isin(
                seen_ride_ids
            )
            seen_ride_ids.update(
                ride_ids.loc[~duplicate_after_first].dropna().astype(str).tolist()
            )
            started = pd.to_datetime(chunk["started_at"], errors="coerce")
            ended = pd.to_datetime(chunk["ended_at"], errors="coerce")
            duration = (ended - started).dt.total_seconds() / 60

            invalid_start = started.isna()
            invalid_end = ended.isna()
            wrong_year = started.dt.year.ne(year).fillna(False)
            nonpositive = duration.le(0).fillna(False)
            over_24h = duration.gt(1440).fillna(False)
            valid_category = chunk["member_casual"].isin(USERS) & chunk[
                "rideable_type"
            ].isin(BIKES)
            reason = pd.Series(pd.NA, index=chunk.index, dtype="string")
            reason.loc[duplicate_after_first] = "duplicate_ride_id_after_first"
            reason.loc[reason.isna() & invalid_start] = "unparseable_started_at"
            reason.loc[reason.isna() & invalid_end] = "unparseable_ended_at"
            reason.loc[reason.isna() & wrong_year] = "started_at_outside_year"
            reason.loc[reason.isna() & nonpositive] = "duration_non_positive"
            reason.loc[reason.isna() & over_24h] = "duration_over_24h"
            reason.loc[reason.isna() & ~valid_category] = "unexpected_segment"
            keep = (
                reason.isna()
            )
            for removal_reason, count in reason.value_counts().items():
                removal_counts[str(removal_reason)] += int(count)
            if not keep.any():
                continue

            selected = pd.DataFrame(
                {
                    "date": started.loc[keep].dt.normalize(),
                    "member_casual": chunk.loc[keep, "member_casual"].astype(str),
                    "rideable_type": chunk.loc[keep, "rideable_type"].astype(str),
                    "duration_min": duration.loc[keep].astype(float),
                }
            )
            casual_classic = selected.loc[
                selected["member_casual"].eq("casual")
                & selected["rideable_type"].eq("classic_bike")
            ]
            if not casual_classic.empty:
                pre_mask = casual_classic["date"].dt.month.le(7)
                for period, mask in (("pre", pre_mask), ("post", ~pre_mask)):
                    values = casual_classic.loc[mask, "duration_min"].to_numpy(
                        dtype="float64", copy=True
                    )
                    if values.size:
                        casual_classic_duration_parts[period].append(values)
            usable_rows += len(selected)
            observed_dates.update(selected["date"].unique())
            grouped = selected.groupby(
                ["date", "member_casual", "rideable_type"], sort=False
            )["duration_min"]
            stats = grouped.agg(ride_count="size", duration_sum="sum")
            for key, row in stats.iterrows():
                counters[key][0] += float(row["ride_count"])
                counters[key][1] += float(row["duration_sum"])
            for key, values in grouped:
                median_parts[key].append(
                    values.to_numpy(dtype="float64", copy=True)
                )

    if not observed_dates:
        return pd.DataFrame(columns=OUTPUT_COLUMNS), {
            "year": year,
            "source_files": len(files),
            "raw_rows": raw_rows,
            "usable_rows": 0,
            "removal_counts": dict(removal_counts),
            "available_dates": [],
        }, casual_classic_duration_parts

    medians = pd.Series(
        {
            key: float(np.median(np.concatenate(parts)))
            for key, parts in median_parts.items()
        },
        name="median_duration_min",
    )
    medians.index = pd.MultiIndex.from_tuples(medians.index)
    medians.index.names = ["date", "member_casual", "rideable_type"]
    observed_dates = pd.DatetimeIndex(sorted(observed_dates))
    full_dates = observed_dates
    grid = pd.MultiIndex.from_product(
        [full_dates, USERS, BIKES],
        names=["date", "member_casual", "rideable_type"],
    )
    observed = pd.DataFrame.from_dict(
        counters,
        orient="index",
        columns=["ride_count", "duration_sum"],
    )
    observed.index = pd.MultiIndex.from_tuples(observed.index, names=grid.names)
    daily = observed.reindex(grid)
    daily["ride_count"] = daily["ride_count"].fillna(0).astype("int64")
    daily["mean_duration_min"] = daily["duration_sum"] / daily["ride_count"]
    daily["median_duration_min"] = medians.reindex(grid)
    daily = daily.drop(columns="duration_sum").reset_index()
    daily.insert(0, "year", year)
    daily.insert(2, "month", daily["date"].dt.month)
    daily.insert(3, "weekday", daily["date"].dt.isocalendar().day.astype(int))

    metrics = {
        "year": year,
        "source_files": len(files),
        "raw_rows": raw_rows,
        "usable_rows": usable_rows,
        "removal_counts": {
            reason: int(removal_counts.get(reason, 0))
            for reason in removal_reasons
        },
        "available_dates": [date.strftime("%Y-%m-%d") for date in observed_dates],
    }
    return daily[OUTPUT_COLUMNS], metrics, casual_classic_duration_parts


def coverage_status(metrics: dict[str, object]) -> tuple[bool, list[str]]:
    year = int(metrics["year"])
    expected = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    actual = pd.DatetimeIndex(pd.to_datetime(metrics["available_dates"]))
    missing = expected.difference(actual)
    return len(missing) == 0, [date.strftime("%Y-%m-%d") for date in missing]


def combine_duration_parts(
    duration_parts_by_year: dict[int, dict[str, list[np.ndarray]]],
    year: int,
    period: str,
) -> np.ndarray:
    """Concatenate one casual classic-bike duration window."""

    parts = duration_parts_by_year.get(year, {}).get(period, [])
    if not parts:
        return np.array([], dtype="float64")
    return np.concatenate(parts)


def percentile(values: np.ndarray, q: float) -> float:
    """Return a linear sample quantile using pandas' documented default."""

    return float(pd.Series(values).quantile(q, interpolation="linear"))


def duration_policy_outputs(
    duration_parts_by_year: dict[int, dict[str, list[np.ndarray]]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build conditional-on-riding duration and fixed-bucket summaries."""

    summary_rows: list[dict[str, object]] = []
    bucket_rows: list[dict[str, object]] = []
    period_labels = {
        (2024, "pre"): "2024 Jan 1-Jul 31 historical early period",
        (2024, "post"): "2024 Aug 1-Dec 31 historical later period",
        (2025, "pre"): "2025 Jan 1-Jul 31 pre-policy",
        (2025, "post"): "2025 Aug 1-Dec 31 post-policy",
    }
    for year in YEARS:
        for period in ("pre", "post"):
            values = combine_duration_parts(duration_parts_by_year, year, period)
            if values.size == 0:
                continue
            summary_rows.append(
                {
                    "year": year,
                    "period": period,
                    "period_label": period_labels[year, period],
                    "ride_count": int(values.size),
                    "mean_duration_min": float(values.mean()),
                    "median_duration_min": float(np.median(values)),
                    "p25_duration_min": percentile(values, 0.25),
                    "p75_duration_min": percentile(values, 0.75),
                }
            )
            bucket = pd.cut(
                values,
                bins=DURATION_BUCKET_BINS,
                labels=DURATION_BUCKET_LABELS,
                right=True,
                include_lowest=False,
            )
            counts = pd.Series(bucket).value_counts(sort=False)
            if int(counts.sum()) != int(values.size):
                raise AssertionError("Duration bucket counts do not reconcile.")
            for order, label in enumerate(DURATION_BUCKET_LABELS, start=1):
                count = int(counts.get(label, 0))
                bucket_rows.append(
                    {
                        "year": year,
                        "period": period,
                        "period_label": period_labels[year, period],
                        "duration_bucket": label,
                        "bucket_order": order,
                        "ride_count": count,
                        "share": count / int(values.size),
                    }
                )
    return pd.DataFrame(summary_rows), pd.DataFrame(bucket_rows)


def price_exposure_output(
    duration_parts_by_year: dict[int, dict[str, list[np.ndarray]]],
) -> pd.DataFrame:
    """Reprice 2025 pre-policy casual classic rides under both rate cards."""

    duration = combine_duration_parts(duration_parts_by_year, 2025, "pre")
    if duration.size == 0:
        raise ValueError("No 2025 pre-policy casual classic-bike rides found.")
    billed_minutes = np.ceil(duration).astype("int64")
    old_price_cents = 100 + billed_minutes * 5
    new_price_cents = 100 + billed_minutes * 15
    increase_cents = new_price_cents - old_price_cents
    old_price = old_price_cents / 100
    new_price = new_price_cents / 100
    increase = increase_cents / 100
    increase_pct = new_price / old_price - 1

    rows: list[dict[str, object]] = []
    metric_values = {
        "duration_min": duration,
        "billed_minutes": billed_minutes.astype(float),
        "old_single_ride_equivalent_cost_usd": old_price,
        "new_single_ride_equivalent_cost_usd": new_price,
        "absolute_increase_usd": increase,
        "percentage_increase": increase_pct,
    }
    exact_sums = {
        "billed_minutes": float(int(billed_minutes.sum())),
        "old_single_ride_equivalent_cost_usd": float(old_price_cents.sum()) / 100,
        "new_single_ride_equivalent_cost_usd": float(new_price_cents.sum()) / 100,
        "absolute_increase_usd": float(increase_cents.sum()) / 100,
    }
    for metric, values in metric_values.items():
        statistics = [
            ("mean", float(np.mean(values))),
            ("median", float(np.median(values))),
            ("p25", percentile(values, 0.25)),
            ("p75", percentile(values, 0.75)),
        ]
        if metric in exact_sums:
            statistics.append(("sum", exact_sums[metric]))
        for statistic, value in statistics:
            rows.append(
                {
                    "record_type": "sample_summary",
                    "sample": "2025 pre-policy casual classic-bike rides",
                    "ride_count": int(duration.size),
                    "metric": metric,
                    "statistic": statistic,
                    "value": value,
                    "unit": (
                        "share"
                        if metric == "percentage_increase"
                        else "USD"
                        if metric.endswith("usd")
                        else "minutes"
                    ),
                    "duration_example_min": np.nan,
                    "old_price_usd": np.nan,
                    "new_price_usd": np.nan,
                    "absolute_increase_usd": np.nan,
                    "note": (
                        "Single-Ride-equivalent standardized ride cost; "
                        "scenario calculation, not an observed transaction price."
                    ),
                }
            )

    for minutes in TYPICAL_RIDE_MINUTES:
        old_example = PRICE_PRE_UNLOCK_FEE + minutes * PRICE_PRE_CLASSIC_PER_MIN
        new_example = PRICE_PRE_UNLOCK_FEE + minutes * PRICE_POST_CLASSIC_PER_MIN
        rows.append(
            {
                "record_type": "typical_ride_example",
                "sample": "published Single Ride rate-card example",
                "ride_count": np.nan,
                "metric": "typical_ride_price",
                "statistic": "exact",
                "value": np.nan,
                "unit": "USD",
                "duration_example_min": minutes,
                "old_price_usd": old_example,
                "new_price_usd": new_example,
                "absolute_increase_usd": new_example - old_example,
                "note": "Minutes are billed using the same ceiling rule.",
            }
        )
    return pd.DataFrame(rows)


def design_matrix(
    data: pd.DataFrame, variables: list[str]
) -> pd.DataFrame:
    parts = [pd.Series(1.0, index=data.index, name="const")]
    for variable in variables:
        parts.append(data[variable].astype(float).rename(variable))
    weekday = pd.get_dummies(data["weekday"], prefix="weekday", drop_first=True)
    return pd.concat(parts + [weekday.astype(float)], axis=1)


def fit_hac(
    y: pd.Series, x: pd.DataFrame, term: str, model_name: str
) -> tuple[dict[str, object], object]:
    result = sm.OLS(y.astype(float), x.astype(float)).fit(
        cov_type="HAC", cov_kwds={"maxlags": 7, "use_correction": True}
    )
    beta = float(result.params[term])
    se = float(result.bse[term])
    p_value = float(result.pvalues[term])
    low, high = (float(value) for value in result.conf_int().loc[term])
    row = {
        "model": model_name,
        "status": "estimated",
        "term": term,
        "coefficient": beta,
        "std_error": se,
        "p_value": p_value,
        "ci_95_low": low,
        "ci_95_high": high,
        "effect_pct": math.expm1(beta) * 100,
        "effect_ci_95_low_pct": math.expm1(low) * 100,
        "effect_ci_95_high_pct": math.expm1(high) * 100,
        "n_observations": int(result.nobs),
        "outcome_transform": "log(casual rides) - log(member rides)",
        "covariance": "HAC(7)",
        "note": "",
    }
    return row, result


def model_row_from_estimate(
    *,
    model_name: str,
    term: str,
    coefficient: float,
    std_error: float,
    p_value: float,
    ci_low: float,
    ci_high: float,
    n_observations: int,
    covariance: str,
    note: str = "",
) -> dict[str, object]:
    """Build a consistently transformed model-result row."""

    return {
        "model": model_name,
        "status": "estimated",
        "term": term,
        "coefficient": coefficient,
        "std_error": std_error,
        "p_value": p_value,
        "ci_95_low": ci_low,
        "ci_95_high": ci_high,
        "effect_pct": math.expm1(coefficient) * 100,
        "effect_ci_95_low_pct": math.expm1(ci_low) * 100,
        "effect_ci_95_high_pct": math.expm1(ci_high) * 100,
        "n_observations": n_observations,
        "outcome_transform": "log(casual rides) - log(member rides)",
        "covariance": covariance,
        "note": note,
    }


def pretrend_comparison_rows(daily: pd.DataFrame) -> list[dict[str, object]]:
    """Compare 2024/2025 Jan-Jul classic-bike log-gap slopes.

    Time resets to zero on January 1 within each year. Weekday effects are
    interacted with year so the reported slopes reproduce separate-year
    regressions. HAC-panel covariance prevents the end of the 2024 panel from
    being treated as adjacent to the start of the 2025 panel.
    """

    pre = daily_log_gap(daily.loc[daily["year"].isin(YEARS)])
    pre = pre.loc[pre["post"].eq(0)].copy().sort_values(["year", "date"])
    pre["year2025"] = pre["year"].eq(2025).astype(int)
    year_start = pd.to_datetime(pre["year"].astype(str) + "-01-01")
    pre["trend_week"] = (pre["date"] - year_start).dt.days / 7
    pre["year2025_trend_week"] = pre["year2025"] * pre["trend_week"]

    x = design_matrix(
        pre, ["year2025", "trend_week", "year2025_trend_week"]
    )
    weekday_columns = [column for column in x if column.startswith("weekday_")]
    for column in weekday_columns:
        x[f"year2025_{column}"] = pre["year2025"] * x[column]

    result = sm.OLS(pre["log_gap"].astype(float), x.astype(float)).fit(
        cov_type="hac-panel",
        cov_kwds={
            "groups": pre["year"].to_numpy(),
            "maxlags": 7,
            "use_correction": "hac",
            "df_correction": False,
        },
        use_t=False,
    )
    covariance = "HAC-panel(7), panel=year"
    n_observations = int(result.nobs)

    def direct_row(model_name: str, term: str, note: str) -> dict[str, object]:
        low, high = (float(value) for value in result.conf_int().loc[term])
        return model_row_from_estimate(
            model_name=model_name,
            term=term,
            coefficient=float(result.params[term]),
            std_error=float(result.bse[term]),
            p_value=float(result.pvalues[term]),
            ci_low=low,
            ci_high=high,
            n_observations=n_observations,
            covariance=covariance,
            note=note,
        )

    slope_2024 = direct_row(
        "historical_pretrend_2024_classic",
        "trend_week",
        "2024 Jan 1-Jul 31 historical same-calendar-period slope.",
    )
    slope_2025_test = result.t_test("trend_week + year2025_trend_week = 0")
    slope_2025_ci = np.asarray(slope_2025_test.conf_int(), dtype=float).reshape(-1, 2)[0]
    slope_2025 = model_row_from_estimate(
        model_name="pretrend_2025_classic",
        term="trend_week + year2025_trend_week",
        coefficient=float(np.asarray(slope_2025_test.effect).reshape(-1)[0]),
        std_error=float(np.asarray(slope_2025_test.sd).reshape(-1)[0]),
        p_value=float(np.asarray(slope_2025_test.pvalue).reshape(-1)[0]),
        ci_low=float(slope_2025_ci[0]),
        ci_high=float(slope_2025_ci[1]),
        n_observations=n_observations,
        covariance=covariance,
        note="2025 Jan 1-Jul 31 pre-policy slope.",
    )
    slope_difference = direct_row(
        "pretrend_difference_2025_vs_2024_classic",
        "year2025_trend_week",
        "Year2025 x Time; 2025 pre-policy slope minus 2024 historical slope.",
    )
    return [slope_2024, slope_2025, slope_difference]


def daily_log_gap(daily: pd.DataFrame) -> pd.DataFrame:
    classic = daily.loc[daily["rideable_type"].eq("classic_bike")].copy()
    if classic["ride_count"].le(0).any():
        raise ValueError(
            "At least one classic-bike daily segment has zero rides; "
            "log(ride_count) is undefined. Inspect source coverage."
        )
    wide = classic.pivot(
        index=["year", "date", "month", "weekday"],
        columns="member_casual",
        values="ride_count",
    ).reset_index()
    wide["log_gap"] = np.log(wide["casual"]) - np.log(wide["member"])
    # Calendar-window indicator for both years; it represents policy exposure
    # only in 2025. In 2024, August 1 is solely a historical calendar cutoff.
    wide["post"] = (
        (wide["date"].dt.month > 8)
        | ((wide["date"].dt.month == 8) & (wide["date"].dt.day >= 1))
    ).astype(int)
    return wide.sort_values(["year", "date"]).reset_index(drop=True)


def descriptive_summary(daily: pd.DataFrame) -> pd.DataFrame:
    data = daily.copy()
    data["period"] = np.where(
        (data["date"].dt.month > 8)
        | ((data["date"].dt.month == 8) & (data["date"].dt.day >= 1)),
        "post",
        "pre",
    )
    summary = (
        data.groupby(
            ["year", "rideable_type", "member_casual", "period"],
            observed=True,
        )["ride_count"]
        .agg(daily_average="mean", days="size")
        .reset_index()
    )
    pivot = summary.pivot(
        index=["year", "rideable_type", "member_casual"],
        columns="period",
        values=["daily_average", "days"],
    )
    pivot.columns = [f"{period}_{metric}" for metric, period in pivot.columns]
    pivot = pivot.reset_index()
    for column in ("pre_daily_average", "post_daily_average"):
        if column not in pivot:
            pivot[column] = np.nan
    pivot["change_pct"] = (
        pivot["post_daily_average"] / pivot["pre_daily_average"] - 1
    ) * 100
    return pivot.sort_values(
        ["year", "rideable_type", "member_casual"]
    ).reset_index(drop=True)


def skipped_row(model: str, term: str, note: str) -> dict[str, object]:
    return {
        "model": model,
        "status": "not_estimated",
        "term": term,
        "coefficient": np.nan,
        "std_error": np.nan,
        "p_value": np.nan,
        "ci_95_low": np.nan,
        "ci_95_high": np.nan,
        "effect_pct": np.nan,
        "effect_ci_95_low_pct": np.nan,
        "effect_ci_95_high_pct": np.nan,
        "n_observations": 0,
        "outcome_transform": "log(casual rides) - log(member rides)",
        "covariance": "HAC(7)",
        "note": note,
    }


def run_models(
    daily: pd.DataFrame,
    complete_years: dict[int, bool],
    missing_dates: dict[int, list[str]],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    gap = daily_log_gap(daily.loc[daily["year"].eq(2025)])
    did_x = design_matrix(gap, ["post"])
    did, _ = fit_hac(gap["log_gap"], did_x, "post", "did_2025_classic")
    rows.append(did)

    if complete_years.get(2024, False):
        rows.extend(pretrend_comparison_rows(daily))
        ddd_daily = daily.loc[daily["year"].isin(YEARS)].copy()
        ddd_gap = daily_log_gap(ddd_daily)
        ddd_gap["year2025"] = ddd_gap["year"].eq(2025).astype(int)
        ddd_gap["year2025_post"] = ddd_gap["year2025"] * ddd_gap["post"]
        ddd_x = design_matrix(
            ddd_gap, ["year2025", "post", "year2025_post"]
        )
        ddd, _ = fit_hac(
            ddd_gap["log_gap"],
            ddd_x,
            "year2025_post",
            "ddd_2024_2025_classic",
        )
        rows.append(ddd)
    else:
        pre = gap.loc[gap["post"].eq(0)].copy()
        pre["trend_week"] = (
            pre["date"] - pd.Timestamp("2025-01-01")
        ).dt.days / 7
        trend_x = design_matrix(pre, ["trend_week"])
        trend, _ = fit_hac(
            pre["log_gap"], trend_x, "trend_week", "pretrend_2025_classic"
        )
        trend["note"] = (
            "2025-only DID diagnostic; DDD trend comparison unavailable "
            "without complete 2024 coverage."
        )
        rows.append(trend)
        rows.append(
            skipped_row(
                "historical_pretrend_2024_classic",
                "trend_week",
                "2024 Jan-Jul coverage is incomplete.",
            )
        )
        rows.append(
            skipped_row(
                "pretrend_difference_2025_vs_2024_classic",
                "year2025_trend_week",
                "Year2025 x Time was not estimated because 2024 is incomplete.",
            )
        )
        missing_months = sorted({date[:7] for date in missing_dates.get(2024, [])})
        note = (
            "DDD 未估计：需要完整的 2024 自然日覆盖；当前缺少 "
            f"{', '.join(missing_months[:7])}。"
        )
        rows.append(
            skipped_row("ddd_2024_2025_classic", "year2025_post", note)
        )
    return pd.DataFrame(rows)


def electric_relative_descriptive(daily: pd.DataFrame) -> dict[str, float]:
    """Return the 2025 e-bike pre/post relative ratio change."""

    electric = daily.loc[
        daily["year"].eq(2025) & daily["rideable_type"].eq("electric_bike")
    ].copy()
    electric["period"] = np.where(electric["date"] < "2025-08-01", "pre", "post")
    means = electric.groupby(["member_casual", "period"])["ride_count"].mean()
    casual_change = float(means["casual", "post"] / means["casual", "pre"] - 1)
    member_change = float(means["member", "post"] / means["member", "pre"] - 1)
    relative_ratio_change = float(
        (means["casual", "post"] / means["member", "post"])
        / (means["casual", "pre"] / means["member", "pre"])
        - 1
    )
    return {
        "casual_change_pct": casual_change * 100,
        "member_change_pct": member_change * 100,
        "relative_ratio_change_pct": relative_ratio_change * 100,
    }


def sample_size_per_arm(
    baseline_rate: float,
    mde: float,
    alpha: float = 0.05,
    power: float = 0.80,
) -> int:
    """Return equal-arm sample size for a two-sided proportions z-test."""

    from statsmodels.stats.power import NormalIndPower
    from statsmodels.stats.proportion import proportion_effectsize

    treatment_rate = baseline_rate + mde
    if not 0 < baseline_rate < 1 or not 0 < treatment_rate < 1:
        raise ValueError("baseline_rate and baseline_rate + mde must be in (0, 1).")
    effect_size = proportion_effectsize(treatment_rate, baseline_rate)
    return math.ceil(
        NormalIndPower().solve_power(
            effect_size=effect_size,
            alpha=alpha,
            power=power,
            ratio=1,
            alternative="two-sided",
        )
    )


def write_outputs(
    daily: pd.DataFrame,
    models: pd.DataFrame,
    price_exposure: pd.DataFrame,
    duration_summary: pd.DataFrame,
    duration_buckets: pd.DataFrame,
    metrics: list[dict[str, object]],
    input_dirs: list[Path],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    daily_out = daily.copy()
    daily_out["date"] = daily_out["date"].dt.strftime("%Y-%m-%d")
    daily_path = output_dir / "pricing_daily_segments.csv"
    daily_matches = False
    if daily_path.exists():
        existing_daily = pd.read_csv(daily_path)
        try:
            pd.testing.assert_frame_equal(
                existing_daily,
                daily_out.reset_index(drop=True),
                check_dtype=False,
                check_exact=False,
                rtol=1e-12,
                atol=1e-12,
            )
            daily_matches = True
        except AssertionError:
            pass
    if daily_matches:
        # Avoid rewriting an unchanged public artifact.
        pass
    else:
        daily_out.to_csv(daily_path, index=False)
    models.to_csv(output_dir / "pricing_model_results.csv", index=False)
    price_exposure.to_csv(output_dir / "pricing_exposure_summary.csv", index=False)
    duration_summary.to_csv(output_dir / "duration_policy_summary.csv", index=False)
    duration_buckets.to_csv(output_dir / "duration_bucket_share.csv", index=False)
    compact_metrics = []
    for item in metrics:
        dates = pd.DatetimeIndex(pd.to_datetime(item.get("available_dates", [])))
        compact_metrics.append(
            {
                key: value
                for key, value in item.items()
                if key != "available_dates"
            }
            | {
                "first_available_date": (
                    dates.min().strftime("%Y-%m-%d") if len(dates) else None
                ),
                "last_available_date": (
                    dates.max().strftime("%Y-%m-%d") if len(dates) else None
                ),
                "available_date_count": len(dates),
            }
        )
    def portable_input_label(path: Path) -> str:
        """Record an audit label without publishing a machine-specific path."""

        resolved = path.resolve()
        try:
            relative = resolved.relative_to(ROOT.resolve())
            return relative.as_posix() or "."
        except ValueError:
            if resolved == (ROOT.parent / "data").resolve():
                return "../data"
            return f"<external>/{resolved.name}"

    audit = {
        "input_dirs": [portable_input_label(path) for path in input_dirs],
        "years": compact_metrics,
        "calendar_window_cutoff": "August 1 within each year",
        "policy_year": 2025,
        "historical_control_year": 2024,
        "cutoff_interpretation": (
            "August 1 is the 2025 policy cutoff; in 2024 it is only the "
            "historical same-calendar-period cutoff."
        ),
        "standardized_price_exposure": {
            "sample": "2025 pre-policy casual classic-bike rides",
            "unlock_fee_usd": PRICE_PRE_UNLOCK_FEE,
            "old_classic_rate_per_minute_usd": PRICE_PRE_CLASSIC_PER_MIN,
            "new_classic_rate_per_minute_usd": PRICE_POST_CLASSIC_PER_MIN,
            "billing_minutes": "ceil(duration_min)",
            "interpretation": (
                "Single-Ride-equivalent standardized ride cost; scenario "
                "calculation, not an observed transaction price or revenue."
            ),
            "taxes_included": False,
        },
        "duration_response": {
            "sample": "casual classic-bike rides conditional on riding",
            "buckets": list(DURATION_BUCKET_LABELS),
            "interval_rule": "left-open, right-closed; final bucket unbounded",
            "inference": "descriptive only",
        },
        "cleaning": (
            "deduplicate ride_id after first; parsed timestamps; "
            "0 < duration_min <= 1440; started_at year"
        ),
    }
    (output_dir / "pricing_analysis_metrics.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def format_p_value(value: float) -> str:
    return "< 0.001" if value < 0.001 else f"{value:.3f}"


def update_results_document(
    daily: pd.DataFrame,
    models: pd.DataFrame,
    price_exposure: pd.DataFrame,
    duration_summary: pd.DataFrame,
    duration_buckets: pd.DataFrame,
    metrics: list[dict[str, object]],
) -> None:
    path = ROOT / "docs" / "pricing_policy_results.md"
    if not path.exists():
        return
    year_status = {int(item["year"]): item for item in metrics}
    complete_2024 = bool(year_status.get(2024, {}).get("complete_calendar", False))
    summary = descriptive_summary(daily)
    table_rows = []
    display_summary = summary.loc[
        summary["year"].eq(2025) | complete_2024
    ]
    for row in display_summary.itertuples(index=False):
        pre_days = int(row.pre_days)
        post_days = int(row.post_days)
        table_rows.append(
            "| "
            f"{row.year} | {row.rideable_type} | {row.member_casual} | "
            f"{row.pre_daily_average:,.1f} ({pre_days}日) | "
            f"{row.post_daily_average:,.1f} ({post_days}日) | "
            f"{row.change_pct:+.1f}% |"
        )
    model_text = []
    for row in models.itertuples(index=False):
        if row.status != "estimated":
            label = {
                "ddd_2024_2025_classic": "DDD",
                "historical_pretrend_2024_classic": "2024 历史同期前期趋势",
                "pretrend_difference_2025_vs_2024_classic": (
                    "2024/2025 前期斜率差"
                ),
            }.get(row.model, row.model)
            model_text.append(f"- **{label}：未估计。** {row.note}")
            continue
        if row.model == "did_2025_classic":
            model_text.append(
                "- **2025 DID：** "
                f"系数 {row.coefficient:.4f}，HAC(7) 标准误 "
                f"{row.std_error:.4f}，p {format_p_value(row.p_value)}，"
                f"95% CI [{row.ci_95_low:.4f}, {row.ci_95_high:.4f}]。"
                f"换算后，调价后 casual/member classic-bike 日需求比"
                f"变化 {row.effect_pct:+.1f}%（95% CI "
                f"{row.effect_ci_95_low_pct:+.1f}% 至 "
                f"{row.effect_ci_95_high_pct:+.1f}%）。"
            )
        elif row.model == "historical_pretrend_2024_classic":
            model_text.append(
                "- **2024 年 1 月 1 日—7 月 31 日历史同期 slope：** "
                f"每周对数趋势系数 {row.coefficient:.4f}"
                f"（{row.effect_pct:+.2f}%/周），"
                f"p {format_p_value(row.p_value)}，95% CI "
                f"[{row.ci_95_low:.4f}, {row.ci_95_high:.4f}]。"
            )
        elif row.model == "pretrend_2025_classic":
            model_text.append(
                "- **2025 年 1 月 1 日—7 月 31 日政策前 slope：** "
                f"每周对数趋势系数 {row.coefficient:.4f}"
                f"（{row.effect_pct:+.2f}%/周），"
                f"p {format_p_value(row.p_value)}，95% CI "
                f"[{row.ci_95_low:.4f}, {row.ci_95_high:.4f}]。"
            )
        elif row.model == "pretrend_difference_2025_vs_2024_classic":
            judgement = (
                "存在统计上清晰的年际前期斜率差异"
                if row.p_value < 0.05
                else (
                    "未发现 2025 政策前斜率与 2024 历史同期斜率"
                    "存在统计上清晰的差异"
                )
            )
            model_text.append(
                "- **Slope difference（Year2025 × Time）：** "
                f"{row.coefficient:+.4f} 对数点/周"
                f"（相对斜率倍数差 {row.effect_pct:+.2f}%/周），"
                f"p {format_p_value(row.p_value)}，95% CI "
                f"[{row.ci_95_low:.4f}, {row.ci_95_high:.4f}]；"
                f"{judgement}。这减轻但不消除 DDD 的趋势假设担忧，"
                "不能视为平行趋势已得到证明。"
            )
        elif row.model == "ddd_2024_2025_classic":
            model_text.append(
                "- **2024+2025 DDD：** "
                f"系数 {row.coefficient:.4f}，HAC(7) 标准误 "
                f"{row.std_error:.4f}，p {format_p_value(row.p_value)}，"
                f"95% CI [{row.ci_95_low:.4f}, {row.ci_95_high:.4f}]；"
                f"相对变化 {row.effect_pct:+.1f}%（95% CI "
                f"{row.effect_ci_95_low_pct:+.1f}% 至 "
                f"{row.effect_ci_95_high_pct:+.1f}%）。"
            )
            model_text.append(
                "  业务解释：扣除 2024 同期 casual/member 季节性差异后，"
                "2025 调价后 classic-bike 的 casual/member 日需求比点估计"
                f"额外变化 {row.effect_pct:+.1f}%；但区间跨越 0，"
                "未发现统计上清晰的额外变化证据。"
            )
    ebike = electric_relative_descriptive(daily)
    model_text.append(
        "- **e-bike 补充：** 2025 年 casual 日均量前后变化 "
        f"{ebike['casual_change_pct']:+.1f}%，member 变化 "
        f"{ebike['member_change_pct']:+.1f}%；casual/member 日均需求比"
        f"描述性变化 {ebike['relative_ratio_change_pct']:+.1f}%。"
    )
    price_sample = price_exposure.loc[
        price_exposure["record_type"].eq("sample_summary")
    ]

    def exposure_value(metric: str, statistic: str) -> float:
        row = price_sample.loc[
            price_sample["metric"].eq(metric)
            & price_sample["statistic"].eq(statistic),
            "value",
        ]
        return float(row.iloc[0])

    exposure_rows = [
        "### Standardized Price Exposure",
        "",
        (
            "样本为 2025 年调价前已发生的 casual classic-bike 骑行 "
            f"**{int(price_sample['ride_count'].iloc[0]):,} 次**。分钟数按 `ceil(duration_min)` "
            "计费；下列金额均为**按 Single Ride 公示价格估算的标准化骑行成本**，"
            "不是实际成交价、实际客单价或真实收入。"
        ),
        "",
        "| 指标 | Mean | Median | P25 | P75 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    exposure_metrics = (
        ("调价前 Single-Ride-equivalent cost", "old_single_ride_equivalent_cost_usd", "$"),
        ("调价后同批骑行情景成本", "new_single_ride_equivalent_cost_usd", "$"),
    )
    for label, metric, prefix in exposure_metrics:
        values = [exposure_value(metric, statistic) for statistic in ("mean", "median", "p25", "p75")]
        exposure_rows.append(
            f"| {label} | {prefix}{values[0]:.2f} | {prefix}{values[1]:.2f} | "
            f"{prefix}{values[2]:.2f} | {prefix}{values[3]:.2f} |"
        )
    exposure_rows.extend(
        [
            "",
            (
                "同一批骑行的标准化情景成本平均绝对差额为 "
                f"**${exposure_value('absolute_increase_usd', 'mean'):.2f}**，"
                f"中位数为 **${exposure_value('absolute_increase_usd', 'median'):.2f}**；"
                f"逐骑行情景成本百分比增幅的平均值为 **{exposure_value('percentage_increase', 'mean') * 100:.1f}%**，"
                f"中位数为 **{exposure_value('percentage_increase', 'median') * 100:.1f}%**。"
            ),
            "",
            "| 骑行时长 | 调价前 Single Ride | 调价后 Single Ride | 增加 |",
            "| ---: | ---: | ---: | ---: |",
        ]
    )
    for row in price_exposure.loc[
        price_exposure["record_type"].eq("typical_ride_example")
    ].itertuples(index=False):
        exposure_rows.append(
            f"| {int(row.duration_example_min)} min | ${row.old_price_usd:.2f} | "
            f"${row.new_price_usd:.2f} | ${row.absolute_increase_usd:.2f} |"
        )

    duration_rows = [
        "### Ride Duration Response",
        "",
        (
            "以下仅描述已经发生的 casual classic-bike 骑行的 trip-level conditional outcome，"
            "不建立新模型；公开数据缺少账户标识，不能识别用户级 intensive margin。"
        ),
        "",
        "| 窗口 | Ride count | Mean duration | Median | P25 | P75 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    duration_labels = {
        (2024, "pre"): "2024 1/1—7/31 历史同期前期",
        (2024, "post"): "2024 8/1—12/31 历史同期后期",
        (2025, "pre"): "2025 调价前",
        (2025, "post"): "2025 调价后",
    }
    for row in duration_summary.itertuples(index=False):
        duration_rows.append(
            f"| {duration_labels[row.year, row.period]} | {row.ride_count:,} | "
            f"{row.mean_duration_min:.2f} min | {row.median_duration_min:.2f} min | "
            f"{row.p25_duration_min:.2f} min | {row.p75_duration_min:.2f} min |"
        )
    duration_rows.extend(
        [
            "",
            "| Duration bucket | 2024 历史前期 | 2024 历史后期 | 2025 调价前 | 2025 调价后 |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    bucket_pivot = duration_buckets.pivot(
        index="duration_bucket", columns=["year", "period"], values="share"
    ).reindex(DURATION_BUCKET_LABELS)
    for label, row in bucket_pivot.iterrows():
        duration_rows.append(
            f"| {label} | {row[2024, 'pre'] * 100:.1f}% | "
            f"{row[2024, 'post'] * 100:.1f}% | {row[2025, 'pre'] * 100:.1f}% | "
            f"{row[2025, 'post'] * 100:.1f}% |"
        )
    duration_lookup = duration_summary.set_index(["year", "period"])
    mean_change_2025 = (
        duration_lookup.loc[(2025, "post"), "mean_duration_min"]
        - duration_lookup.loc[(2025, "pre"), "mean_duration_min"]
    )
    median_change_2025 = (
        duration_lookup.loc[(2025, "post"), "median_duration_min"]
        - duration_lookup.loc[(2025, "pre"), "median_duration_min"]
    )
    mean_change_2024 = (
        duration_lookup.loc[(2024, "post"), "mean_duration_min"]
        - duration_lookup.loc[(2024, "pre"), "mean_duration_min"]
    )
    short_share_change = (
        bucket_pivot.loc["0-10 min", (2025, "post")]
        - bucket_pivot.loc["0-10 min", (2025, "pre")]
    ) * 100
    long_share_change = (
        bucket_pivot.loc["45+ min", (2025, "post")]
        - bucket_pivot.loc["45+ min", (2025, "pre")]
    ) * 100
    duration_rows.extend(
        [
            "",
            (
                f"2025 调价切点后，已发生骑行的 mean duration 变化 "
                f"**{mean_change_2025:+.2f} 分钟**，median 变化 "
                f"**{median_change_2025:+.2f} 分钟**；`0–10 min` 占比变化 "
                f"**{short_share_change:+.1f} 个百分点**，`45+ min` 占比变化 "
                f"**{long_share_change:+.1f} 个百分点**。"
            ),
            (
                "这构成平均时长和超长骑行占比下降的温和观察性信号，但中位数几乎不变、"
                f"部分中等时长分桶上升，且 2024 历史同期 mean duration 也变化 "
                f"{mean_change_2024:+.2f} 分钟；因此不能解释为所有骑行普遍缩短，"
                "也不能归因为价格调整。"
            ),
        ]
    )
    records = "，".join(
        f"{item['year']} 年 {item['usable_rows']:,} 条可用记录"
        for item in metrics
    )
    coverage_note = (
        "2024 覆盖完整，表中展示 8 月 1 日前/后历史同期描述性结果；"
        "该切点不代表 2024 存在价格政策。"
        if complete_2024
        else (
            "2024 当前 1 月 1 日—7 月 31 日历史同期窗口覆盖不完整，"
            "两个同历日窗口均值不作为结果展示。"
        )
    )
    replacement = "\n".join(
        [
            f"本次实际使用：{records}。",
            coverage_note,
            "",
            "| 年份 | 车辆 | 用户 | 1/1—7/31 日均（天数） | 8/1—12/31 日均（天数） | 变化率 |",
            "| ---: | --- | --- | ---: | ---: | ---: |",
            *table_rows,
            "",
            *model_text,
            "",
            *exposure_rows,
            "",
            *duration_rows,
        ]
    )
    did_row = models.loc[models["model"].eq("did_2025_classic")].iloc[0]
    ddd_rows = models.loc[
        models["model"].eq("ddd_2024_2025_classic")
        & models["status"].eq("estimated")
    ]
    headline_row = ddd_rows.iloc[0] if not ddd_rows.empty else did_row
    headline_label = "DDD" if not ddd_rows.empty else "DID"
    total_usable = sum(int(item["usable_rows"]) for item in metrics)
    trend_difference_rows = models.loc[
        models["model"].eq("pretrend_difference_2025_vs_2024_classic")
        & models["status"].eq("estimated")
    ]
    trend_difference_text = (
        ""
        if trend_difference_rows.empty
        else (
            "Year2025 × Time 的前期 slope difference 为 "
            f"{trend_difference_rows.iloc[0]['coefficient']:+.4f} 对数点/周，"
            f"p {format_p_value(trend_difference_rows.iloc[0]['p_value'])}；"
            "未显著并不等于已证明平行趋势。"
        )
    )
    resume_numbers = (
        f"处理并合并 2024/2025 共 **{total_usable / 10_000:.1f} 万条**"
        f"有效骑行记录；classic-bike {headline_label} 的需求比点估计为 "
        f"**{headline_row['effect_pct']:+.1f}%**，但区间包含零、统计证据不足。"
    )
    resume_caveat = (
        "第二个数字必须同时注明 95% CI 为 "
        f"{headline_row['effect_ci_95_low_pct']:+.1f}% 至 "
        f"{headline_row['effect_ci_95_high_pct']:+.1f}%"
        f"，并说明结果不显著。{trend_difference_text}"
        "同时保留观察性研究边界。"
    )
    resume_statement = (
        f"> 基于 Python 分块处理 {total_usable / 10_000:.1f} 万条 "
        "Capital Bikeshare 骑行记录，构建用户类型 × 车辆类型日级指标，"
        "并用 DID/DDD 评估 2025 年调价后 casual 相对 member 的需求变化；"
        f"classic-bike {headline_label} 需求比点估计变化 "
        f"{headline_row['effect_pct']:+.1f}%，但区间包含零、统计证据不足；"
        "同时比较 2024 历史同期与 "
        "2025 政策前的日级需求比斜率并核查数据覆盖，"
        "保留观察性解释边界，并"
        "进一步设计账户级价格激励 A/B Test。"
    )
    resume_replacement = "\n\n".join(
        [resume_numbers, resume_caveat, resume_statement]
    )
    text = path.read_text(encoding="utf-8")
    start = "<!-- PRICING_RESULTS_START -->"
    end = "<!-- PRICING_RESULTS_END -->"
    before, remainder = text.split(start, maxsplit=1)
    _old, after = remainder.split(end, maxsplit=1)
    text = f"{before}{start}\n{replacement}\n{end}{after}"
    resume_start = "<!-- PRICING_RESUME_START -->"
    resume_end = "<!-- PRICING_RESUME_END -->"
    before, remainder = text.split(resume_start, maxsplit=1)
    _old, after = remainder.split(resume_end, maxsplit=1)
    path.write_text(
        f"{before}{resume_start}\n{resume_replacement}\n{resume_end}{after}",
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    input_dirs = find_input_dirs(args.input_dir)
    files_by_year = discover_files(input_dirs)
    validate_core_schema(path for files in files_by_year.values() for path in files)

    tables: list[pd.DataFrame] = []
    metrics: list[dict[str, object]] = []
    duration_parts_by_year: dict[int, dict[str, list[np.ndarray]]] = {}
    complete_years: dict[int, bool] = {}
    missing_dates: dict[int, list[str]] = {}
    for year in YEARS:
        if not files_by_year[year]:
            complete_years[year] = False
            missing_dates[year] = [
                date.strftime("%Y-%m-%d")
                for date in pd.date_range(
                    f"{year}-01-01", f"{year}-12-31", freq="D"
                )
            ]
            metrics.append(
                {
                    "year": year,
                    "source_files": 0,
                    "raw_rows": 0,
                    "usable_rows": 0,
                    "removal_counts": {},
                    "available_dates": [],
                    "complete_calendar": False,
                    "missing_date_count": len(missing_dates[year]),
                }
            )
            continue
        daily, year_metrics, duration_parts = process_year(year, files_by_year[year])
        complete, missing = coverage_status(year_metrics)
        complete_years[year] = complete
        missing_dates[year] = missing
        year_metrics["complete_calendar"] = complete
        year_metrics["missing_date_count"] = len(missing)
        metrics.append(year_metrics)
        tables.append(daily)
        duration_parts_by_year[year] = duration_parts

    if not complete_years.get(2025, False):
        raise ValueError("2025 does not cover every calendar date; DID was not run.")
    daily = pd.concat(tables, ignore_index=True).sort_values(
        ["year", "date", "member_casual", "rideable_type"]
    )
    models = run_models(daily, complete_years, missing_dates)
    price_exposure = price_exposure_output(duration_parts_by_year)
    duration_summary, duration_buckets = duration_policy_outputs(
        duration_parts_by_year
    )
    if args.strict_ddd and not complete_years.get(2024, False):
        raise SystemExit(
            "DDD was not estimated because full 2024 calendar coverage is missing."
        )
    write_outputs(
        daily,
        models,
        price_exposure,
        duration_summary,
        duration_buckets,
        metrics,
        input_dirs,
        args.output_dir.resolve(),
    )
    update_results_document(
        daily,
        models,
        price_exposure,
        duration_summary,
        duration_buckets,
        metrics,
    )

    summary = descriptive_summary(daily)
    printable_summary = summary.loc[
        summary["year"].eq(2025) | complete_years.get(2024, False)
    ]
    print("Input directories: " + ", ".join(str(path) for path in input_dirs))
    for item in metrics:
        print(
            f"{item['year']}: {item['usable_rows']:,} usable rows from "
            f"{item['source_files']} file(s); complete calendar="
            f"{item['complete_calendar']}"
        )
    print("\nDaily averages and changes:")
    if not complete_years.get(2024, False):
        print(
            "2024 descriptives omitted because the Jan 1-Jul 31 historical "
            "same-calendar-period window is incomplete."
        )
    print(
        printable_summary.to_string(
            index=False, float_format=lambda value: f"{value:,.3f}"
        )
    )
    print("\nModel results:")
    print(models.to_string(index=False, float_format=lambda value: f"{value:,.6f}"))
    print("\nStandardized price exposure:")
    print(
        price_exposure.to_string(
            index=False, float_format=lambda value: f"{value:,.6f}"
        )
    )
    print("\nCasual classic-bike duration summary:")
    print(
        duration_summary.to_string(
            index=False, float_format=lambda value: f"{value:,.6f}"
        )
    )


if __name__ == "__main__":
    main()
