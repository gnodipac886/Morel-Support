"""Precipitation layer: Open-Meteo, NASA Daymet, NOAA ACIS/PRISM."""

from __future__ import annotations

import csv
import datetime
import io
import json
import urllib.request
import urllib.parse
import concurrent.futures

from global_land_mask import globe

from utils import fetch_json, haversine_miles
from layers.base import BaseLayer


def _parse_time_window(tw) -> int:
    """Parse '1w','2w','1m','2m' (or plain int) → number of days."""
    tw = str(tw).strip().lower()
    if tw.endswith("w"):
        try:
            return int(tw[:-1]) * 7
        except ValueError:
            pass
    if tw.endswith("m"):
        try:
            return int(tw[:-1]) * 30
        except ValueError:
            pass
    try:
        return int(tw)
    except ValueError:
        return 14


def _fetch_precip_pt(args):
    """Fetch one Open-Meteo sample point. Module-level so it's picklable."""
    lat, lon, start_iso, end_iso = args
    params = {
        "latitude":         round(lat, 3),
        "longitude":        round(lon, 3),
        "start_date":       start_iso,
        "end_date":         end_iso,
        "daily":            "precipitation_sum,snowfall_sum,soil_temperature_0_to_7cm_mean",
        "temperature_unit": "fahrenheit",
        "timezone":         "auto",
    }
    url  = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(params)
    data = fetch_json(url, timeout=8)
    if data and "daily" in data:
        daily   = data["daily"]
        precip  = [p or 0 for p in daily.get("precipitation_sum", [])]
        snow_cm = [s or 0 for s in daily.get("snowfall_sum", [])]
        temps   = [t for t in daily.get("soil_temperature_0_to_7cm_mean", []) if t is not None]
        return {
            "lat":         lat,
            "lon":         lon,
            "precip_in":   sum(precip) / 25.4,
            "snow_in":     sum(snow_cm) / 10 / 2.54,
            "soil_temp_f": sum(temps) / len(temps) if temps else None,
        }
    return None


def _fetch_daymet_pt(args):
    """Fetch one NASA Daymet point (total precip). Module-level so it's picklable."""
    lat, lon, start_iso, end_iso = args
    if not (14.0 <= lat <= 55.0 and -131.0 <= lon <= -53.0):
        return None
    if not globe.is_land(lat, lon):
        return None
    params = {
        "lat": round(lat, 4), "lon": round(lon, 4),
        "vars": "prcp", "start": start_iso, "end": end_iso,
    }
    url = "https://daymet.ornl.gov/single-pixel/api/data?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MorelSupport/1.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            text = r.read().decode("utf-8")
        lines = [l for l in text.splitlines() if not l.startswith("#") and l.strip()]
        if len(lines) < 2:
            return None
        reader    = csv.DictReader(lines)
        total_mm  = 0.0
        for row in reader:
            v = (row.get("prcp (mm/day)") or row.get("prcp") or "").strip()
            if v and v not in ("NA", ""):
                try:
                    total_mm += float(v)
                except ValueError:
                    pass
        if total_mm == 0:
            return None
        return {"lat": lat, "lon": lon, "precip_in": total_mm / 25.4, "source": "Daymet"}
    except Exception as e:
        print(f"[precip] Daymet {lat:.2f},{lon:.2f}: {e}")
        return None


def _fetch_acis_precip(bounds: dict, start_str: str, end_str: str) -> list:
    """Fetch PRISM precipitation from NOAA ACIS GridData."""
    url  = "http://data.rcc-acis.org/GridData"
    body = json.dumps({
        "bbox":  f"{bounds['west']:.3f},{bounds['south']:.3f},{bounds['east']:.3f},{bounds['north']:.3f}",
        "grid":  "21",
        "sdate": start_str,
        "edate": end_str,
        "elems": [{"name": "pcpn", "reduce": {"reduce": "sum"}}],
        "meta":  "ll",
    }).encode()
    try:
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", "User-Agent": "MorelSupport/1.0",
        })
        with urllib.request.urlopen(req, timeout=25) as r:
            resp = json.loads(r.read())
        pts     = []
        meta_ll = resp.get("meta", {}).get("ll", [])
        rows    = resp.get("data", [])
        if meta_ll and rows:
            n_pts  = len(meta_ll)
            totals = [0.0] * n_pts
            valid  = [False] * n_pts
            for row in rows:
                vals = row[1:] if len(row) > 1 else row
                for i, v in enumerate(vals):
                    if i >= n_pts:
                        break
                    if v not in ("M", "T", None, "") and v != -9999:
                        try:
                            totals[i] += float(v)
                            valid[i]   = True
                        except (TypeError, ValueError):
                            pass
            for i, ll in enumerate(meta_ll):
                if valid[i]:
                    try:
                        pts.append({"lat": float(ll[1]), "lon": float(ll[0]),
                                    "precip_in": totals[i], "source": "PRISM"})
                    except (TypeError, ValueError, IndexError):
                        pass
        print(f"[precip] ACIS/PRISM: {len(pts)} grid pts")
        return pts
    except Exception as e:
        print(f"[precip] ACIS: {e}")
        return []


