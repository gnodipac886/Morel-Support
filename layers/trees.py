"""Host trees layer: GBIF, LANDFIRE EVT, USFS TreeMap, NLCD."""

from __future__ import annotations

import threading
import urllib.parse
import concurrent.futures

from global_land_mask import globe

from utils import fetch_json, haversine_miles
from layers.base import BaseLayer


# ── EVT keyword → species mapping ─────────────────────────────────────────────
_EVT_KEYWORDS = {
    "elm":          ["elm"],
    "ash":          ["ash"],
    "oak":          ["oak"],
    "tulip_poplar": ["tulip", "yellow-poplar", "yellow poplar"],
    "cottonwood":   ["cottonwood", "populus"],
    "hickory":      ["hickory"],
    "maple":        ["maple"],
    "walnut":       ["walnut"],
    "sycamore":     ["sycamore"],
    "douglas_fir":  ["douglas-fir", "douglas fir", "pseudotsuga"],
    "pine":         ["pine", "pinus"],
    "white_fir":    ["white fir", "abies concolor", "grand fir", "abies"],
}

# Species whose presence inside a fire perimeter triggers a probability boost.
BURN_SPECIES = frozenset({"douglas_fir", "pine", "white_fir"})

# NLCD class → species mapping (MRLC classes)
_NLCD_SPECIES = {
    41: "deciduous",
    43: "deciduous",
    90: "cottonwood",
    95: "deciduous",
}


def _evt_name_to_species(evt_name: str) -> str | None:
    low = evt_name.lower()
    for sp, kws in _EVT_KEYWORDS.items():
        if any(kw in low for kw in kws):
            return sp
    if any(kw in low for kw in ["deciduous", "hardwood", "forest"]):
        return "deciduous"
    return None


# ── LANDFIRE lazy service discovery ───────────────────────────────────────────
_lf_lock         = threading.Lock()
_lf_service_url  = None
_lf_service_type = None
_lf_probed       = False

_LF_ROOTS = [
    "https://lfps.usgs.gov/arcgis/rest/services",
    "https://edcintl.cr.usgs.gov/arcgis/rest/services",
    "https://edcintl.cr.usgs.gov/arcgis/rest/services/Landfire",
    "https://www.landfire.gov/arcgis/rest/services",
]


def _discover_landfire():
    global _lf_service_url, _lf_service_type, _lf_probed
    with _lf_lock:
        if _lf_probed:
            return
        _lf_probed = True
        for root in _LF_ROOTS:
            d = fetch_json(root + "?f=json", timeout=10)
            if not d:
                continue
            all_svcs = list(d.get("services", []))
            for folder in d.get("folders", []):
                fd = fetch_json(f"{root}/{folder}?f=json", timeout=10)
                if fd:
                    all_svcs.extend(fd.get("services", []))
            for svc in all_svcs:
                name  = svc.get("name", "")
                stype = svc.get("type", "")
                if "EVT" not in name.upper():
                    continue
                if stype == "ImageServer":
                    _lf_service_url  = f"{root}/{name}/ImageServer/identify"
                    _lf_service_type = "identify"
                    print(f"[trees] LANDFIRE discovered: {_lf_service_url}")
                    return
                if stype == "MapServer":
                    _lf_service_url  = f"{root}/{name}/MapServer/0/query"
                    _lf_service_type = "query"
                    print(f"[trees] LANDFIRE discovered: {_lf_service_url}")
                    return
        print("[trees] LANDFIRE: no EVT service found in catalogue — skipping")


