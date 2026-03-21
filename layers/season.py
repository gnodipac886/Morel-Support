"""Seasonality layer: score based on lat/lon and target date only."""

from __future__ import annotations

from layers.base import BaseLayer


class SeasonLayer(BaseLayer):
    """No network fetch — score is computed purely from location + date."""

    name      = "season"
    cache_ttl = 24 * 3600   # not really used (no fetch)

    def fetch(self, bounds: dict, opts: dict, grid=None):
        return None   # season has no external data to fetch

    def to_geojson(self, data) -> list:
        return []


def score_seasonality(lat: float, _lon: float, target_date) -> float:
    """Estimate probability based on target date vs. typical morel window."""
    doy      = target_date.timetuple().tm_yday
    peak_doy = 70 + (lat - 30) * 2.5   # ~DOY 70 at 30°N, ~120 at 50°N
    window   = 25
    diff     = abs(doy - peak_doy)
    if diff < window:
        return max(0.30, 1.0 - (diff / window) ** 1.5)
    elif diff < 55:
        return max(0.05, 0.40 * (1.0 - (diff - window) / 30.0))
    else:
        return 0.05
