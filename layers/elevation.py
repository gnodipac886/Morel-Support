"""Elevation layer: OpenTopoData NED 10m batch lookup."""

from __future__ import annotations

import concurrent.futures
import urllib.parse

from utils import fetch_json
from layers.base import BaseLayer

_BATCH = 100   # OpenTopoData hard limit per request


def _fetch_elevation_batch(batch: list) -> dict:
    loc_str = "|".join(f"{lat},{lon}" for lat, lon in batch)
    url  = "https://api.opentopodata.org/v1/ned10m?locations=" + urllib.parse.quote(loc_str)
    data = fetch_json(url, timeout=20)
    out  = {}
    if data and "results" in data:
        for i, r in enumerate(data["results"]):
            if i < len(batch):
                lat, lon = batch[i]
                out[(round(lat, 6), round(lon, 6))] = r.get("elevation")
    return out


class ElevationLayer(BaseLayer):
    name      = "elevation"
    cache_ttl = 72 * 3600

    def fetch(self, bounds: dict, opts: dict, grid=None):
        cells = grid or []
        if not cells:
            return {}
        centers = [c["center"] for c in cells]
        batches = [centers[i:i+_BATCH] for i in range(0, len(centers), _BATCH)]
        out = {}
        # Batches are independent HTTP requests — parallelise them.
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(batches))) as pool:
            for result in pool.map(_fetch_elevation_batch, batches):
                out.update(result)
        print(f"[elevation] {len(out)} / {len(centers)} values ({len(batches)} batches)")
        return out

    def to_geojson(self, data) -> list:
        return [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"elevation_ft": round(elev * 3.281) if elev else None},
            }
            for (lat, lon), elev in (data or {}).items()
        ]


def score_elevation(cell_lat: float, cell_lon: float, elev_map: dict) -> float:
    key    = (round(cell_lat, 6), round(cell_lon, 6))
    elev_m = elev_map.get(key)
    if elev_m is None:
        return 0.5
    ft = elev_m * 3.281
    if   ft < 0:    return 0.10
    elif ft < 300:  return 0.30
    elif ft < 600:  return 0.65
    elif ft < 1500: return 1.00
    elif ft < 3000: return 0.90
    elif ft < 5000: return 0.55
    elif ft < 8000: return 0.25
    else:           return 0.10
