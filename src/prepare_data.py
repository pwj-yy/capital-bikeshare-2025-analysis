"""Prepare Capital Bikeshare 2025 data and portfolio aggregates.

The pipeline reads the 12 official monthly trip-history files in chunks, checks
their schemas, validates timestamps and ride duration, removes duplicate ride
IDs, builds temporal and station/OD fields, and writes compact aggregate tables.

Row-level outputs are optional because they are large and must not be committed.
Run ``python src/prepare_data.py --help`` for usage.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import shutil
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, TextIO

import numpy as np
import pandas as pd


PROJECT_YEAR = 2025
CHUNK_SIZE = 250_000
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = ROOT / "data" / "raw"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "aggregates"

CSV_PATTERN = f"{PROJECT_YEAR}??-capitalbikeshare-tripdata.csv"
ZIP_PATTERN = f"{PROJECT_YEAR}??-capitalbikeshare-tripdata.zip"

EXPECTED_COLUMNS = [
    "ride_id",
    "rideable_type",
    "started_at",
    "ended_at",
    "start_station_name",
    "start_station_id",
    "end_station_name",
    "end_station_id",
    "start_lat",
    "start_lng",
    "end_lat",
    "end_lng",
    "member_casual",
]

DERIVED_COLUMNS = [
    "source_file",
    "source_month",
    "duration_min",
    "date",
    "month",
    "weekday",
    "hour",
    "is_weekend",
    "station_pair_complete",
    "start_end_pair_id",
    "start_end_pair_name",
    "same_station",
]


@dataclass
class MonthlyQuality:
    """Accumulators for one source month."""

    raw_rows: int = 0
    cleaned_rows: int = 0
    removed_rows: int = 0
    station_pair_complete_rows: int = 0
    duration_under_1_min_rows: int = 0
    member_counts: Counter = field(default_factory=Counter)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing the 12 monthly CSV or ZIP files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for compact aggregate and audit tables.",
    )
    parser.add_argument(
        "--write-row-level",
        action="store_true",
        help="Also write large cleaned and station/OD CSV.GZ files locally.",
    )
    return parser.parse_args()


def discover_input_files(input_dir: Path) -> list[Path]:
    """Return the 12 official monthly files, preferring extracted CSV files."""

    csv_files = sorted(input_dir.glob(CSV_PATTERN))
    files = csv_files or sorted(input_dir.glob(ZIP_PATTERN))
    if len(files) != 12:
        raise FileNotFoundError(
            f"Expected 12 files matching {CSV_PATTERN} or {ZIP_PATTERN} in "
            f"{input_dir}, found {len(files)}."
        )
    return files


def read_header(path: Path) -> list[str]:
    return list(pd.read_csv(path, nrows=0).columns)


def read_trip_file(
    path: Path, *, usecols: list[str] | None = None
) -> Iterable[pd.DataFrame]:
    """Stream one monthly file with stable string dtypes."""

    string_columns = {
        column: "string"
        for column in EXPECTED_COLUMNS
        if column
        not in {"start_lat", "start_lng", "end_lat", "end_lng"}
    }
    dtype = (
        {key: value for key, value in string_columns.items() if key in usecols}
        if usecols is not None
        else string_columns
    )
    return pd.read_csv(
        path,
        usecols=usecols,
        dtype=dtype,
        chunksize=CHUNK_SIZE,
        low_memory=False,
    )


def nonempty(series: pd.Series) -> pd.Series:
    return series.notna() & series.astype("string").str.strip().ne("")


def nullable_pair(
    left: pd.Series, right: pd.Series, separator: str = " -> "
) -> pd.Series:
    left_text = left.astype("string").str.strip()
    right_text = right.astype("string").str.strip()
    complete = nonempty(left_text) & nonempty(right_text)
    result = pd.Series(pd.NA, index=left.index, dtype="string")
    result.loc[complete] = (
        left_text.loc[complete] + separator + right_text.loc[complete]
    )
    return result


def station_key(station_id: pd.Series, station_name: pd.Series) -> pd.Series:
    ids = station_id.astype("string").str.strip()
    names = station_name.astype("string").str.strip()
    result = ids.where(nonempty(ids), names)
    return result.where(nonempty(result), pd.NA)


def pair_key(data: pd.DataFrame) -> pd.Series:
    id_pair = nullable_pair(data["start_station_id"], data["end_station_id"])
    name_pair = nullable_pair(
        data["start_station_name"], data["end_station_name"]
    )
    return id_pair.fillna(name_pair)


def same_station(data: pd.DataFrame) -> pd.Series:
    ids_complete = nonempty(data["start_station_id"]) & nonempty(
        data["end_station_id"]
    )
    names_complete = nonempty(data["start_station_name"]) & nonempty(
        data["end_station_name"]
    )
    result = pd.Series(pd.NA, index=data.index, dtype="boolean")
    result.loc[ids_complete] = (
        data.loc[ids_complete, "start_station_id"].astype("string").str.strip()
        == data.loc[ids_complete, "end_station_id"].astype("string").str.strip()
    ).to_numpy()
    fallback = ~ids_complete & names_complete
    result.loc[fallback] = (
        data.loc[fallback, "start_station_name"].astype("string").str.strip()
        == data.loc[fallback, "end_station_name"].astype("string").str.strip()
    ).to_numpy()
    return result


def validate_schema(files: list[Path], output_dir: Path) -> None:
    """Fail fast when a monthly file differs from the official 13-column schema."""

    rows = []
    for path in files:
        header = read_header(path)
        rows.append(
            {
                "source_file": path.name,
                "column_count": len(header),
                "column_names": "|".join(header),
                "schema_match": header == EXPECTED_COLUMNS,
            }
        )
    audit = pd.DataFrame(rows)
    audit.to_csv(output_dir / "schema_check.csv", index=False)
    if not audit["schema_match"].all():
        raise ValueError("Input schemas differ; inspect schema_check.csv.")


def find_duplicate_ride_ids(files: list[Path]) -> set[str]:
    """Find duplicate IDs across all chunks without retaining full rows."""

    seen: set[str] = set()
    duplicates: set[str] = set()
    for path in files:
        for chunk in read_trip_file(path, usecols=["ride_id"]):
            for ride_id in chunk["ride_id"].dropna().astype(str):
                if ride_id in seen:
                    duplicates.add(ride_id)
                else:
                    seen.add(ride_id)
    return duplicates


def add_derived_columns(
    data: pd.DataFrame, *, source_file: str, source_month: str
) -> pd.DataFrame:
    data = data.copy()
    started = pd.to_datetime(data["started_at"], errors="coerce")
    ended = pd.to_datetime(data["ended_at"], errors="coerce")
    data.insert(0, "source_month", source_month)
    data.insert(0, "source_file", source_file)
    data["duration_min"] = (
        (ended - started).dt.total_seconds() / 60
    ).round(6)
    data["date"] = started.dt.strftime("%Y-%m-%d")
    data["month"] = started.dt.month.astype("Int64")
    data["weekday"] = started.dt.isocalendar().day.astype("Int64")
    data["hour"] = started.dt.hour.astype("Int64")
    data["is_weekend"] = data["weekday"].isin([6, 7])

    station_ids_complete = nonempty(data["start_station_id"]) & nonempty(
        data["end_station_id"]
    )
    station_names_complete = nonempty(data["start_station_name"]) & nonempty(
        data["end_station_name"]
    )
    data["station_pair_complete"] = (
        station_ids_complete | station_names_complete
    )
    data["start_end_pair_id"] = pair_key(data)
    data["start_end_pair_name"] = nullable_pair(
        data["start_station_name"], data["end_station_name"]
    )
    data["same_station"] = same_station(data)
    return data


def clean_chunk(
    chunk: pd.DataFrame,
    *,
    seen_duplicate_ids: set[str],
    duplicate_ids: set[str],
    source_file: str,
    source_month: str,
) -> tuple[pd.DataFrame, pd.Series]:
    """Apply the auditable row filters and return cleaned rows plus reasons."""

    started = pd.to_datetime(chunk["started_at"], errors="coerce")
    ended = pd.to_datetime(chunk["ended_at"], errors="coerce")
    duration = (ended - started).dt.total_seconds() / 60

    duplicate_after_first = pd.Series(False, index=chunk.index)
    if duplicate_ids:
        is_duplicate_id = chunk["ride_id"].astype(str).isin(duplicate_ids)
        for index in chunk.index[is_duplicate_id]:
            ride_id = str(chunk.at[index, "ride_id"])
            if ride_id in seen_duplicate_ids:
                duplicate_after_first.at[index] = True
            else:
                seen_duplicate_ids.add(ride_id)

    reason = pd.Series(pd.NA, index=chunk.index, dtype="string")
    reason.loc[duplicate_after_first] = "duplicate_ride_id_after_first"
    reason.loc[reason.isna() & started.isna()] = "unparseable_started_at"
    reason.loc[reason.isna() & ended.isna()] = "unparseable_ended_at"
    outside_year = (started < f"{PROJECT_YEAR}-01-01") | (
        started >= f"{PROJECT_YEAR + 1}-01-01"
    )
    reason.loc[reason.isna() & outside_year.fillna(False)] = (
        "started_at_outside_2025"
    )
    reason.loc[reason.isna() & (duration <= 0).fillna(False)] = (
        "duration_non_positive"
    )
    reason.loc[reason.isna() & (duration > 1440).fillna(False)] = (
        "duration_over_24h"
    )

    cleaned = chunk.loc[reason.isna()].copy()
    if not cleaned.empty:
        cleaned = add_derived_columns(
            cleaned,
            source_file=source_file,
            source_month=source_month,
        )
    return cleaned, reason


def update_counter(counter: Counter, grouped: pd.Series) -> None:
    for key, value in grouped.items():
        counter[key] += int(value)


def update_station_usage(
    data: pd.DataFrame,
    departures: Counter,
    arrivals: Counter,
    metadata: dict[str, dict[str, object]],
) -> None:
    """Count station usage from any valid trip end, not only complete OD rows."""

    sides = [
        (
            "start",
            station_key(data["start_station_id"], data["start_station_name"]),
            departures,
        ),
        (
            "end",
            station_key(data["end_station_id"], data["end_station_name"]),
            arrivals,
        ),
    ]
    for prefix, keys, counter in sides:
        frame = pd.DataFrame(
            {
                "station_key": keys,
                "station_name": data[f"{prefix}_station_name"],
                "lat": data[f"{prefix}_lat"],
                "lng": data[f"{prefix}_lng"],
            }
        )
        frame = frame.loc[nonempty(frame["station_key"])]
        if frame.empty:
            continue
        update_counter(counter, frame.groupby("station_key").size())
        for row in frame.drop_duplicates("station_key").itertuples(index=False):
            key = str(row.station_key)
            current = metadata.setdefault(
                key,
                {
                    "station_name": pd.NA,
                    "lat": np.nan,
                    "lng": np.nan,
                },
            )
            if pd.isna(current["station_name"]) and pd.notna(row.station_name):
                current["station_name"] = row.station_name
            if pd.isna(current["lat"]) and pd.notna(row.lat):
                current["lat"] = float(row.lat)
            if pd.isna(current["lng"]) and pd.notna(row.lng):
                current["lng"] = float(row.lng)


def open_row_level_writers(
    output_dir: Path, enabled: bool
) -> tuple[TextIO | None, TextIO | None]:
    if not enabled:
        return None, None
    processed = output_dir.parent / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    cleaned = gzip.open(
        processed / "capital_bikeshare_2025_cleaned.csv.gz",
        "wt",
        encoding="utf-8",
        newline="",
        compresslevel=1,
    )
    station_od = gzip.open(
        processed / "capital_bikeshare_2025_station_od.csv.gz",
        "wt",
        encoding="utf-8",
        newline="",
        compresslevel=1,
    )
    return cleaned, station_od


def process_files(
    files: list[Path],
    duplicate_ids: set[str],
    output_dir: Path,
    write_row_level: bool,
) -> dict[str, object]:
    monthly_quality: dict[str, MonthlyQuality] = defaultdict(MonthlyQuality)
    removal_counts: Counter = Counter()
    monthly_counts: Counter = Counter()
    weekday_hour_counts: Counter = Counter()
    daytype_hour_counts: Counter = Counter()
    user_counts: Counter = Counter()
    user_hour_counts: Counter = Counter()
    user_daytype_counts: Counter = Counter()
    user_duration_files: dict[str, Path] = {}
    duration_handles: dict[str, TextIO] = {}
    station_departures: Counter = Counter()
    station_arrivals: Counter = Counter()
    station_metadata: dict[str, dict[str, object]] = {}
    balance_departures: Counter = Counter()
    balance_arrivals: Counter = Counter()
    od_counts: Counter = Counter()
    od_duration_sum: Counter = Counter()
    od_weekday_counts: Counter = Counter()
    od_names: dict[str, str] = {}

    seen_duplicate_ids: set[str] = set()
    raw_rows = cleaned_rows = station_od_rows = 0
    cleaned_writer, station_writer = open_row_level_writers(
        output_dir, write_row_level
    )
    first_cleaned = first_station = True

    with tempfile.TemporaryDirectory(prefix="cabi-duration-") as temp_dir:
        try:
            for path in files:
                source_month = path.name[:6]
                quality = monthly_quality[source_month]
                for chunk in read_trip_file(path):
                    raw_rows += len(chunk)
                    quality.raw_rows += len(chunk)
                    cleaned, reason = clean_chunk(
                        chunk,
                        seen_duplicate_ids=seen_duplicate_ids,
                        duplicate_ids=duplicate_ids,
                        source_file=path.name,
                        source_month=source_month,
                    )
                    removal_counts.update(reason.dropna().tolist())
                    quality.removed_rows += int(reason.notna().sum())
                    if cleaned.empty:
                        continue

                    row_count = len(cleaned)
                    cleaned_rows += row_count
                    quality.cleaned_rows += row_count
                    quality.station_pair_complete_rows += int(
                        cleaned["station_pair_complete"].sum()
                    )
                    quality.duration_under_1_min_rows += int(
                        (cleaned["duration_min"] < 1).sum()
                    )
                    quality.member_counts.update(
                        cleaned["member_casual"].dropna().astype(str).tolist()
                    )

                    update_counter(monthly_counts, cleaned.groupby("month").size())
                    update_counter(
                        weekday_hour_counts,
                        cleaned.groupby(["weekday", "hour"]).size(),
                    )
                    update_counter(
                        daytype_hour_counts,
                        cleaned.groupby(["is_weekend", "hour"]).size(),
                    )
                    update_counter(
                        user_counts, cleaned.groupby("member_casual").size()
                    )
                    update_counter(
                        user_hour_counts,
                        cleaned.groupby(["member_casual", "hour"]).size(),
                    )
                    update_counter(
                        user_daytype_counts,
                        cleaned.groupby(["member_casual", "is_weekend"]).size(),
                    )
                    update_station_usage(
                        cleaned,
                        station_departures,
                        station_arrivals,
                        station_metadata,
                    )

                    for user_type, group in cleaned.groupby("member_casual"):
                        user = str(user_type)
                        if user not in duration_handles:
                            temp_path = Path(temp_dir) / f"{user}.txt"
                            user_duration_files[user] = temp_path
                            duration_handles[user] = temp_path.open(
                                "w", encoding="utf-8", newline=""
                            )
                        writer = csv.writer(duration_handles[user])
                        writer.writerows(
                            [[value] for value in group["duration_min"].to_numpy()]
                        )

                    station = cleaned.loc[
                        cleaned["station_pair_complete"]
                        & nonempty(cleaned["start_end_pair_id"])
                    ].copy()
                    station_od_rows += len(station)
                    if not station.empty:
                        start_keys = station_key(
                            station["start_station_id"], station["start_station_name"]
                        )
                        end_keys = station_key(
                            station["end_station_id"], station["end_station_name"]
                        )
                        update_counter(balance_departures, start_keys.value_counts())
                        update_counter(balance_arrivals, end_keys.value_counts())

                        grouped = station.groupby("start_end_pair_id", dropna=True)
                        update_counter(od_counts, grouped.size())
                        for key, value in grouped["duration_min"].sum().items():
                            od_duration_sum[str(key)] += float(value)
                        update_counter(
                            od_weekday_counts,
                            station.loc[~station["is_weekend"]]
                            .groupby("start_end_pair_id")
                            .size(),
                        )
                        names = station[
                            ["start_end_pair_id", "start_end_pair_name"]
                        ].dropna().drop_duplicates("start_end_pair_id")
                        for row in names.itertuples(index=False):
                            od_names.setdefault(
                                str(row.start_end_pair_id),
                                str(row.start_end_pair_name),
                            )

                    if cleaned_writer is not None:
                        cleaned.to_csv(
                            cleaned_writer,
                            index=False,
                            header=first_cleaned,
                        )
                        first_cleaned = False
                    if station_writer is not None and not station.empty:
                        station.to_csv(
                            station_writer,
                            index=False,
                            header=first_station,
                        )
                        first_station = False
        finally:
            for handle in duration_handles.values():
                handle.close()
            if cleaned_writer is not None:
                cleaned_writer.close()
            if station_writer is not None:
                station_writer.close()

        duration_stats = {}
        for user, path in user_duration_files.items():
            values = pd.read_csv(path, header=None)[0].to_numpy(dtype="float64")
            duration_stats[user] = {
                "mean": float(values.mean()),
                "q25": float(np.quantile(values, 0.25)),
                "median": float(np.quantile(values, 0.50)),
                "q75": float(np.quantile(values, 0.75)),
                "p95": float(np.quantile(values, 0.95)),
            }

    return {
        "raw_rows": raw_rows,
        "cleaned_rows": cleaned_rows,
        "removed_rows": raw_rows - cleaned_rows,
        "station_od_rows": station_od_rows,
        "duplicate_ride_ids": len(duplicate_ids),
        "removal_counts": removal_counts,
        "monthly_quality": monthly_quality,
        "monthly_counts": monthly_counts,
        "weekday_hour_counts": weekday_hour_counts,
        "daytype_hour_counts": daytype_hour_counts,
        "user_counts": user_counts,
        "user_hour_counts": user_hour_counts,
        "user_daytype_counts": user_daytype_counts,
        "duration_stats": duration_stats,
        "station_departures": station_departures,
        "station_arrivals": station_arrivals,
        "station_metadata": station_metadata,
        "balance_departures": balance_departures,
        "balance_arrivals": balance_arrivals,
        "od_counts": od_counts,
        "od_duration_sum": od_duration_sum,
        "od_weekday_counts": od_weekday_counts,
        "od_names": od_names,
    }


def collect_top_od_durations(
    files: list[Path],
    duplicate_ids: set[str],
    top_keys: set[str],
) -> dict[str, list[float]]:
    """Second streaming pass for exact medians of only the Top 100 OD pairs."""

    durations: dict[str, list[float]] = defaultdict(list)
    seen_duplicate_ids: set[str] = set()
    for path in files:
        source_month = path.name[:6]
        for chunk in read_trip_file(path):
            cleaned, _reason = clean_chunk(
                chunk,
                seen_duplicate_ids=seen_duplicate_ids,
                duplicate_ids=duplicate_ids,
                source_file=path.name,
                source_month=source_month,
            )
            if cleaned.empty:
                continue
            selected = cleaned.loc[
                cleaned["station_pair_complete"]
                & cleaned["start_end_pair_id"].astype("string").isin(top_keys),
                ["start_end_pair_id", "duration_min"],
            ]
            for key, values in selected.groupby("start_end_pair_id")["duration_min"]:
                durations[str(key)].extend(values.astype(float).tolist())
    return durations


def write_outputs(summary: dict[str, object], output_dir: Path) -> None:
    """Write compact portfolio tables and quality audits."""

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_rows = int(summary["raw_rows"])
    cleaned_rows = int(summary["cleaned_rows"])
    station_od_rows = int(summary["station_od_rows"])

    monthly_counts: Counter = summary["monthly_counts"]  # type: ignore[assignment]
    pd.DataFrame(
        [
            {"month": int(month), "ride_count": int(count)}
            for month, count in sorted(monthly_counts.items())
        ]
    ).to_csv(output_dir / "monthly_rides.csv", index=False)

    weekday_hour_counts: Counter = summary["weekday_hour_counts"]  # type: ignore[assignment]
    pd.DataFrame(
        [
            {"weekday": int(key[0]), "hour": int(key[1]), "ride_count": int(count)}
            for key, count in weekday_hour_counts.items()
        ]
    ).sort_values(["weekday", "hour"]).to_csv(
        output_dir / "weekday_hour.csv", index=False
    )

    daytype_hour_counts: Counter = summary["daytype_hour_counts"]  # type: ignore[assignment]
    pd.DataFrame(
        [
            {
                "is_weekend": bool(key[0]),
                "hour": int(key[1]),
                "ride_count": int(count),
            }
            for key, count in daytype_hour_counts.items()
        ]
    ).sort_values(["is_weekend", "hour"]).to_csv(
        output_dir / "hour_by_daytype.csv", index=False
    )

    user_counts: Counter = summary["user_counts"]  # type: ignore[assignment]
    duration_stats: dict[str, dict[str, float]] = summary["duration_stats"]  # type: ignore[assignment]
    user_rows = []
    duration_rows = []
    for user, count in sorted(user_counts.items()):
        stats = duration_stats[str(user)]
        user_rows.append(
            {
                "member_casual": user,
                "ride_count": int(count),
                "ride_share": int(count) / cleaned_rows,
                "duration_mean_min": stats["mean"],
                "duration_median_min": stats["median"],
                "duration_p95_min": stats["p95"],
            }
        )
        duration_rows.append(
            {
                "member_casual": user,
                "ride_count": int(count),
                "duration_q25_min": stats["q25"],
                "duration_median_min": stats["median"],
                "duration_q75_min": stats["q75"],
                "duration_p95_min": stats["p95"],
                "duration_mean_min": stats["mean"],
            }
        )
    pd.DataFrame(user_rows).to_csv(output_dir / "user_type_summary.csv", index=False)
    pd.DataFrame(duration_rows).to_csv(
        output_dir / "duration_by_user_type.csv", index=False
    )

    user_hour_counts: Counter = summary["user_hour_counts"]  # type: ignore[assignment]
    user_hour_rows = []
    for hour in range(24):
        row: dict[str, float | int] = {"hour": hour}
        for user in sorted(user_counts):
            row[f"{user}_pct"] = (
                user_hour_counts.get((user, hour), 0) / user_counts[user] * 100
            )
        user_hour_rows.append(row)
    pd.DataFrame(user_hour_rows).to_csv(output_dir / "user_hour_share.csv", index=False)

    user_daytype_counts: Counter = summary["user_daytype_counts"]  # type: ignore[assignment]
    weekday_days, weekend_days = 261, 104
    daytype_rows = []
    for user in sorted(user_counts):
        weekday_daily = user_daytype_counts.get((user, False), 0) / weekday_days
        weekend_daily = user_daytype_counts.get((user, True), 0) / weekend_days
        daytype_rows.append(
            {
                "member_casual": user,
                "weekday_daily": weekday_daily,
                "weekend_daily": weekend_daily,
                "weekend_index": weekend_daily / weekday_daily,
            }
        )
    pd.DataFrame(daytype_rows).to_csv(
        output_dir / "user_daytype_daily.csv", index=False
    )

    station_departures: Counter = summary["station_departures"]  # type: ignore[assignment]
    station_arrivals: Counter = summary["station_arrivals"]  # type: ignore[assignment]
    metadata: dict[str, dict[str, object]] = summary["station_metadata"]  # type: ignore[assignment]
    station_rows = []
    for key in sorted(set(station_departures) | set(station_arrivals)):
        departures = int(station_departures.get(key, 0))
        arrivals = int(station_arrivals.get(key, 0))
        station_rows.append(
            {
                "station_key": key,
                "station_name": metadata.get(key, {}).get("station_name", pd.NA),
                "lat": metadata.get(key, {}).get("lat", np.nan),
                "lng": metadata.get(key, {}).get("lng", np.nan),
                "departures": departures,
                "arrivals": arrivals,
                "total_usage": departures + arrivals,
            }
        )
    pd.DataFrame(station_rows).sort_values(
        "total_usage", ascending=False
    ).to_csv(output_dir / "station_usage.csv", index=False)

    balance_departures: Counter = summary["balance_departures"]  # type: ignore[assignment]
    balance_arrivals: Counter = summary["balance_arrivals"]  # type: ignore[assignment]
    balance_rows = []
    for key in sorted(set(balance_departures) | set(balance_arrivals)):
        departures = int(balance_departures.get(key, 0))
        arrivals = int(balance_arrivals.get(key, 0))
        total = departures + arrivals
        balance_rows.append(
            {
                "station_key": key,
                "station_name": metadata.get(key, {}).get("station_name", pd.NA),
                "lat": metadata.get(key, {}).get("lat", np.nan),
                "lng": metadata.get(key, {}).get("lng", np.nan),
                "departures": departures,
                "arrivals": arrivals,
                "total_usage": total,
                "balance_gap": departures - arrivals,
                "imbalance_rate": (departures - arrivals) / total if total else 0,
            }
        )
    pd.DataFrame(balance_rows).sort_values(
        "balance_gap", ascending=False
    ).to_csv(output_dir / "station_balance.csv", index=False)

    od_counts: Counter = summary["od_counts"]  # type: ignore[assignment]
    od_duration_sum: Counter = summary["od_duration_sum"]  # type: ignore[assignment]
    od_durations: dict[str, list[float]] = summary["od_durations"]  # type: ignore[assignment]
    od_weekday_counts: Counter = summary["od_weekday_counts"]  # type: ignore[assignment]
    od_names: dict[str, str] = summary["od_names"]  # type: ignore[assignment]
    od_rows = []
    for rank, (key, count) in enumerate(od_counts.most_common(100), start=1):
        values = od_durations.get(str(key), [])
        od_rows.append(
            {
                "rank": rank,
                "ride_count": int(count),
                "median_duration": float(np.median(values)) if values else np.nan,
                "avg_duration": od_duration_sum[str(key)] / count,
                "weekday_share": od_weekday_counts.get(key, 0) / count * 100,
                "route": od_names.get(str(key), str(key)),
            }
        )
    pd.DataFrame(od_rows).to_csv(output_dir / "top_od_routes.csv", index=False)

    monthly_quality: dict[str, MonthlyQuality] = summary["monthly_quality"]  # type: ignore[assignment]
    quality_rows = []
    for month, quality in sorted(monthly_quality.items()):
        quality_rows.append(
            {
                "source_month": month,
                "raw_rows": quality.raw_rows,
                "cleaned_rows": quality.cleaned_rows,
                "removed_rows": quality.removed_rows,
                "station_pair_complete_rows": quality.station_pair_complete_rows,
                "station_pair_complete_rate": (
                    quality.station_pair_complete_rows / quality.cleaned_rows
                    if quality.cleaned_rows
                    else 0
                ),
                "duration_under_1_min_rows": quality.duration_under_1_min_rows,
            }
        )
    pd.DataFrame(quality_rows).to_csv(
        output_dir / "monthly_quality_summary.csv", index=False
    )

    removal_counts: Counter = summary["removal_counts"]  # type: ignore[assignment]
    pd.DataFrame(
        [
            {
                "removal_reason": reason,
                "rows": int(count),
                "share_of_raw_rows": int(count) / raw_rows,
            }
            for reason, count in sorted(removal_counts.items())
        ]
    ).to_csv(output_dir / "cleaning_removed_records_summary.csv", index=False)

    metrics = {
        "project_year": PROJECT_YEAR,
        "source_files": 12,
        "raw_rows": raw_rows,
        "cleaned_rows": cleaned_rows,
        "removed_rows": int(summary["removed_rows"]),
        "station_od_rows": station_od_rows,
        "unique_stations": len(station_rows),
        "unique_od_pairs": len(od_counts),
        "duplicate_ride_ids": int(summary["duplicate_ride_ids"]),
    }
    (output_dir / "pipeline_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )


def publish_outputs(staging_dir: Path, output_dir: Path) -> None:
    """Replace this pipeline's tables after the complete pipeline succeeds.

    Only same-named targets are replaced so independently generated aggregate
    products, such as the pricing-policy tables, are preserved.
    """

    owned_outputs = {
        "cleaning_removed_records_summary.csv",
        "duration_by_user_type.csv",
        "hour_by_daytype.csv",
        "monthly_quality_summary.csv",
        "monthly_rides.csv",
        "pipeline_metrics.json",
        "schema_check.csv",
        "station_balance.csv",
        "station_usage.csv",
        "top_od_routes.csv",
        "user_daytype_daily.csv",
        "user_hour_share.csv",
        "user_type_summary.csv",
        "weekday_hour.csv",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in owned_outputs:
        old = output_dir / name
        if old.exists() and not (staging_dir / name).exists():
            old.unlink()
    for path in staging_dir.iterdir():
        target = output_dir / path.name
        if target.exists():
            target.unlink()
        shutil.move(str(path), target)


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    files = discover_input_files(input_dir)

    with tempfile.TemporaryDirectory(
        prefix="cabi-output-", dir=output_dir.parent
    ) as temp_output:
        staging_dir = Path(temp_output)
        validate_schema(files, staging_dir)
        duplicate_ids = find_duplicate_ride_ids(files)
        summary = process_files(
            files,
            duplicate_ids,
            staging_dir,
            write_row_level=args.write_row_level,
        )
        od_counts: Counter = summary["od_counts"]  # type: ignore[assignment]
        top_keys = {str(key) for key, _count in od_counts.most_common(100)}
        summary["od_durations"] = collect_top_od_durations(
            files, duplicate_ids, top_keys
        )
        if summary["raw_rows"] != summary["cleaned_rows"] + summary["removed_rows"]:
            raise AssertionError("Raw rows must equal cleaned rows plus removed rows.")
        write_outputs(summary, staging_dir)
        publish_outputs(staging_dir, output_dir)

    print(f"Processed {summary['raw_rows']:,} raw rides from 12 files.")
    print(f"Retained {summary['cleaned_rows']:,} cleaned rides.")
    print(f"Station/OD subset: {summary['station_od_rows']:,} rides.")
    print(f"Aggregate outputs: {output_dir}")


if __name__ == "__main__":
    main()
