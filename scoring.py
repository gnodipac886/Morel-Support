"""Cell scoring: combine layer scores into a morel probability per grid cell."""

from __future__ import annotations

import datetime

from utils import haversine_miles
from layers.inat          import score_inat
from layers.precipitation import score_precip
from layers.fires         import score_fires
from layers.trees         import score_trees, BURN_SPECIES
from layers.season        import score_seasonality
from layers.elevation     import score_elevation
from layers.soil          import score_soil


DEFAULT_WEIGHTS = {
    "inat":      0.30,
    "precip":    0.25,
    "fires":     0.20,
    "trees":     0.12,
    "season":    0.07,
    "soil":      0.04,
    "elevation": 0.02,
}


def combine(scores: dict, custom_weights: dict) -> float:
    if not scores:
        return 0.0
    w     = {k: custom_weights.get(k, DEFAULT_WEIGHTS.get(k, 0)) for k in scores}
    total = sum(w.values())
    if total == 0:
        return 0.0
    return sum(scores[k] * w[k] for k in scores) / total


# ── Process-pool worker ────────────────────────────────────────────────────────
# Must be module-level (not a class method) so it's picklable for
# ProcessPoolExecutor with the 'spawn' start method.

def _score_cell_worker(args):
    """Score a single grid cell. Runs in a worker process."""
    cell, layer_data, layers_cfg, weights, target_date = args
    lat, lon = cell["center"]
    cb       = cell["bounds"]
    scores   = {}
    details  = {}

    if "inat" in layer_data:
        s, cnt = score_inat(lat, lon, layer_data["inat"], layers_cfg.get("inat", {}), target_date)
        scores["inat"] = s
        details["iNat sightings"] = cnt

    if "precip" in layer_data:
        s, det = score_precip(lat, lon, layer_data["precip"], layers_cfg.get("precip", {}))
        scores["precip"] = s
        details.update(det)

    if "fires" in layer_data:
        s, cnt = score_fires(lat, lon, cb, layer_data["fires"], layers_cfg.get("fires", {}))
        scores["fires"] = s
        details["Fire perimeters"] = cnt

    if "trees" in layer_data:
        s, cnt = score_trees(lat, lon, layer_data["trees"], layers_cfg.get("trees", {}))
        scores["trees"] = s
        details["Host trees"] = cnt

    # Burn-species fire boost
    if "fires" in scores and "trees" in layer_data and scores.get("fires", 0) > 0.15:
        burn_nearby = [
            obs for obs in layer_data["trees"]
            if obs.get("species") in BURN_SPECIES
            and haversine_miles(lat, lon, obs["lat"], obs["lon"]) <= 8.0
        ]
        if burn_nearby:
            scores["fires"] = min(1.0, scores["fires"] * 1.55)
            scores["trees"] = min(1.0, scores.get("trees", 0.5) * 1.45)
            details["Burn species in fire zone"] = len(burn_nearby)

    if layers_cfg.get("season", {}).get("enabled"):
        scores["season"] = score_seasonality(lat, lon, target_date)

    if "elevation" in layer_data:
        scores["elevation"] = score_elevation(lat, lon, layer_data["elevation"])

    if "soil" in layer_data:
        scores["soil"] = score_soil(lat, lon, layer_data["soil"])

    prob = combine(scores, weights)
    b    = cb
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [b["west"], b["south"]], [b["east"], b["south"]],
                [b["east"], b["north"]], [b["west"], b["north"]],
                [b["west"], b["south"]],
            ]],
        },
        "properties": {
            "probability":  round(prob * 100),
            "layer_scores": {k: round(v * 100) for k, v in scores.items()},
            "details":      details,
        },
    }
