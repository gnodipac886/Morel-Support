"""Soil properties layer: SoilGrids ISRIC (pH, clay texture)."""

from __future__ import annotations

import urllib.parse

from utils import fetch_json, haversine_miles
from layers.base import BaseLayer


class SoilLayer(BaseLayer):
    name      = "soil"
    cache_ttl = 72 * 3600

    def fetch(self, bounds: dict, opts: dict, grid=None):
        n, s, e, w = bounds["north"], bounds["south"], bounds["east"], bounds["west"]
        pts = [
            ((n + s) / 2,          (e + w) / 2),
            (s + (n-s)*0.25, w + (e-w)*0.25),
            (s + (n-s)*0.75, w + (e-w)*0.75),
        ]
        results = []
        for lat, lon in pts:
            params = {
                "lon":      round(lon, 4),
                "lat":      round(lat, 4),
                "property": "phh2o,clay,ocd",
                "depth":    "5-15cm",
                "value":    "mean",
            }
            url  = "https://rest.isric.org/soilgrids/v2.0/properties/query?" + urllib.parse.urlencode(params)
            data = fetch_json(url)
            if data and "properties" in data:
                props  = data["properties"]
                layers = {p["name"]: p for p in props.get("layers", [])}
                ph_val   = None
                clay_val = None
                if "phh2o" in layers:
                    for depth in layers["phh2o"].get("depths", []):
                        if depth.get("label") == "5-15cm":
                            ph_val = depth["values"].get("mean")
                            if ph_val:
                                ph_val /= 10   # stored as pH*10
                if "clay" in layers:
                    for depth in layers["clay"].get("depths", []):
                        if depth.get("label") == "5-15cm":
                            clay_val = depth["values"].get("mean")
                results.append({"lat": lat, "lon": lon, "ph": ph_val, "clay": clay_val})
        print(f"[soil] {len(results)} sample points")
        return results

    def to_geojson(self, data) -> list:
        return [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [pt["lon"], pt["lat"]]},
                "properties": {"ph": pt.get("ph"), "clay": pt.get("clay")},
            }
            for pt in (data or [])
        ]


def score_soil(cell_lat: float, cell_lon: float, soil_pts: list) -> float:
    if not soil_pts:
        return 0.5
    pt   = min(soil_pts, key=lambda p: haversine_miles(cell_lat, cell_lon, p["lat"], p["lon"]))
    ph   = pt.get("ph")
    clay = pt.get("clay")    # g/kg
    ph_score = 0.5
    if ph is not None:
        if   ph < 4.5: ph_score = 0.10
        elif ph < 5.5: ph_score = 0.35
        elif ph < 6.0: ph_score = 0.65
        elif ph < 7.0: ph_score = 1.00
        elif ph < 7.5: ph_score = 0.85
        elif ph < 8.0: ph_score = 0.50
        else:          ph_score = 0.20
    clay_score = 0.5
    if clay is not None:
        if   clay < 50:  clay_score = 0.60
        elif clay < 200: clay_score = 1.00
        elif clay < 350: clay_score = 0.70
        else:            clay_score = 0.30
    return ph_score * 0.6 + clay_score * 0.4
