"""Regenerate all seven static portfolio figures."""

from __future__ import annotations

import od_analysis
import station_analysis
import temporal_analysis
import user_analysis


def main() -> None:
    temporal_analysis.main()
    user_analysis.main()
    station_analysis.main()
    od_analysis.main()


if __name__ == "__main__":
    main()

