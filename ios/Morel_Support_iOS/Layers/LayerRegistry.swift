import Foundation

// Registry of all fetchable layers
let layerRegistry: [String: any Layer] = [
    "inat":      InatLayer(),
    "precip":    PrecipitationLayer(),
    "fires":     FiresLayer(),
    "trees":     TreesLayer(),
    "elevation": ElevationLayer(),
    "soil":      SoilLayer(),
]

// SeasonLayer is excluded — no fetch, scored directly in ScoringEngine
let seasonLayer = SeasonLayer()
