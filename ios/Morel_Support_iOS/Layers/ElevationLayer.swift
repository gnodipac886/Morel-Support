import Foundation

// MARK: - ElevationLayer

struct ElevationLayer: Layer {
    let id = "elevation"
    let cacheTTL: TimeInterval = 72 * 3600

    func fetch(bounds: Bounds, opts: LayerOptions, grid: [GridCell]) async -> LayerData {
        let cells = grid
        guard !cells.isEmpty else { return .elevation([]) }

        // OpenTopoData accepts at most 100 locations per request
        let batch = Array(cells.prefix(100))
        let locString = batch
            .map { String(format: "%.6f,%.6f", $0.centerLat, $0.centerLon) }
            .joined(separator: "|")

        guard var components = URLComponents(string: "https://api.opentopodata.org/v1/ned10m") else {
            return .elevation([])
        }
        components.queryItems = [URLQueryItem(name: "locations", value: locString)]
        guard let url = components.url else { return .elevation([]) }

        struct TopoResult: Decodable {
            let elevation: Double?
        }
        struct TopoResponse: Decodable {
            let results: [TopoResult]?
        }

        let response = await NetworkService.shared.fetchJSON(url, as: TopoResponse.self, timeout: 20)
        let results  = response?.results ?? []

        var points: [ElevationPoint] = []
        for (i, result) in results.enumerated() {
            guard i < batch.count else { break }
            let cell = batch[i]
            points.append(ElevationPoint(
                lat: cell.centerLat,
                lon: cell.centerLon,
                elevationM: result.elevation
            ))
        }

        print("[elevation] \(points.count) values")
        return .elevation(points)
    }

    func toRawFeatures(data: LayerData) -> [RawFeature] {
        return data.elevPts.compactMap { pt in
            guard let elevM = pt.elevationM else { return nil }
            let elevFt = Int((elevM * 3.281).rounded())
            return RawFeature(
                geometry: .point(lat: pt.lat, lon: pt.lon),
                props: ["elevation_ft": "\(elevFt)"]
            )
        }
    }
}

// MARK: - Scoring

/// Score a grid cell based on its elevation.
///
/// Elevation thresholds (in feet) derived from the Python original:
///
/// | Range (ft)      | Score |
/// |-----------------|-------|
/// | < 0             | 0.10  |
/// | 0 – 299         | 0.30  |
/// | 300 – 599       | 0.65  |
/// | 600 – 1499      | 1.00  |
/// | 1500 – 2999     | 0.90  |
/// | 3000 – 4999     | 0.55  |
/// | 5000 – 7999     | 0.25  |
/// | ≥ 8000          | 0.10  |
///
/// Returns 0.5 when no elevation data is available for the cell.
func scoreElevation(cellLat: Double, cellLon: Double, points: [ElevationPoint]) -> Double {
    guard !points.isEmpty else { return 0.5 }

    guard let nearest = points.min(by: {
        haversineMiles(cellLat, cellLon, $0.lat, $0.lon) <
        haversineMiles(cellLat, cellLon, $1.lat, $1.lon)
    }) else { return 0.5 }

    guard let elevM = nearest.elevationM else { return 0.5 }

    let ft = elevM * 3.281
    switch ft {
    case ..<0:         return 0.10
    case 0..<300:      return 0.30
    case 300..<600:    return 0.65
    case 600..<1500:   return 1.00
    case 1500..<3000:  return 0.90
    case 3000..<5000:  return 0.55
    case 5000..<8000:  return 0.25
    default:           return 0.10
    }
}