def _fetch_landfire_evt_pt(args):
    """Query LANDFIRE EVT at a single lat/lon point. Module-level for pickling."""
    lat, lon = args
    if not globe.is_land(lat, lon):
        return None
    _discover_landfire()
    if not _lf_service_url:
        return None

    if _lf_service_type == "identify":
        params = {
            "geometry":       f"{lon},{lat}",
            "geometryType":   "esriGeometryPoint",
            "inSR":           "4326",
            "returnGeometry": "false",
            "f":              "json",
        }
        data = fetch_json(_lf_service_url + "?" + urllib.parse.urlencode(params), timeout=10)
        if not data:
            return None
        evt_name = (
            (data.get("properties") or {}).get("EVT_NAME")
            or (data.get("attributes") or {}).get("EVT_NAME")
            or str(data.get("value") or "")
        )
    else:
        params = {
            "geometry":       f"{lon},{lat}",
            "geometryType":   "esriGeometryPoint",
            "inSR":           "4326",
            "spatialRel":     "esriSpatialRelIntersects",
            "where":          "1=1",
            "outFields":      "EVT_NAME,EVT_PHYS",
            "returnGeometry": "false",
            "f":              "json",
        }
        data = fetch_json(_lf_service_url + "?" + urllib.parse.urlencode(params), timeout=10)
        if not data or "features" not in data:
            return None
        evt_name = ""
        for feat in data["features"]:
            evt_name = (feat.get("attributes") or {}).get("EVT_NAME", "") or ""
            if evt_name:
                break

    sp = _evt_name_to_species(str(evt_name))
    if sp:
        return {"lat": lat, "lon": lon, "species": sp, "evt_name": evt_name, "source": "LANDFIRE"}
    return None


def _fetch_landfire_trees_bbox(bounds: dict, selected: list) -> list:
    _discover_landfire()
    if not _lf_service_url:
        return []
    n, s, e, w = bounds["north"], bounds["south"], bounds["east"], bounds["west"]
    steps = 4
    pts   = [
        (s + (n - s) * (fi + 0.5) / steps, w + (e - w) * (fj + 0.5) / steps)
        for fi in range(steps) for fj in range(steps)
    ]
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for r in pool.map(_fetch_landfire_evt_pt, pts):
            if r and (r["species"] in selected or r["species"] == "deciduous"):
                results.append(r)
    print(f"[trees] LANDFIRE: {len(results)} EVT matches")
    return results


def _fetch_usfs_treemap_bbox(bounds: dict) -> list:
    n, s, e, w = bounds["north"], bounds["south"], bounds["east"], bounds["west"]
    envelope   = f"{w},{s},{e},{n}"
    services   = [
        "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_ForestCoverType_01/MapServer/0/query",
        "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_ForestCoverType_01/MapServer/1/query",
        "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_ForestAttributes_01/MapServer/0/query",
        "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_TreeMap2016_01/MapServer/0/query",
        "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_TreeMap2016_01/FeatureServer/0/query",
    ]
    params = {
        "geometry":          envelope,
        "geometryType":      "esriGeometryEnvelope",
        "inSR":              "4326",
        "outSR":             "4326",
        "spatialRel":        "esriSpatialRelIntersects",
        "where":             "1=1",
        "outFields":         "*",
        "resultRecordCount": 50,
        "returnGeometry":    "true",
        "f":                 "json",
    }
    for url_base in services:
        data = fetch_json(url_base + "?" + urllib.parse.urlencode(params), timeout=12)
        if not data or "error" in data:
            continue
        feats = data.get("features", [])
        if not feats:
            continue
        results = []
        for feat in feats:
            attrs = feat.get("attributes") or {}
            geom  = feat.get("geometry") or {}
            lat = lon = None
            if "x" in geom and "y" in geom:
                lon, lat = geom["x"], geom["y"]
            elif "rings" in geom:
                ring = geom["rings"][0]
                xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
                if xs and ys:
                    lon, lat = sum(xs) / len(xs), sum(ys) / len(ys)
            if lat is None or lon is None:
                continue
            sp = None
            for v in attrs.values():
                if isinstance(v, str) and v:
                    sp = _evt_name_to_species(v)
                    if sp:
                        break
            results.append({"lat": lat, "lon": lon, "species": sp or "deciduous", "source": "USFS_TreeMap"})
        if results:
            print(f"[trees] USFS TreeMap ({url_base.split('/')[-4]}): {len(results)} features")
            return results
    print("[trees] USFS TreeMap: no results")
    return []


def _fetch_nlcd_pt(args):
    """Get NLCD land-cover class at a point. Module-level for pickling."""
    lat, lon = args
    if not globe.is_land(lat, lon):
        return None
    params = {
        "geometry":       f"{lon},{lat}",
        "geometryType":   "esriGeometryPoint",
        "inSR":           "4326",
        "returnGeometry": "false",
        "f":              "json",
    }
    url  = ("https://landscape2.arcgis.com/arcgis/rest/services/USA_NLCD_2019/"
            "ImageServer/identify?" + urllib.parse.urlencode(params))
    data = fetch_json(url, timeout=10)
    if not data:
        return None
    raw = (data.get("value")
           or (data.get("properties") or {}).get("Values", [None])[0])
    try:
        val = int(float(str(raw)))
    except (TypeError, ValueError):
        return None
    sp = _NLCD_SPECIES.get(val)
    if sp:
        return {"lat": lat, "lon": lon, "species": sp, "nlcd_class": val, "source": "NLCD"}
    return None


