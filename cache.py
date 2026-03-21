"""In-process TTL caches for per-layer data and fully-scored results."""

import datetime
import hashlib
import json
import threading

from utils import haversine_miles

# Viewport centre must stay within this many miles for a cache hit.
CACHE_RADIUS_MILES = 75

# Results cache lifetime matches the most-volatile layer (precip = 6 h).
_RESULTS_TTL = 6 * 3600

_cache: dict      = {}
_cache_lock       = threading.Lock()

_results_cache: dict = {}
_results_lock        = threading.Lock()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bounds_center(bounds: dict) -> tuple:
    return (
        (bounds["north"] + bounds["south"]) / 2,
        (bounds["east"]  + bounds["west"])  / 2,
    )


def _opts_hash(opts: dict) -> str:
    return hashlib.md5(json.dumps(opts, sort_keys=True).encode()).hexdigest()[:10]


# ── Per-layer cache ───────────────────────────────────────────────────────────

def cache_get(layer: str, bounds: dict, opts: dict, ttl: int = 24 * 3600):
    """Return cached layer data or *None* if missing / expired / out-of-range."""
    key         = (layer, _opts_hash(opts))
    now         = datetime.datetime.utcnow()
    clat, clon  = _bounds_center(bounds)
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        age  = (now - entry["fetched_at"]).total_seconds()
        dist = haversine_miles(clat, clon, entry["center"][0], entry["center"][1])
        if age > ttl or dist > CACHE_RADIUS_MILES:
            del _cache[key]
            reason = f"expired ({age/3600:.1f}h old)" if age > ttl else f"moved {dist:.0f} mi"
            print(f"[cache] {layer} invalidated — {reason}")
            return None
        print(f"[cache] {layer} hit  (center {dist:.1f} mi away, {age/3600:.1f}h old)")
        return entry["data"]


def cache_set(layer: str, bounds: dict, opts: dict, data) -> None:
    key = (layer, _opts_hash(opts))
    with _cache_lock:
        _cache[key] = {
            "center":     _bounds_center(bounds),
            "fetched_at": datetime.datetime.utcnow(),
            "data":       data,
        }


# ── Results cache ─────────────────────────────────────────────────────────────

def payload_hash(
    bounds, resolution, layers, weights, lookahead_weeks, skip_urban, urban_scale
) -> str:
    payload = {
        "bounds":          bounds,
        "resolution":      resolution,
        "layers":          layers,
        "weights":         weights,
        "lookahead_weeks": lookahead_weeks,
        "skip_urban":      skip_urban,
        "urban_scale":     urban_scale,
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def results_cache_get(phash: str, bounds: dict):
    now        = datetime.datetime.utcnow()
    clat, clon = _bounds_center(bounds)
    with _results_lock:
        entry = _results_cache.get(phash)
        if entry is None:
            return None
        age  = (now - entry["fetched_at"]).total_seconds()
        dist = haversine_miles(clat, clon, entry["center"][0], entry["center"][1])
        if age > _RESULTS_TTL or dist > CACHE_RADIUS_MILES:
            del _results_cache[phash]
            return None
        print(f"[cache] results hit (age {age/3600:.1f}h, center {dist:.1f} mi away)")
        return entry["data"]


def results_cache_set(phash: str, bounds: dict, data: dict) -> None:
    with _results_lock:
        _results_cache[phash] = {
            "center":     _bounds_center(bounds),
            "fetched_at": datetime.datetime.utcnow(),
            "data":       data,
        }
