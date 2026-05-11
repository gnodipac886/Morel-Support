import Foundation

// MARK: - SoilLayer

struct SoilLayer: Layer {
    let id = "soil"
    let cacheTTL: TimeInterval = 72 * 3600

    func fetch(bounds: Bounds, opts: LayerOptions, grid: [GridCell]) async -> LayerData {
        let n = bounds.north, s = bounds.south
        let e = bounds.east,  w = bounds.west

        // Three representative sample points spread across the bounding box
        let samplePoints: [(lat: Double, lon: Double)] = [
            ((n + s) / 2,                   (e + w) / 2),
            (s + (n - s) * 0.25, w + (e - w) * 0.25),
            (s + (n - s) * 0.75, w + (e - w) * 0.75),
        ]

        var results: [SoilPoint] = []

        for pt in samplePoints {
            var components = URLComponents(string: "https://rest.isric.org/soilgrids/v2.0/properties/query")!
            components.queryItems = [
                URLQueryItem(name: "lon",      value: String(format: "%.4f", pt.lon)),
                URLQueryItem(name: "lat",      value: String(format: "%.4f", pt.lat)),
                URLQueryItem(name: "property", value: "phh2o,clay"),
                URLQueryItem(name: "depth",    value: "5-15cm"),
                URLQueryItem(name: "value",    value: "mean"),
            ]
            guard let url = components.url else { continue }

            struct DepthValues: Decodable { let mean: Double? }
            struct Depth: Decodable { let label: String?; let values: DepthValues }
            struct SoilLayer_: Decodable {
                let name: String?
                let depths: [Depth]?
            }
            struct Properties: Decodable { let layers: [SoilLayer_]? }
            struct SoilResponse: Decodable { let properties: Properties? }

            let response = await NetworkService.shared.fetchJSON(url, as: SoilResponse.self, timeout: 20)

            var phValue:   Double? = nil
            var clayValue: Double? = nil

            if let layers = response?.properties?.layers {
                let byName = Dictionary(uniqueKeysWithValues: layers.compactMap { l -> (String, SoilLayer_)? in
                    guard let name = l.name else { return nil }
                    return (name, l)
                })

                if let phLayer = byName["phh2o"] {
                    for depth in phLayer.depths ?? [] {
                        if depth.label == "5-15cm", let mean = depth.values.mean {
                            phValue = mean / 10.0   // stored as pH * 10
                        }
                    }
                }

                if let clayLayer = byName["clay"] {
                    for depth in clayLayer.depths ?? [] {
                        if depth.label == "5-15cm" {
                            clayValue = depth.values.mean   // g/kg
                        }
                    }
                }
            }

            results.append(SoilPoint(lat: pt.lat, lon: pt.lon, ph: phValue, clay: clayValue))
        }

        print("[soil] \(results.count) sample points")
        return .soil(results)
    }

    func toRawFeatures(data: LayerData) -> [RawFeature] {
        return data.soilPts.map { pt in
            var props: [String: String] = [:]
            if let ph   = pt.ph   { props["ph"]   = String(format: "%.2f", ph)   }
            if let clay = pt.clay { props["clay"]  = String(format: "%.0f", clay) }
            return RawFeature(geometry: .point(lat: pt.lat, lon: pt.lon), props: props)
        }
    }
}

// MARK: - Scoring

/// Score a grid cell based on soil pH and clay content from the nearest sample point.
///
/// pH score table:
///
/// | pH range  | Score |
/// |-----------|-------|
/// | < 4.5     | 0.10  |
/// | < 5.5     | 0.35  |
/// | < 6.0     | 0.65  |
/// | < 7.0     | 1.00  |
/// | < 7.5     | 0.85  |
/// | < 8.0     | 0.50  |
/// | ≥ 8.0     | 0.20  |
///
/// Clay score table (g/kg):
///
/// | Clay range | Score |
/// |------------|-------|
/// | < 50       | 0.60  |
/// | < 200      | 1.00  |
/// | < 350      | 0.70  |
/// | ≥ 350      | 0.30  |
///
/// Combined as: ph_score × 0.6 + clay_score × 0.4.
/// Returns 0.5 when no soil data is available.
func scoreSoil(cellLat: Double, cellLon: Double, points: [SoilPoint]) -> Double {
    guard !points.isEmpty else { return 0.5 }

    guard let nearest = points.min(by: {
        haversineMiles(cellLat, cellLon, $0.lat, $0.lon) <
        haversineMiles(cellLat, cellLon, $1.lat, $1.lon)
    }) else { return 0.5 }

    var phScore: Double = 0.5
    if let ph = nearest.ph {
        switch ph {
        case ..<4.5:      phScore = 0.10
        case 4.5..<5.5:   phScore = 0.35
        case 5.5..<6.0:   phScore = 0.65
        case 6.0..<7.0:   phScore = 1.00
        case 7.0..<7.5:   phScore = 0.85
        case 7.5..<8.0:   phScore = 0.50
        default:          phScore = 0.20
        }
    }

    var clayScore: Double = 0.5
    if let clay = nearest.clay {
        switch clay {
        case ..<50:    clayScore = 0.60
        case 50..<200: clayScore = 1.00
        case 200..<350:clayScore = 0.70
        default:       clayScore = 0.30
        }
    }

    return phScore * 0.6 + clayScore * 0.4
}
