"""Create member/casual composition and usage-pattern figures."""

from __future__ import annotations

from common import COLORS, DATA_DIR, save_figure, setup_style

import matplotlib.pyplot as plt
import pandas as pd


def plot_user_mix() -> None:
    data = pd.read_csv(DATA_DIR / "user_type_summary.csv")
    data = data.set_index("member_casual").loc[["member", "casual"]].reset_index()
    shares = data["ride_share"] * 100

    fig, ax = plt.subplots(figsize=(10.5, 3.8))
    left = 0.0
    for row, share, color in zip(
        data.itertuples(index=False), shares, [COLORS["teal"], COLORS["orange"]]
    ):
        ax.barh([0], [share], left=left, color=color, height=0.42)
        ax.text(
            left + share / 2,
            0,
            f"{row.member_casual.title()}\n{share:.2f}%  |  {row.ride_count:,.0f} rides",
            ha="center",
            va="center",
            color="white",
            fontsize=12,
            fontweight="bold",
        )
        left += share
    ax.set_xlim(0, 100)
    ax.set_yticks([])
    ax.set_xlabel("Share of cleaned rides (%)")
    ax.set_title("Members generated 71.03% of 2025 rides", loc="left")
    ax.spines[["left", "bottom"]].set_visible(False)
    ax.tick_params(axis="x", bottom=False, labelbottom=False)
    fig.text(
        0.01,
        0.02,
        "Member rides form the core demand base; casual riders still account for nearly three in ten trips.",
        color=COLORS["muted"],
        fontsize=9,
    )
    fig.tight_layout()
    save_figure(fig, "03_user_mix.png")


def plot_user_hour_profile() -> None:
    data = pd.read_csv(DATA_DIR / "user_hour_share.csv")
    daytype = pd.read_csv(DATA_DIR / "user_daytype_daily.csv").set_index("member_casual")

    fig, ax = plt.subplots(figsize=(10.5, 5.4))
    ax.plot(
        data["hour"],
        data["member_pct"],
        color=COLORS["teal"],
        linewidth=3,
        marker="o",
        markersize=4,
        label=f"Member (weekend index {daytype.loc['member', 'weekend_index']:.3f})",
    )
    ax.plot(
        data["hour"],
        data["casual_pct"],
        color=COLORS["orange"],
        linewidth=3,
        marker="o",
        markersize=4,
        label=f"Casual (weekend index {daytype.loc['casual', 'weekend_index']:.3f})",
    )
    ax.set(
        title="Hourly profile within each user type",
        xlabel="Start hour",
        ylabel="Share of user-type rides (%)",
        xticks=range(0, 24, 2),
    )
    ax.grid(axis="y", color=COLORS["grid"], linewidth=0.8)
    ax.legend(loc="upper left")
    fig.text(
        0.01,
        -0.02,
        "Members are more commute-oriented; casual use is relatively stronger in daytime and on weekends.",
        color=COLORS["muted"],
        fontsize=9,
    )
    fig.tight_layout()
    save_figure(fig, "04_user_hour_profile.png")


def main() -> None:
    setup_style()
    plot_user_mix()
    plot_user_hour_profile()


if __name__ == "__main__":
    main()
