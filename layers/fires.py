"""Fire perimeters layer: NIFC WFIGS, USFS EDW, CAL FIRE/FRAP, NASA FIRMS."""

from __future__ import annotations

import csv
import datetime
import io
import math
import os
import urllib.request
import urllib.parse
import concurrent.futures

from utils import fetch_json, haversine_miles
from layers.base import BaseLayer


def _parse_esri_fire_features(data, source_name, name_fields, ts_fields, year_field, min_year):
    """Parse ESRI FeatureServer JSON → unified fire list."""
    fires = []
    if data is None:
        print(f"[fires] {source_name}: fetch returned None")
        return fires
    if "error" in data:
        print(f"[fires] {source_name}: API error: {data['error']}")
        return fires
    if "features" not in data:
        print(f"[fires] {source_name}: unexpected keys: {list(data.keys())}")
        return fires
    cur_year = datetime.datetime.now().year
    for feat in data["features"]:
        attrs = feat.get("attributes") or {}
        year  = cur_year
        if year_field and attrs.get(year_field) is not None:
            try:
                year = int(attrs[year_field])
            except (TypeError, ValueError):
                pass
        else:
            ts = next((attrs[f] for f in ts_fields if attrs.get(f)), None)
            if ts and isinstance(ts, (int, float)) and ts > 0:
                try:
                    year = datetime.datetime.utcfromtimestamp(ts / 1000).year
                except Exception:
                    pass
        if year < min_year:
            continue
        name      = next((attrs[f] for f in name_fields if attrs.get(f)), "")
        esri_geom = feat.get("geometry") or {}
        rings     = esri_geom.get("rings", [])
        if not rings:
            continue
        if len(rings) == 1:
            geojson_geom = {"type": "Polygon", "coordinates": rings}
        else:
            geojson_geom = {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}
        fires.append({
            "feature": {"geometry": geojson_geom, "properties": {"attr_IncidentName": name}},
            "year": year,
        })
    print(f"[fires] {source_name}: {len(fires)} features (min_year={min_year})")
    return fires


def _fetch_firms_fires(bounds: dict, min_year: int, map_key: str) -> list:
    """Fetch NASA FIRMS MODIS hotspot detections."""
    fires       = []
    days_needed = min(500, max(10, (datetime.date.today() - datetime.date(min_year, 1, 1)).days + 30))
    area = f"{bounds['west']:.3f},{bounds['south']:.3f},{bounds['east']:.3f},{bounds['north']:.3f}"
    url  = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{map_key}/MODIS_C6_1/{area}/{days_needed}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MorelSupport/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode("utf-8")
    except Exception as e:
        print(f"[fires] FIRMS: {e}")
        return fires
    try:
        for row in csv.DictReader(io.StringIO(text)):
            try:
                lat      = float(row["latitude"])
                lon      = float(row["longitude"])
                acq_date = row.get("acq_date", "")
                year     = int(acq_date[:4]) if len(acq_date) >= 4 else datetime.datetime.now().year
            except (ValueError, KeyError):
                continue
            if year < min_year:
                continue
            d    = 0.005
            ring = [[lon-d, lat-d], [lon+d, lat-d], [lon+d, lat+d], [lon-d, lat+d], [lon-d, lat-d]]
            fires.append({
                "feature": {
                    "geometry":   {"type": "Polygon", "coordinates": [ring]},
                    "properties": {"attr_IncidentName": f"FIRMS hotspot {year}"},
                },
                "year": year,
            })
    except Exception as e:
        print(f"[fires] FIRMS parse error: {e}")
    print(f"[fires] FIRMS: {len(fires)} hotspot detections")
    return fires


