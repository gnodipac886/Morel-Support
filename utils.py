"""Shared HTTP and geometry utilities."""

import json
import math
import time
import urllib.error
import urllib.request


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS-84 points, in statute miles."""
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def fetch_json(url: str, timeout: int = 15):
    """GET *url* with up to 3 attempts (exponential back-off on 429).

    Returns parsed JSON dict/list, or None on any failure.
    """
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MorelSupport/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            print(f"[fetch] {url[:90]} → HTTP {exc.code}: {exc.reason}")
            return None
        except Exception as exc:
            print(f"[fetch] {url[:90]} → {exc}")
            return None
    return None
