"""Create the hourly and weekday-hour temporal analysis figures."""

from __future__ import annotations

from common import COLORS, COUNT_FORMATTER, DATA_DIR, save_figure, setup_style

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def plot_hourly_daytype() -> None:
    data = pd.read_csv(DATA_DIR / "hour_by_daytype.csv")
    data["is_weekend"] = (
        data["is_weekend"].astype(str).str.lower().map({"true": True, "false": False})
    )
    weekday_days, weekend_days = 261, 104
    data["daily_average"] = data["ride_count"] / data["is_weekend"].map(
        {False: weekday_days, True: weekend_days}
    )

    weekday = data.loc[~data["is_weekend"]].sort_values("hour")
    weekend = data.loc[data["is_weekend"]].sort_values("hour")

    fig, ax = plt.subplots(figsize=(10.5, 5.4))
    ax.plot(
        weekday["hour"],
        weekday["daily_average"],
        color=COLORS["teal"],
        linewidth=3,
        marker="o",
        markersize=4,
        label="Weekday",
    )
    ax.plot(
        weekend["hour"],
        weekend["daily_average"],
        color=COLORS["orange"],
        linewidth=3,
        marker="o",
        markersize=4,
        label="Weekend",
    )
    ax.axvspan(7, 9, color=COLORS["teal"], alpha=0.08)
    ax.axvspan(16, 18, color=COLORS["teal"], alpha=0.08)
    ax.set(
        title="Average rides by hour: weekday vs weekend",
        xlabel="Start hour",
        ylabel="Average rides per day",
        xticks=range(0, 24, 2),
    )
    ax.yaxis.set_major_formatter(COUNT_FORMATTER)
    ax.grid(axis="y", color=COLORS["grid"], linewidth=0.8)
    ax.legend(loc="upper left", ncols=2)
    fig.text(
        0.01,
        -0.02,
        "Weekdays show commute peaks around 08:00 and 17:00; weekend demand shifts toward midday.",
        color=COLORS["muted"],
        fontsize=9,
    )
    fig.tight_layout()
    save_figure(fig, "01_hourly_weekday_weekend.png")


def plot_weekday_heatmap() -> None:
    data = pd.read_csv(DATA_DIR / "weekday_hour.csv")
    weekday_days = {1: 52, 2: 52, 3: 53, 4: 52, 5: 52, 6: 52, 7: 52}
    data["daily_average"] = data["ride_count"] / data["weekday"].map(weekday_days)
    matrix = data.pivot(index="weekday", columns="hour", values="daily_average").sort_index()
    matrix.index = WEEKDAY_LABELS

    fig, ax = plt.subplots(figsize=(11.2, 4.5))
    sns.heatmap(
        matrix,
        ax=ax,
        cmap=sns.diverging_palette(230, 15, as_cmap=True),
        center=matrix.to_numpy().mean(),
        linewidths=0.35,
        linecolor="white",
        cbar_kws={"label": "Average rides per day"},
    )
    ax.set(
        title="Weekday-hour demand heatmap",
        xlabel="Start hour",
        ylabel="Day of week",
    )
    ax.tick_params(axis="x", rotation=0)
    ax.tick_params(axis="y", rotation=0)
    fig.text(
        0.01,
        -0.03,
        "The weekday 17:00 band is the most persistent high-demand period; weekends are flatter and later.",
        color=COLORS["muted"],
        fontsize=9,
    )
    fig.tight_layout()
    save_figure(fig, "02_weekday_hour_heatmap.png")


def main() -> None:
    setup_style()
    plot_hourly_daytype()
    plot_weekday_heatmap()


if __name__ == "__main__":
    main()
