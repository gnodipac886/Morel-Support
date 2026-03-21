"""iNaturalist morel sightings layer."""

from __future__ import annotations

import datetime
import urllib.parse

from utils import fetch_json, haversine_miles
from layers.base import BaseLayer


class InatLayer(BaseLayer):
    name      = "inat"
    cache_ttl = 24 * 3600

    def fetch(self, bounds: dict, opts: dict, grid=None):
        quality = opts.get("quality", "research,needs_id")
        params = {
            "taxon_name":    "Morchella",
            "nelat":         round(bounds["north"], 4),
            "nelng":         round(bounds["east"],  4),
            "swlat":         round(bounds["south"], 4),
            "swlng":         round(bounds["west"],  4),
            "per_page":      200,
            "quality_grade": quality,
            "order_by":      "observed_on",
            "order":         "desc",
        }
        url  = "https://api.inaturalist.org/v1/observations?" + urllib.parse.urlencode(params)
        data = fetch_json(url)
        obs  = [o for o in (data.get("results", []) if data else [])
                if not o.get("obscured") and not o.get("geoprivacy")]
        print(f"[iNat] {len(obs)} observations (non-obscured)")
        return obs

    def to_geojson(self, data) -> list:
        today    = datetime.date.today()
        features = []
        for obs in (data or []):
            loc = obs.get("location", "")
            if not loc:
                continue
            parts = loc.split(",")
            if len(parts) < 2:
                continue
            try:
                lat, lon = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            obs_date  = obs.get("observed_on", "")
            years_ago = 0
            if obs_date:
                try:
                    years_ago = today.year - datetime.date.fromisoformat(obs_date).year
                except Exception:
                    pass
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "observed_on":   obs_date,
                    "taxon_name":    (obs.get("taxon") or {}).get("name", "Morchella"),
                    "quality_grade": obs.get("quality_grade", ""),
                    "user":          (obs.get("user") or {}).get("login", ""),
                    "uri":           obs.get("uri", ""),
                    "years_ago":     years_ago,
                },
            })
        return features


def score_inat(cell_lat, cell_lon, obs_list, opts, target_date):
    if not obs_list:
        return 0.0, 0
    target_doy = target_date.timetuple().tm_yday
    max_dist   = 25.0
    best  = 0.0
    count = 0
    for obs in obs_list:
        try:
            loc = obs.get("location", "")
            if not loc:
                continue
            parts   = loc.split(",")
            obs_lat = float(parts[0])
            obs_lon = float(parts[1])
            dist    = haversine_miles(cell_lat, cell_lon, obs_lat, obs_lon)
            if dist > max_dist:
                continue
            count += 1
            dist_score = max(0.0, 1.0 - (dist / max_dist) ** 0.7)
            time_score = 0.5
            obs_date_str = obs.get("observed_on", "")
            if obs_date_str and opts.get("seasonal_weight", True):
                d        = datetime.date.fromisoformat(obs_date_str)
                doy      = d.timetuple().tm_yday
                doy_diff = abs(target_doy - doy)
                if doy_diff > 183:
                    doy_diff = 366 - doy_diff
                time_score  = max(0.05, 1.0 - doy_diff / 45.0)
                years_ago   = target_date.year - d.year
                time_score *= max(0.25, 1.0 - years_ago * 0.08)
            best = max(best, dist_score * time_score)
        except Exception:
            continue
    density_bonus = min(0.15, count * 0.015)
    return min(1.0, best + density_bonus), count
