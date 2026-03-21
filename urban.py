"""OSM Overpass urban filter: fetch city/town nodes and compute exclusion radii."""

import json
import urllib.request


def fetch_urban_place_nodes(bounds: dict):
    """Query OSM Overpass for city/town nodes inside *bounds*.

    Returns a list of dicts ``{lat, lon, name, radius_miles}`` or ``None``
    on network failure.  Callers should skip urban filtering when ``None``
    is returned so no cells are accidentally discarded.
    """
    n, s, e, w = bounds["north"], bounds["south"], bounds["east"], bounds["west"]
    pad  = 0.15   # degrees buffer beyond viewport
    bbox = f"{s - pad},{w - pad},{n + pad},{e + pad}"
    query = f"""[out:json][timeout:30];
(
  node["place"~"^(city|town)$"]({bbox});
);
out body;
"""
    try:
        req = urllib.request.Request(
            "https://overpass-api.de/api/interpreter",
            data=query.encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent":   "MushroomMapApp/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=35) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        print(f"[urban] Overpass error: {exc}")
        return None  # None → skip filtering; don't drop all cells

    places = []
    for el in data.get("elements", []):
        tags  = el.get("tags", {})
        place = tags.get("place", "")
        try:
            pop = int(tags.get("population", 0))
        except (ValueError, TypeError):
            pop = 0

        if place == "city":
            if   pop > 500_000: radius = 6.0
            elif pop > 100_000: radius = 4.0
            elif pop > 50_000:  radius = 2.5
            else:               radius = 1.5
        else:  # town
            if 0 < pop < 15_000:
                continue   # too small to be considered urban
            radius = 1.2 if pop > 20_000 else 0.8

        places.append({
            "lat":          el["lat"],
            "lon":          el["lon"],
            "name":         tags.get("name", ""),
            "radius_miles": radius,
        })

    print(f"[urban] Overpass: {len(places)} place nodes")
    return places