def _fetch_calfire_csv(bounds: dict, min_year: int) -> list:
    """Fetch CAL FIRE incident CSV; falls back to local mapdataall.csv."""
    url        = "https://incidents.fire.ca.gov/imapdata/mapdataall.csv"
    local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mapdataall.csv")
    text       = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MorelSupport/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode("utf-8", errors="replace")
        print("[fires] CAL FIRE CSV: fetched from live URL")
    except Exception as e:
        print(f"[fires] CAL FIRE CSV: live URL failed ({e}), trying local fallback")
        if os.path.exists(local_path):
            with open(local_path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            print(f"[fires] CAL FIRE CSV: loaded from {local_path}")
        else:
            print("[fires] CAL FIRE CSV: no local fallback found, skipping")
            return []

    n, s, e_b, w = bounds["north"], bounds["south"], bounds["east"], bounds["west"]
    fires = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            lat = float(row["incident_latitude"])
            lon = float(row["incident_longitude"])
        except (ValueError, TypeError, KeyError):
            continue
        if abs(lat) < 2 and abs(lon) < 2:
            continue
        if not (s <= lat <= n and w <= lon <= e_b):
            continue
        try:
            year = int((row.get("incident_dateonly_created") or "")[:4])
        except (ValueError, TypeError):
            continue
        if year < min_year:
            continue
        try:
            acres = float(row.get("incident_acres_burned") or 0)
        except (ValueError, TypeError):
            acres = 0
        name  = row.get("incident_name", "").strip()
        r_miles = max(0.3, min(30.0, math.sqrt(acres / (math.pi * 640)))) if acres > 0 else 0.3
        r_lat   = r_miles / 69.0
        r_lon   = r_miles / (69.0 * math.cos(math.radians(lat)))
        ring    = [
            [lon + r_lon * math.sin(math.radians(i * 45)),
             lat + r_lat * math.cos(math.radians(i * 45))]
            for i in range(9)
        ]
        fires.append({
            "feature": {
                "geometry":   {"type": "Polygon", "coordinates": [ring]},
                "properties": {"attr_IncidentName": name},
            },
            "year": year,
        })
    print(f"[fires] CAL FIRE CSV: {len(fires)} incidents in bbox (min_year={min_year})")
    return fires


class FiresLayer(BaseLayer):
    name      = "fires"
    cache_ttl = 12 * 3600

    def fetch(self, bounds: dict, opts: dict, grid=None):
        years_back          = int(opts.get("years_back", 3))
        ignore_current_year = bool(opts.get("ignore_current_year", False))
        cur_year  = datetime.datetime.now().year
        min_year  = cur_year - years_back
        geom = f"{bounds['west']},{bounds['south']},{bounds['east']},{bounds['north']}"
        esri_base = {
            "geometry":          geom,
            "geometryType":      "esriGeometryEnvelope",
            "inSR":              "4326",
            "outSR":             "4326",
            "spatialRel":        "esriSpatialRelIntersects",
            "resultRecordCount": 200,
            "returnGeometry":    "true",
            "f":                 "json",
        }

        tasks = []

        wfigs_name = ["poly_IncidentName", "attr_IncidentName", "IncidentName", "INCIDENTNAME"]
        wfigs_ts   = ["poly_PolygonDateTime", "poly_DateCurrent", "attr_FireDiscoveryDateTime",
                      "FireDiscoveryDateTime", "DISCOVERYDATETIME"]
        for svc in [
            "WFIGS_Interagency_Perimeters",
            "WFIGS_Interagency_Perimeters_YearToDate",
            "WFIGS_Interagency_Perimeters_Current",
        ]:
            url = (f"https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
                   f"{svc}/FeatureServer/0/query?" +
                   urllib.parse.urlencode({**esri_base, "outFields": "*", "where": "1=1"}))
            tasks.append((url, f"WFIGS/{svc}", wfigs_name, wfigs_ts, None))

        for usfs_svc, layer, name_fs, ts_fs, yr_f in [
            ("EDW_FireOccurrenceAndPerimeter_01", 0, ["FIRE_NAME"], [], "FIRE_YEAR"),
            ("EDW_MTBS_01",                       0, ["Fire_Name", "FIRE_NAME"], [], "Year"),
        ]:
            url = (f"https://apps.fs.usda.gov/arcx/rest/services/EDW/{usfs_svc}/MapServer/{layer}/query?" +
                   urllib.parse.urlencode({**esri_base, "outFields": "*", "where": "1=1"}))
            tasks.append((url, f"USFS/{usfs_svc}", name_fs, ts_fs, yr_f))

        ca_bbox = (bounds["east"] > -124.5 and bounds["west"] < -114.0
                   and bounds["north"] > 32.5 and bounds["south"] < 42.0)
        if ca_bbox:
            for yr in range(min_year, cur_year + 1):
                svc = (f"{yr}_California_Fire_Perimeters"
                       if yr == 2021 else f"{yr}_California_Fire_Perimeters_View")
                url = (f"https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/"
                       f"{svc}/FeatureServer/0/query?" +
                       urllib.parse.urlencode({**esri_base, "outFields": "*", "where": "1=1"}))
                tasks.append((url, f"CAL FIRE/{yr}", ["FIRE_NAME", "INCIDENTNAME"],
                              ["ALARM_DATE", "CONT_DATE"], "YEAR_"))

        def _fetch_task(task):
            url, label, name_fs, ts_fs, yr_f = task
            return label, yr_f, name_fs, ts_fs, fetch_json(url)

        fires = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as tpool:
            results = list(tpool.map(_fetch_task, tasks))

        for label, yr_f, name_fs, ts_fs, data in results:
            if label.startswith("CAL FIRE/") and data and \
                    data.get("error", {}).get("message") == "Invalid URL":
                break
            fires += _parse_esri_fire_features(data, label, name_fs, ts_fs, yr_f, min_year)

        firms_key = os.environ.get("FIRMS_MAP_KEY", "").strip()
        if firms_key:
            fires += _fetch_firms_fires(bounds, min_year, firms_key)
        else:
            print("[fires] FIRMS: skipped (set FIRMS_MAP_KEY env var to enable)")

        if ca_bbox:
            fires += _fetch_calfire_csv(bounds, min_year)

        if ignore_current_year:
            before = len(fires)
            fires  = [f for f in fires if f["year"] != cur_year]
            print(f"[fires] dropped {before - len(fires)} current-year features")

        seen, unique = set(), []
        for fire in fires:
            key = fire["feature"]["properties"]["attr_IncidentName"] + str(fire["year"])
            if key not in seen:
                seen.add(key)
                unique.append(fire)
        print(f"[fires] {len(unique)} perimeters after dedup (min_year={min_year})")
        return unique

    def to_geojson(self, data) -> list:
        features = []
        for fire in (data or []):
            inner = fire["feature"]
            props = dict(inner.get("properties", {}))
            props["year"] = fire["year"]
            features.append({
                "type":       "Feature",
                "geometry":   inner["geometry"],
                "properties": props,
            })
        return features


def score_fires(cell_lat, cell_lon, cell_bounds, fires, _opts):
    if not fires:
        return 0.0, 0
    now   = datetime.datetime.now().year
    best  = 0.0
    count = 0
    for fire in fires:
        years_ago = now - fire["year"]
        if   years_ago < 0:  age = 0.40
        elif years_ago == 0: age = 0.70
        elif years_ago == 1: age = 1.00
        elif years_ago == 2: age = 0.85
        elif years_ago == 3: age = 0.60
        elif years_ago == 4: age = 0.35
        else:                age = 0.15
        geom = fire["feature"].get("geometry", {})
        if not geom or not geom.get("coordinates"):
            continue
        try:
            coords = geom["coordinates"]
            if   geom["type"] == "Polygon":      flat = [c for ring in coords for c in ring]
            elif geom["type"] == "MultiPolygon": flat = [c for poly in coords for ring in poly for c in ring]
            else: continue
            if not flat:
                continue
            fs = min(c[1] for c in flat); fn = max(c[1] for c in flat)
            fw = min(c[0] for c in flat); fe = max(c[0] for c in flat)
            overlap = (cell_bounds["south"] < fn and cell_bounds["north"] > fs and
                       cell_bounds["west"]  < fe and cell_bounds["east"]  > fw)
            if overlap:
                score = age
                count += 1
            else:
                nearest_lat = max(fs, min(fn, cell_lat))
                nearest_lon = max(fw, min(fe, cell_lon))
                dist  = haversine_miles(cell_lat, cell_lon, nearest_lat, nearest_lon)
                score = age * max(0.0, 1.0 - dist / 20.0)
            best = max(best, score)
        except Exception:
            continue
    return min(1.0, best), count
