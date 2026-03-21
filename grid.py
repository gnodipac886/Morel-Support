"""Grid generation: subdivide a bounding box into land-only cells."""

import math

from global_land_mask import globe


def create_grid(bounds: dict, resolution_miles: float) -> list:
    """Return a list of land-only grid cells for *bounds*.

    Each cell dict has:
        ``center`` – ``(lat, lon)`` tuple
        ``bounds`` – ``{south, north, west, east}``
    """
    n, s, e, w = bounds["north"], bounds["south"], bounds["east"], bounds["west"]
    clat     = (n + s) / 2
    lat_step = resolution_miles / 69.0
    lon_step = resolution_miles / (69.0 * math.cos(math.radians(clat)) + 1e-9)

    cells = []
    lat   = s
    while lat < n:
        lon = w
        while lon < e:
            cn       = min(lat + lat_step, n)
            ce       = min(lon + lon_step, e)
            cell_lat = (lat + cn) / 2
            cell_lon = (lon + ce) / 2
            if globe.is_land(cell_lat, cell_lon):
                cells.append({
                    "center": (cell_lat, cell_lon),
                    "bounds": {"south": lat, "north": cn, "west": lon, "east": ce},
                })
            lon += lon_step
        lat += lat_step
    return cells
