"""Create station popularity and directional-balance figures."""

from __future__ import annotations

from common import COLORS, COUNT_FORMATTER, DATA_DIR, save_figure, setup_style

import matplotlib.pyplot as plt
import pandas as pd


def shorten(label: str, length: int = 42) -> str:
    return label if len(label) <= length else f"{label[: length - 1]}…"


def plot_top_stations() -> None:
    data = pd.read_csv(DATA_DIR / "station_usage.csv").nlargest(10, "total_usage")
    data = data.sort_values("total_usage")

    fig, ax = plt.subplots(figsize=(10.5, 6.2))
    bars = ax.barh(
        [shorten(str(x)) for x in data["station_name"]],
        data["total_usage"],
        color=COLORS["red"],
        alpha=0.9,
    )
    ax.bar_label(bars, labels=[f"{x:,.0f}" for x in data["total_usage"]], padding=5)
    ax.set(
        title="Top 10 stations by annual activity",
        xlabel="Departures + arrivals",
        ylabel="",
    )
    ax.xaxis.set_major_formatter(COUNT_FORMATTER)
    ax.grid(axis="x", color=COLORS["grid"], linewidth=0.8)
    ax.set_xlim(0, data["total_usage"].max() * 1.18)
    fig.text(
        0.01,
        -0.02,
        "Columbus Circle / Union Station leads the network, highlighting the role of transit hubs and central DC.",
        color=COLORS["muted"],
        fontsize=9,
    )
    fig.tight_layout()
    save_figure(fig, "05_top_stations.png")


def plot_station_balance() -> None:
    data = pd.read_csv(DATA_DIR / "station_balance.csv")
    eligible = data.loc[data["total_usage"] >= 10_000].copy()
    positive = eligible.nlargest(5, "balance_gap")
    negative = eligible.nsmallest(5, "balance_gap")
    selected = pd.concat([negative, positive]).sort_values("balance_gap")
    colors = [COLORS["orange"] if x < 0 else COLORS["teal"] for x in selected["balance_gap"]]

    fig, ax = plt.subplots(figsize=(10.5, 6.2))
    bars = ax.barh(
        [shorten(str(x)) for x in selected["station_name"]],
        selected["balance_gap"],
        color=colors,
    )
    ax.axvline(0, color=COLORS["text"], linewidth=0.9)
    for bar, value in zip(bars, selected["balance_gap"]):
        ax.text(
            bar.get_width() + (90 if value >= 0 else -90),
            bar.get_y() + bar.get_height() / 2,
            f"{value:+,.0f}",
            ha="left" if value >= 0 else "right",
            va="center",
        )
    ax.set(
        title="Station directional imbalance (departures - arrivals)",
        xlabel="Annual balance gap within complete station/OD records",
        ylabel="",
    )
    ax.xaxis.set_major_formatter(COUNT_FORMATTER)
    ax.grid(axis="x", color=COLORS["grid"], linewidth=0.8)
    gap_limit = max(selected["balance_gap"].abs()) * 1.18
    ax.set_xlim(-gap_limit, gap_limit)
    fig.text(
        0.01,
        -0.02,
        "Positive gaps flag potential bike-shortage monitoring; negative gaps flag possible dock-capacity pressure.",
        color=COLORS["muted"],
        fontsize=9,
    )
    fig.tight_layout()
    save_figure(fig, "06_station_balance.png")


def main() -> None:
    setup_style()
    plot_top_stations()
    plot_station_balance()


if __name__ == "__main__":
    main()
