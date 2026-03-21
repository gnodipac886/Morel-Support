"""Abstract base class shared by all data layers."""

from __future__ import annotations


class BaseLayer:
    """Contract for a morel probability data layer.

    Subclasses implement ``fetch()`` and ``to_geojson()``.  Scoring functions
    live at module level in each layer file so they stay picklable for use
    inside a ``ProcessPoolExecutor``.
    """

    #: Unique key matching the frontend layer id (e.g. ``"inat"``).
    name: str = ""

    #: How long (seconds) to consider fetched data fresh.
    cache_ttl: int = 24 * 3600

    def fetch(self, bounds: dict, opts: dict, grid: list | None = None):
        """Retrieve raw data for this layer.

        Args:
            bounds: ``{north, south, east, west}``
            opts:   Layer-specific config dict from the frontend.
            grid:   List of cell dicts; needed by grid-sampled layers (precip,
                    elevation).

        Returns a layer-specific data structure (list or dict).
        """
        raise NotImplementedError

    def to_geojson(self, data) -> list:
        """Convert raw fetched *data* to a list of GeoJSON Feature dicts."""
        return []
