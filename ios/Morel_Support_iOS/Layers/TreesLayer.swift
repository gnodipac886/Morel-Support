import Foundation

// MARK: - Constants

/// Tree species whose presence inside a recent fire perimeter boosts morel probability.
let BURN_SPECIES: Set<String> = ["douglas_fir", "pine", "white_fir", "ponderosa_pine"]

// MARK: - TreesLayer

struct TreesLayer: Layer {
    let id = "trees"
    let cacheTTL: TimeInterval = 48 * 3600

    /// GBIF backbone taxon keys for morel host-tree genera/species.
    private static let taxonMap: [(species: String, taxonKey: Int)] = [
        ("elm",          6265),
        ("ash",          3189866),
        ("tulip_poplar", 3190081),
        ("sycamore",     2874592),
        ("cottonwood",   3190165),
        ("apple",        3000641),
        ("douglas_fir",  5284895),
        ("pine",         2684241),
        ("white_fir",    2684222),
    ]

    func fetch(bounds: Bounds, opts: LayerOptions, grid: [GridCell]) async -> LayerData {
        // Allow callers to restrict which species are fetched
        let speciesFilter: Set<String>
        let rawFilter = opts.str("species", "")
        if rawFilter.isEmpty {
            speciesFilter = Set(Self.taxonMap.map(\.species))
        } else {
            speciesFilter = Set(rawFilter.split(separator: ",").map { String($0).trimmingCharacters(in: .whitespaces) })
        }

        let observations: [TreeObservation] = await withTaskGroup(of: [TreeObservation].self) { group in
            for entry in Self.taxonMap where speciesFilter.contains(entry.species) {
                group.addTask {
                    await Self.fetchGBIF(taxonKey: entry.taxonKey, species: entry.species, bounds: bounds)
                }
            }
            var all: [TreeObservation] = []
            for await chunk in group { all.append(contentsOf: chunk) }
            return all
        }

        print("[trees] \(observations.count) occurrences (GBIF)")
        return .trees(observations)
    }

    func toRawFeatures(data: LayerData) -> [RawFeature] {
        data.treeObs.map { obs in
            let props: [String: String] = [
                "species": obs.species,
                "source":  obs.source,
            ]
            return RawFeature(geometry: .point(lat: obs.lat, lon: obs.lon), props: props)
        }
    }

    // MARK: - Private GBIF fetch

    private static func fetchGBIF(taxonKey: Int, species: String, bounds: Bounds) async -> [TreeObservation] {
        var comps = URLComponents(string: "https://api.gbif.org/v1/occurrence/search")!
        comps.queryItems = [
            URLQueryItem(name: "taxonKey",
                         value: "\(taxonKey)"),
            // GBIF expects latitude/longitude ranges as "min,max"
            URLQueryItem(name: "decimalLatitude",
                         value: "\(String(format: "%.4f", bounds.south)),\(String(format: "%.4f", bounds.north))"),
            URLQueryItem(name: "decimalLongitude",
                         value: "\(String(format: "%.4f", bounds.west)),\(String(format: "%.4f", bounds.east))"),
            URLQueryItem(name: "limit",            value: "300"),
            URLQueryItem(name: "hasCoordinate",    value: "true"),
            URLQueryItem(name: "occurrenceStatus", value: "PRESENT"),
        ]
        guard let url = comps.url else { return [] }

        struct GBIFResponse: Decodable {
            struct Result: Decodable {
                let decimalLatitude:  Double?
                let decimalLongitude: Double?
            }
            let results: [Result]?
        }

        guard let resp = await NetworkService.shared.fetchJSON(url, as: GBIFResponse.self, timeout: 15) else {
            return []
        }

        return (resp.results ?? []).compactMap { r in
            guard let lat = r.decimalLatitude, let lon = r.decimalLongitude else { return nil }
            return TreeObservation(lat: lat, lon: lon, species: species, source: "GBIF")
        }
    }
}

// MARK: - Scoring

/// Score a grid cell based on nearby host-tree occurrences.
///
/// Species weights reflect empirical morel association strength.
/// Distance decay is applied within 0.5 miles (very local habitat signal).
///
/// - Parameters:
///   - cellLat: Cell center latitude.
///   - cellLon: Cell center longitude.
///   - trees: The `[TreeObservation]` array from `TreesLayer.fetch`.
///   - options: Layer options (currently unused, reserved for future tuning).
/// - Returns: A score in [0, 1] and the count of trees within 0.5 miles.
func scoreTrees(
    cellLat: Double,
    cellLon: Double,
    trees: [TreeObservation],
    options: LayerOptions
) -> (score: Double, count: Int) {
    guard !trees.isEmpty else { return (0.5, 0) }

    // Species weights match Python web app; conifers kept for burn-species boost
    let weights: [String: Double] = [
        "ash":          0.90,
        "elm":          0.85,
        "tulip_poplar": 0.80,
        "cottonwood":   0.70,
        "douglas_fir":  0.70,
        "sycamore":     0.65,
        "white_fir":    0.65,
        "apple":        0.60,
        "pine":         0.60,
        "deciduous":    0.40,
    ]

    let maxDistMiles = 8.0  // matches Python hardcoded max_dist
    let speciesFilter: Set<String>? = {
        let raw = options.str("species")
        guard !raw.isEmpty else { return nil }   // nil = all species enabled
        return Set(raw.split(separator: ",").map(String.init))
    }()
    var best  = 0.0
    var count = 0

    for obs in trees {
        if let filter = speciesFilter, !filter.contains(obs.species) { continue }
        let dist = haversineMilesT(cellLat, cellLon, obs.lat, obs.lon)
        guard dist <= maxDistMiles else { continue }
        count += 1

        let w = weights[obs.species, default: 0.40]
        // Non-linear decay: close trees score much higher than distant ones
        let score = w * max(0.0, 1.0 - pow(dist / maxDistMiles, 0.8))
        best = max(best, score)
    }

    return (min(1.0, best), count)
}

// MARK: - Internal geometry helper

private func haversineMilesT(_ lat1: Double, _ lon1: Double, _ lat2: Double, _ lon2: Double) -> Double {
    let R = 3958.8
    let dLat = (lat2 - lat1) * .pi / 180
    let dLon = (lon2 - lon1) * .pi / 180
    let a = sin(dLat / 2) * sin(dLat / 2)
        + cos(lat1 * .pi / 180) * cos(lat2 * .pi / 180)
        * sin(dLon / 2) * sin(dLon / 2)
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))
}
