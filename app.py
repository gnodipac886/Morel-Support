"""Flask application — serves the map UI and /api/calculate endpoint."""

from __future__ import annotations

import concurrent.futures
import datetime
import multiprocessing

from flask import Flask, jsonify, render_template, request

from cache   import cache_get, cache_set, payload_hash, results_cache_get, results_cache_set
from config  import CPU_COUNT
from grid    import create_grid
from layers  import LAYERS
from scoring import _score_cell_worker
from urban   import fetch_urban_place_nodes
from utils   import haversine_miles

app = Flask(__name__)


# ── Raw GeoJSON conversion ─────────────────────────────────────────────────────

def _build_raw_geojson(layer_name: str, data) -> list:
    """Convert raw fetched layer data to GeoJSON features for single-layer mode."""
    layer = LAYERS.get(layer_name)
    if layer:
        return layer.to_geojson(data)
    return []


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/calculate", methods=["POST"])
def calculate():
    body            = request.get_json()
    bounds          = body["bounds"]
    resolution      = max(1.0, min(20.0, float(body.get("resolution", 5))))
    layers          = body.get("layers", {})
    weights         = body.get("weights", {})
    lookahead_weeks = max(0, int(body.get("lookahead_weeks", 0)))
    skip_urban      = bool(body.get("skip_urban", True))
    urban_scale     = max(0.1, min(4.0, float(body.get("urban_scale", 1.4))))
    target_date     = datetime.date.today() + datetime.timedelta(weeks=lookahead_weeks)

    # ── Results cache ──────────────────────────────────────────────────────────
    phash         = payload_hash(bounds, resolution, layers, weights,
                                 lookahead_weeks, skip_urban, urban_scale)
    cached_result = results_cache_get(phash, bounds)
    if cached_result is not None:
        return jsonify(cached_result)

    grid = create_grid(bounds, resolution)
    if not grid:
        return jsonify({"type": "FeatureCollection", "features": [], "meta": {}})

    # ── Urban filter ───────────────────────────────────────────────────────────
    urban_filtered = 0
    place_nodes    = []
    if skip_urban and grid:
        cached_places = cache_get("urban", bounds, {"resolution": 0}, ttl=72 * 3600)
        if cached_places is not None:
            place_nodes = cached_places
        else:
            place_nodes = fetch_urban_place_nodes(bounds)
            if place_nodes is not None:
                cache_set("urban", bounds, {"resolution": 0}, place_nodes)

        if place_nodes:
            before = len(grid)

            def _cell_is_urban(cell):
                clat, clon = cell["center"]
                for p in place_nodes:
                    if haversine_miles(clat, clon, p["lat"], p["lon"]) <= p["radius_miles"] * urban_scale:
                        return True
                return False

            grid           = [c for c in grid if not _cell_is_urban(c)]
            urban_filtered = before - len(grid)
            print(f"[urban] OSM: removed {urban_filtered} urban cells ({len(grid)} remain)")

    # ── Per-layer fetch (with cache) ───────────────────────────────────────────
    layer_data = {}
    FETCHERS   = {k: v for k, v in LAYERS.items()}   # inat, precip, fires, trees, elevation, soil

    futures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(FETCHERS)) as tpool:
        for k, layer_obj in FETCHERS.items():
            if not layers.get(k, {}).get("enabled"):
                continue
            cached = cache_get(k, bounds, layers[k], ttl=layer_obj.cache_ttl)
            if cached is not None:
                layer_data[k] = cached
            else:
                # grid-based layers receive the grid; bounds-based receive bounds
                if k in ("precip", "elevation"):
                    futures[k] = tpool.submit(layer_obj.fetch, bounds, layers[k], grid)
                else:
                    futures[k] = tpool.submit(layer_obj.fetch, bounds, layers[k])

        for k, f in futures.items():
            try:
                data = f.result(timeout=90)
                layer_data[k] = data
                cache_set(k, bounds, layers[k], data)
            except Exception as e:
                print(f"[{k}] error: {e}")
                layer_data[k] = {} if k == "elevation" else []

    # ── Single-layer raw mode ──────────────────────────────────────────────────
    enabled_data_layers = [k for k in FETCHERS if layers.get(k, {}).get("enabled")]
    if len(enabled_data_layers) == 1:
        layer_name   = enabled_data_layers[0]
        raw_features = _build_raw_geojson(layer_name, layer_data.get(layer_name))
        print(f"[raw] {layer_name} → {len(raw_features)} features")
        result = {
            "type":     "FeatureCollection",
            "features": raw_features,
            "meta":     {"raw_layer": layer_name, "count": len(raw_features),
                         "target_date": target_date.isoformat()},
        }
        results_cache_set(phash, bounds, result)
        return jsonify(result)

    # ── Multi-layer scoring via process pool ───────────────────────────────────
    ctx         = multiprocessing.get_context("spawn")
    worker_args = [(cell, layer_data, layers, weights, target_date) for cell in grid]
    chunksize   = max(1, len(grid) // (CPU_COUNT * 4))
    with concurrent.futures.ProcessPoolExecutor(max_workers=CPU_COUNT, mp_context=ctx) as pool:
        features = list(pool.map(_score_cell_worker, worker_args, chunksize=chunksize))

    total_obs = sum(1 for f in features if f["properties"]["probability"] >= 30)
    print(f"[calc] {len(features)} cells, {total_obs} cells ≥30%")

    active_places = place_nodes if (skip_urban and place_nodes) else []

    def _urban_point(lat, lon):
        return any(
            haversine_miles(lat, lon, p["lat"], p["lon"]) <= p["radius_miles"] * urban_scale
            for p in active_places
        )

    def _poly_centroid(coords):
        ring = coords[0]
        lon  = sum(pt[0] for pt in ring) / len(ring)
        lat  = sum(pt[1] for pt in ring) / len(ring)
        return lat, lon

    overlays = {}
    for ov_key in ("inat", "fires"):
        if not (layers.get(ov_key, {}).get("enabled") and ov_key in layer_data):
            continue
        raw = _build_raw_geojson(ov_key, layer_data[ov_key])
        if not active_places:
            overlays[ov_key] = raw
            continue
        filtered = []
        for feat in raw:
            geom   = feat.get("geometry", {})
            gtype  = geom.get("type", "")
            coords = geom.get("coordinates", [])
            if gtype == "Point":
                lon, lat = coords[0], coords[1]
            elif gtype == "Polygon":
                lat, lon = _poly_centroid(coords)
            elif gtype == "MultiPolygon":
                lat, lon = _poly_centroid(coords[0])
            else:
                filtered.append(feat)
                continue
            if ov_key == "inat":
                is_urban = any(
                    haversine_miles(lat, lon, p["lat"], p["lon"]) <= min(p["radius_miles"] * urban_scale, 5.0)
                    for p in active_places
                )
            else:
                is_urban = _urban_point(lat, lon)
            if not is_urban:
                filtered.append(feat)
        overlays[ov_key] = filtered

    result = {
        "type":     "FeatureCollection",
        "features": features,
        "meta":     {
            "cells":            len(features),
            "resolution_miles": resolution,
            "target_date":      target_date.isoformat(),
            "urban_filtered":   urban_filtered,
            "overlays":         overlays,
        },
    }
    results_cache_set(phash, bounds, result)
    return jsonify(result)


if __name__ == "__main__":
    import threading
    import webbrowser

    port = 8081
    threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    print(f"🍄  Morel Support →  http://localhost:{port}")
    app.run(port=port, debug=False)
