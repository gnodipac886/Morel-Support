import Foundation

// MARK: - Weights

let defaultWeights: [String: Double] = [
    "inat":      0.30,
    "precip":    0.25,
    "fires":     0.20,
    "trees":     0.12,
    "season":    0.07,
    "soil":      0.04,
    "elevation": 0.02,
]

// MARK: - Burn species (must match TreesLayer)

/// Tree species whose presence inside a fire perimeter triggers a probability boost.
let burnSpecies: Set<String> = ["douglas_fir", "pine", "white_fir"]

// MARK: - Combine

/// Compute a weighted-average score from a per-layer score dictionary.
///
/// For each layer present in `scores` the weight is taken from `customWeights` if supplied,
/// falling back to `defaultWeights`, then falling back to 0.  The result is normalised by
/// the sum of active weights so missing layers do not penalise the overall score.
///
/// - Parameters:
///   - scores: Map of layer name → raw score in [0, 1].
///   - customWeights: Caller-supplied overrides; may be empty.
/// - Returns: Weighted-average score in [0, 1], or 0 if `scores` is empty.
func combine(scores: [String: Double], customWeights: [String: Double]) -> Double {
    guard !scores.isEmpty else { return 0.0 }

    var weightedSum = 0.0
    var totalWeight = 0.0

    for (layer, score) in scores {
        let w = customWeights[layer] ?? defaultWeights[layer] ?? 0.0
        weightedSum += score * w
        totalWeight += w
    }

    guard totalWeight > 0 else { return 0.0 }
    return weightedSum / totalWeight
}

// MARK: - CellScore

/// The scored result for a single grid cell.
struct CellScore: Sendable {
    /// Overall morel probability as an integer percentage (0–100).
    let probability: Int
    /// Per-layer scores as integer percentages (0–100).
    let layerScores: [String: Int]
    /// Human-readable detail strings (observation counts, boosted species, etc.)
    let details: [String: String]
}

// MARK: - ScoringEngine

/// Score a single grid cell by combining all available layer data.
///
/// - Parameters:
///   - cell: The grid cell to score.
///   - layerData: Fetched data keyed by layer name.
///   - layerOptions: Options keyed by layer name; used to check `enabled` flags.
///   - weights: Custom weight overrides; may be empty to use `defaultWeights`.
///   - targetDate: The date the user intends to forage.
/// - Returns: A `CellScore` with probability in 0–100 and per-layer breakdown.
func scoreCell(
    cell: GridCell,
    layerData: [String: LayerData],
    layerOptions: [String: LayerOptions],
    weights: [String: Double],
    targetDate: Date
) -> CellScore {
    let lat = cell.centerLat
    let lon = cell.centerLon

    var scores:  [String: Double] = [:]
    var details: [String: String] = [:]

    // ── iNaturalist ───────────────────────────────────────────────────────────
    if let data = layerData["inat"] {
        let obs = data.inatObs
        let opts = layerOptions["inat"] ?? LayerOptions()
        let (s, cnt) = scoreInat(
            cellLat: lat,
            cellLon: lon,
            observations: obs,
            options: opts,
            targetDate: targetDate
        )
        scores["inat"] = s
        details["iNat sightings"] = "\(cnt)"
    }

    // ── Precipitation ─────────────────────────────────────────────────────────
    if let data = layerData["precip"] {
        let pts  = data.precipPts
        let opts = layerOptions["precip"] ?? LayerOptions()
        let (s, det) = scorePrecip(cellLat: lat, cellLon: lon, points: pts, options: opts)
        scores["precip"] = s
        details.merge(det) { _, new in new }
    }

    // ── Fires ─────────────────────────────────────────────────────────────────
    if let data = layerData["fires"] {
        let feats = data.fireFeats
        let opts  = layerOptions["fires"] ?? LayerOptions()
        let (s, cnt) = scoreFires(
            cellLat: lat,
            cellLon: lon,
            cellBounds: cell.bounds,
            fires: feats,
            options: opts
        )
        scores["fires"] = s
        details["Fire perimeters"] = "\(cnt)"
    }

    // ── Trees ─────────────────────────────────────────────────────────────────
    if let data = layerData["trees"] {
        let obs  = data.treeObs
        let opts = layerOptions["trees"] ?? LayerOptions()
        let (s, cnt) = scoreTrees(cellLat: lat, cellLon: lon, trees: obs, options: opts)
        scores["trees"] = s
        details["Host trees"] = "\(cnt)"
    }

    // ── Burn-species fire boost ───────────────────────────────────────────────
    // When fire score is meaningful AND burn-species host trees are present nearby,
    // boost both the fire and tree scores to reflect post-burn morel conditions.
    if let fireScore = scores["fires"], fireScore > 0.15,
       let treeData = layerData["trees"] {
        let burnNearby = treeData.treeObs.filter { obs in
            burnSpecies.contains(obs.species) &&
            haversineMiles(lat, lon, obs.lat, obs.lon) <= 8.0
        }
        if !burnNearby.isEmpty {
            scores["fires"] = min(1.0, fireScore * 1.55)
            scores["trees"] = min(1.0, (scores["trees"] ?? 0.5) * 1.45)
            details["Burn species in fire zone"] = "\(burnNearby.count)"
        }
    }

    // ── Seasonality ───────────────────────────────────────────────────────────
    if layerOptions["season"]?.enabled == true {
        scores["season"] = scoreSeasonality(lat: lat, targetDate: targetDate)
    }

    // ── Elevation ─────────────────────────────────────────────────────────────
    if let data = layerData["elevation"] {
        scores["elevation"] = scoreElevation(
            cellLat: lat,
            cellLon: lon,
            points: data.elevPts
        )
    }

    // ── Soil ──────────────────────────────────────────────────────────────────
    if let data = layerData["soil"] {
        scores["soil"] = scoreSoil(
            cellLat: lat,
            cellLon: lon,
            points: data.soilPts
        )
    }

    // ── Combine ───────────────────────────────────────────────────────────────
    let prob = combine(scores: scores, customWeights: weights)

    let layerScoresInt = Dictionary(
        uniqueKeysWithValues: scores.map { ($0.key, Int((($0.value * 100).rounded()))) }
    )

    return CellScore(
        probability: Int((prob * 100).rounded()),
        layerScores: layerScoresInt,
        details: details
    )
}
