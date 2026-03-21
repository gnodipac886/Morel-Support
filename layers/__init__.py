"""Layer registry — maps string keys to BaseLayer instances."""

from __future__ import annotations

from layers.inat          import InatLayer
from layers.precipitation import PrecipLayer
from layers.fires         import FiresLayer
from layers.trees         import TreesLayer
from layers.season        import SeasonLayer
from layers.elevation     import ElevationLayer
from layers.soil          import SoilLayer

# SeasonLayer is intentionally excluded from LAYERS because it has no
# external fetch and is applied directly in the scoring worker.
LAYERS: dict = {
    "inat":      InatLayer(),
    "precip":    PrecipLayer(),
    "fires":     FiresLayer(),
    "trees":     TreesLayer(),
    "elevation": ElevationLayer(),
    "soil":      SoilLayer(),
}

__all__ = [
    "LAYERS",
    "InatLayer",
    "PrecipLayer",
    "FiresLayer",
    "TreesLayer",
    "SeasonLayer",
    "ElevationLayer",
    "SoilLayer",
]