class PrecipLayer(BaseLayer):
    name      = "precip"
    cache_ttl = 6 * 3600

    def fetch(self, bounds: dict, opts: dict, grid=None):
        cells = grid or []
        if not cells:
            return []
        time_window = opts.get("time_window", opts.get("days_back", "2w"))
        days_back   = _parse_time_window(time_window)
        end         = datetime.date.today() - datetime.timedelta(days=1)
        start       = end - datetime.timedelta(days=days_back)
        s_iso, e_iso = start.isoformat(), end.isoformat()

        lats   = [c["center"][0] for c in cells]
        lons   = [c["center"][1] for c in cells]
        bbox   = {"north": max(lats), "south": min(lats),
                  "east":  max(lons), "west":  min(lons)}
        n, s, e, w = bbox["north"], bbox["south"], bbox["east"], bbox["west"]

        sample_coords = [
            (s + (n-s)*fy, w + (e-w)*fx)
            for fy, fx in [(0.1,0.1),(0.1,0.5),(0.1,0.9),
                           (0.5,0.1),(0.5,0.5),(0.5,0.9),
                           (0.9,0.1),(0.9,0.5),(0.9,0.9)]
        ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            om_futs  = [pool.submit(_fetch_precip_pt,  (lat, lon, s_iso, e_iso)) for lat, lon in sample_coords]
            dm_futs  = [pool.submit(_fetch_daymet_pt,   (lat, lon, s_iso, e_iso)) for lat, lon in sample_coords]
            acis_fut = pool.submit(_fetch_acis_precip, bbox, s_iso, e_iso)
            om_res   = [f.result() for f in om_futs]
            dm_res   = [f.result() for f in dm_futs]
            acis_pts = acis_fut.result()

        sample_pts = []
        for i, (lat, lon) in enumerate(sample_coords):
            om, dm = om_res[i], dm_res[i]
            if om and dm:
                sample_pts.append({
                    "lat": lat, "lon": lon,
                    "precip_in":   (om["precip_in"] + dm["precip_in"]) / 2,
                    "snow_in":     om.get("snow_in", 0),
                    "soil_temp_f": om.get("soil_temp_f"),
                    "source": "Open-Meteo+Daymet",
                })
            elif om:
                sample_pts.append({**om, "source": "Open-Meteo"})
            elif dm:
                sample_pts.append({"lat": lat, "lon": lon, "precip_in": dm["precip_in"],
                                   "snow_in": 0, "soil_temp_f": None, "source": "Daymet"})

        all_ref = acis_pts + sample_pts
        avg_in  = sum(p["precip_in"] for p in sample_pts) / max(1, len(sample_pts))
        print(f"[precip] {len(sample_pts)}/9 pts · avg {avg_in:.2f} in · "
              f"ACIS {len(acis_pts)} pts · window={time_window}")

        results = []
        for cell in cells:
            lat, lon = cell["center"]
            ref = min(all_ref, key=lambda p: haversine_miles(lat, lon, p["lat"], p["lon"])) if all_ref else None
            results.append({
                "lat":         lat,
                "lon":         lon,
                "bounds":      cell["bounds"],
                "precip_in":   round(ref["precip_in"], 2)      if ref else 0,
                "snow_in":     round(ref.get("snow_in", 0), 2) if ref else 0,
                "soil_temp_f": ref.get("soil_temp_f")          if ref else None,
                "source":      ref.get("source", "")           if ref else "",
            })
        return results

    def to_geojson(self, data) -> list:
        features = []
        for pt in (data or []):
            b     = pt.get("bounds")
            props = {
                "precip_in":   round(pt.get("precip_in", 0), 2),
                "snow_in":     round(pt.get("snow_in", 0), 2),
                "soil_temp_f": pt.get("soil_temp_f"),
                "source":      pt.get("source", ""),
            }
            if b:
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [b["west"], b["south"]], [b["east"], b["south"]],
                            [b["east"], b["north"]], [b["west"], b["north"]],
                            [b["west"], b["south"]],
                        ]],
                    },
                    "properties": props,
                })
            else:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [pt["lon"], pt["lat"]]},
                    "properties": props,
                })
        return features


def score_precip(cell_lat, cell_lon, precip_pts, _opts):
    if not precip_pts:
        return 0.5, {}
    pt     = min(precip_pts, key=lambda p: haversine_miles(cell_lat, cell_lon, p["lat"], p["lon"]))
    inches = pt.get("precip_in", pt.get("precip_mm", 0) / 25.4)
    if   inches < 0.2: ps = 0.05
    elif inches < 0.5: ps = 0.30
    elif inches < 1.0: ps = 0.60
    elif inches < 2.0: ps = 0.85
    elif inches < 3.5: ps = 1.00
    elif inches < 6.0: ps = 0.75
    else:              ps = 0.45
    tf = pt.get("soil_temp_f")
    if   tf is None: ts = 0.5
    elif tf < 32:    ts = 0.0
    elif tf < 42:    ts = 0.20
    elif tf < 50:    ts = 0.75
    elif tf < 60:    ts = 1.00
    elif tf < 68:    ts = 0.65
    elif tf < 78:    ts = 0.25
    else:            ts = 0.05
    combined = ps * 0.55 + ts * 0.45
    return combined, {
        "Precipitation": f"{inches:.1f} in",
        "Soil Temp":     f"{tf:.0f}°F" if tf else "N/A",
    }