def _fetch_nlcd_bbox(bounds: dict) -> list:
    n, s, e, w = bounds["north"], bounds["south"], bounds["east"], bounds["west"]
    steps  = 4
    pts    = [
        (s + (n - s) * (fi + 0.5) / steps, w + (e - w) * (fj + 0.5) / steps)
        for fi in range(steps) for fj in range(steps)
    ]
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for r in pool.map(_fetch_nlcd_pt, pts):
            if r:
                results.append(r)
    print(f"[trees] NLCD: {len(results)} forest pts")
    return results


class TreesLayer(BaseLayer):
    name      = "trees"
    cache_ttl = 48 * 3600

    _TAXON_MAP = {
        "elm":          6265,
        "ash":          3189866,
        "oak":          2877951,
        "tulip_poplar": 3190081,
        "cottonwood":   3190165,
        "douglas_fir":  5284895,
        "pine":         2684241,
        "white_fir":    2684222,
    }

    def fetch(self, bounds: dict, opts: dict, grid=None):
        selected = opts.get("species", ["douglas_fir", "pine", "white_fir",
                                        "oak", "cottonwood", "sycamore", "ash"])

        def _gbif():
            res = []
            for sp in selected:
                if sp not in self._TAXON_MAP:
                    continue
                params = {
                    "taxonKey":         self._TAXON_MAP[sp],
                    "decimalLatitude":  f"{bounds['south']},{bounds['north']}",
                    "decimalLongitude": f"{bounds['west']},{bounds['east']}",
                    "limit":            100,
                    "hasCoordinate":    "true",
                    "occurrenceStatus": "PRESENT",
                }
                url  = "https://api.gbif.org/v1/occurrence/search?" + urllib.parse.urlencode(params)
                data = fetch_json(url)
                if data:
                    for obs in data.get("results", []):
                        lat = obs.get("decimalLatitude")
                        lon = obs.get("decimalLongitude")
                        if lat and lon:
                            res.append({"lat": lat, "lon": lon, "species": sp, "source": "GBIF"})
            return res

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            gbif_fut    = pool.submit(_gbif)
            lf_fut      = pool.submit(_fetch_landfire_trees_bbox, bounds, selected)
            treemap_fut = pool.submit(_fetch_usfs_treemap_bbox, bounds)
            nlcd_fut    = pool.submit(_fetch_nlcd_bbox, bounds)

            results  = gbif_fut.result()
            results += lf_fut.result()
            results += treemap_fut.result()
            results += nlcd_fut.result()

        print(f"[trees] {len(results)} occurrences (GBIF + LANDFIRE + TreeMap + NLCD)")
        return results

    def to_geojson(self, data) -> list:
        return [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [obs["lon"], obs["lat"]]},
                "properties": {"species": obs.get("species", "")},
            }
            for obs in (data or [])
        ]


def score_trees(cell_lat, cell_lon, tree_obs, _opts):
    if not tree_obs:
        return 0.5, 0
    sp_weights = {
        "ash":          0.90,
        "elm":          0.85,
        "tulip_poplar": 0.80,
        "cottonwood":   0.70,
        "oak":          0.65,
        "sycamore":     0.65,
        "walnut":       0.60,
        "hickory":      0.60,
        "maple":        0.55,
        "deciduous":    0.40,
        "douglas_fir":  0.70,
        "white_fir":    0.65,
        "pine":         0.60,
    }
    max_dist = 8.0
    best  = 0.0
    count = 0
    for obs in tree_obs:
        dist = haversine_miles(cell_lat, cell_lon, obs["lat"], obs["lon"])
        if dist > max_dist:
            continue
        count += 1
        w     = sp_weights.get(obs.get("species", ""), 0.40)
        score = w * max(0.0, 1.0 - (dist / max_dist) ** 0.8)
        best  = max(best, score)
    return min(1.0, best), count
