"""Create the high-frequency origin-destination route figure."""

from __future__ import annotations

from common import COLORS, COUNT_FORMATTER, DATA_DIR, save_figure, setup_style

import matplotlib.pyplot as plt
import pandas as pd


def shorten(label: str, length: int = 62) -> str:
    return label if len(label) <= length else f"{label[: length - 1]}…"


def plot_top_od_routes() -> None:
    data = pd.read_csv(DATA_DIR / "top_od_routes.csv").nlargest(10, "ride_count")
    data = data.sort_values("ride_count")
    same_station = data["route"].apply(is_same_station_route)
    colors = [COLORS["orange"] if value else COLORS["teal"] for value in same_station]

    fig, ax = plt.subplots(figsize=(11.5, 6.4))
    bars = ax.barh(
        [shorten(str(x)) for x in data["route"]],
        data["ride_count"],
        color=colors,
    )
    ax.bar_label(bars, labels=[f"{x:,.0f}" for x in data["ride_count"]], padding=4)
    ax.set(
        title="Top 10 origin-destination routes",
        xlabel="Ride count in complete station/OD subset",
        ylabel="",
    )
    ax.xaxis.set_major_formatter(COUNT_FORMATTER)
    ax.grid(axis="x", color=COLORS["grid"], linewidth=0.8)
    ax.set_xlim(0, data["ride_count"].max() * 1.18)
    ax.text(
        0.99,
        0.04,
        "Teal: point-to-point  |  Orange: same-station return",
        transform=ax.transAxes,
        ha="right",
        color=COLORS["muted"],
        fontsize=9,
    )
    fig.text(
        0.01,
        -0.02,
        "Frequent short Union Station links coexist with longer same-station leisure loops near visitor destinations.",
        color=COLORS["muted"],
        fontsize=9,
    )
    fig.tight_layout()
    save_figure(fig, "07_top_od_routes.png")


def is_same_station_route(route: str) -> bool:
    """Identify a same-station route from either display arrow convention."""

    for separator in (" → ", " -> "):
        parts = str(route).split(separator)
        if len(parts) == 2:
            return parts[0].strip() == parts[1].strip()
    return False


def main() -> None:
    setup_style()
    plot_top_od_routes()


if __name__ == "__main__":
    main()
