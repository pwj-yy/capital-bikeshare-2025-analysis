"""Shared plotting helpers for the portfolio figures."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "aggregates"
FIGURE_DIR = Path(os.environ.get("CABI_FIGURE_DIR", ROOT / "figures"))
MPL_CACHE_DIR = Path(tempfile.gettempdir()) / "capital-bikeshare-matplotlib"
MPL_CACHE_DIR.mkdir(exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MPL_CACHE_DIR))

import matplotlib  # noqa: E402

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

COLORS = {
    "teal": "#079889",
    "orange": "#F28C28",
    "red": "#D9272E",
    "navy": "#183B56",
    "gold": "#F2B705",
    "blue": "#3E7CB1",
    "light": "#F5F7F6",
    "grid": "#D9E0DE",
    "text": "#232323",
    "muted": "#6B7471",
}


def setup_style() -> None:
    """Apply a consistent, GitHub-friendly visual style."""

    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": COLORS["grid"],
            "axes.labelcolor": COLORS["text"],
            "axes.titlecolor": COLORS["text"],
            "axes.titleweight": "bold",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": False,
            "font.family": "sans-serif",
            "font.sans-serif": [
                "Microsoft YaHei",
                "Noto Sans CJK SC",
                "Arial",
                "DejaVu Sans",
            ],
            "text.color": COLORS["text"],
            "xtick.color": COLORS["muted"],
            "ytick.color": COLORS["muted"],
            "legend.frameon": False,
            "savefig.facecolor": "white",
            "savefig.bbox": "tight",
        }
    )


def thousands(value: float, _position: int) -> str:
    """Format chart ticks as compact counts."""

    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.0f}K"
    return f"{value:.0f}"


COUNT_FORMATTER = FuncFormatter(thousands)


def save_figure(fig: plt.Figure, filename: str) -> Path:
    """Save a high-resolution PNG and close the Matplotlib figure."""

    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    output = FIGURE_DIR / filename
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    try:
        display_path = output.relative_to(ROOT)
    except ValueError:
        display_path = output
    print(f"Saved {display_path}")
    return output
