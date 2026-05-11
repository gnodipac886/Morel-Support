import Foundation

// MARK: - PrecipitationLayer

struct PrecipitationLayer: Layer {
    let id = "precip"
    let cacheTTL: TimeInterval = 6 * 3600

    func fetch(bounds: Bounds, opts: LayerOptions, grid: [GridCell]) async -> LayerData {
        guard !grid.isEmpty else { return .precip([]) }

        // Derive a bounding box that covers all grid cells
        let lats = grid.map(\.centerLat)
        let lons = grid.map(\.centerLon)
        let n = lats.max()!; let s = lats.min()!
        let e = lons.max()!; let w = lons.min()!

        // 3×3 grid of representative sample points spread across the bounding box
        let fractions: [Double] = [0.1, 0.5, 0.9]
        var sampleCoords: [(lat: Double, lon: Double)] = []
        for fy in fractions {
            for fx in fractions {
                sampleCoords.append((s + (n - s) * fy, w + (e - w) * fx))
            }
        }

        // Fetch all sample points concurrently
        let windowDays = opts.int("time_window", 14)
        let samplePoints: [PrecipPoint] = await withTaskGroup(of: PrecipPoint?.self) { group in
            for coord in sampleCoords {
                group.addTask {
                    await Self.fetchOpenMeteo(lat: coord.lat, lon: coord.lon, windowDays: windowDays)
                }
            }
            var results: [PrecipPoint] = []
            for await pt in group {
                if let pt { results.append(pt) }
            }
            return results
        }

        guard !samplePoints.isEmpty else {
            // Fallback: return empty PrecipPoints for each cell so scoring returns neutral
            let fallback = grid.map { cell in
                PrecipPoint(lat: cell.centerLat, lon: cell.centerLon, bounds: cell.bounds,
                            precipIn: 0, snowIn: 0, soilTempF: nil, source: "")
            }
            return .precip(fallback)
        }

        // For each grid cell find the nearest sample point and assign its readings
        let cellPoints: [PrecipPoint] = grid.map { cell in
            let nearest = samplePoints.min(by: {
                haversineMilesP($0.lat, $0.lon, cell.centerLat, cell.centerLon) <
                haversineMilesP($1.lat, $1.lon, cell.centerLat, cell.centerLon)
            })!
            return PrecipPoint(
                lat:        cell.centerLat,
                lon:        cell.centerLon,
                bounds:     cell.bounds,
                precipIn:   (nearest.precipIn * 100).rounded() / 100,
                snowIn:     (nearest.snowIn   * 100).rounded() / 100,
                soilTempF:  nearest.soilTempF,
                source:     nearest.source
            )
        }

        let avg = samplePoints.map(\.precipIn).reduce(0, +) / Double(samplePoints.count)
        print("[precip] \(samplePoints.count)/9 pts · avg \(String(format: "%.2f", avg)) in")
        return .precip(cellPoints)
    }

    func toRawFeatures(data: LayerData) -> [RawFeature] {
        data.precipPts.compactMap { pt in
            let props: [String: String] = [
                "precip_in":   String(format: "%.2f", pt.precipIn),
                "snow_in":     String(format: "%.2f", pt.snowIn),
                "soil_temp_f": pt.soilTempF.map { String(format: "%.1f", $0) } ?? "",
                "source":      pt.source,
            ]
            if let b = pt.bounds {
                // Emit the cell as a polygon so it renders as a filled tile
                let ring: [[Double]] = [
                    [b.west, b.south], [b.east, b.south],
                    [b.east, b.north], [b.west, b.north],
                    [b.west, b.south],
                ]
                return RawFeature(geometry: .polygon(rings: [ring]), props: props)
            } else {
                return RawFeature(geometry: .point(lat: pt.lat, lon: pt.lon), props: props)
            }
        }
    }

    // MARK: - Private fetch helper

    /// Fetch accumulated precipitation and mean soil temperature from Open-Meteo archive.
    private static func fetchOpenMeteo(lat: Double, lon: Double, windowDays: Int = 14) async -> PrecipPoint? {
        // Use the archive endpoint so we always have data up through yesterday
        var comps = URLComponents(string: "https://archive-api.open-meteo.com/v1/archive")!

        let today = Date()
        let yesterday = Calendar.current.date(byAdding: .day, value: -1, to: today)!
        let windowStart = Calendar.current.date(byAdding: .day, value: -windowDays, to: yesterday)!

        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd"
        df.timeZone = TimeZone(identifier: "UTC")

        comps.queryItems = [
            URLQueryItem(name: "latitude",         value: String(format: "%.3f", lat)),
            URLQueryItem(name: "longitude",        value: String(format: "%.3f", lon)),
            URLQueryItem(name: "start_date",       value: df.string(from: windowStart)),
            URLQueryItem(name: "end_date",         value: df.string(from: yesterday)),
            URLQueryItem(name: "daily",            value: "precipitation_sum,snowfall_sum,soil_temperature_0_to_7cm_mean"),
            URLQueryItem(name: "temperature_unit", value: "fahrenheit"),
            URLQueryItem(name: "timezone",         value: "auto"),
        ]
        guard let url = comps.url else { return nil }

        struct OMResponse: Decodable {
            struct Daily: Decodable {
                let precipitation_sum: [Double?]?
                let snowfall_sum:      [Double?]?
                let soil_temperature_0_to_7cm_mean: [Double?]?
            }
            let daily: Daily?
        }

        guard let resp = await NetworkService.shared.fetchJSON(url, as: OMResponse.self, timeout: 10),
              let daily = resp.daily else { return nil }

        let precipMm = (daily.precipitation_sum ?? []).map { $0 ?? 0.0 }
        let snowCm   = (daily.snowfall_sum ?? []).map      { $0 ?? 0.0 }
        let soilTemps = (daily.soil_temperature_0_to_7cm_mean ?? []).compactMap { $0 }

        let precipIn = precipMm.reduce(0, +) / 25.4
        let snowIn   = snowCm.reduce(0, +) / 10.0 / 2.54
        let soilTempF: Double? = soilTemps.isEmpty ? nil : soilTemps.reduce(0, +) / Double(soilTemps.count)

        return PrecipPoint(
            lat:       lat,
            lon:       lon,
            bounds:    nil,
            precipIn:  precipIn,
            snowIn:    snowIn,
            soilTempF: soilTempF,
            source:    "Open-Meteo"
        )
    }
}

// MARK: - Scoring

/// Score a grid cell for morel suitability based on recent precipitation and soil temperature.
///
/// - Parameters:
///   - cellLat: Cell center latitude.
///   - cellLon: Cell center longitude.
///   - points: The `[PrecipPoint]` array from `PrecipitationLayer.fetch`.
///   - options: Layer options (currently unused, reserved for future tuning).
/// - Returns: A score in [0, 1] and a details dict for display (rain amount, soil temp).
func scorePrecip(
    cellLat: Double,
    cellLon: Double,
    points: [PrecipPoint],
    options: LayerOptions
) -> (score: Double, details: [String: String]) {
    guard !points.isEmpty else { return (0.5, [:]) }

    // Find the closest data point to the cell centre
    let pt = points.min(by: {
        haversineMilesP($0.lat, $0.lon, cellLat, cellLon) <
        haversineMilesP($1.lat, $1.lon, cellLat, cellLon)
    })!

    let inches = pt.precipIn

    // Precipitation score: morels prefer 1–3.5 in of rain in the recent window
    let precipScore: Double
    switch inches {
    case ..<0.2:        precipScore = 0.05
    case 0.2..<0.5:     precipScore = 0.30
    case 0.5..<1.0:     precipScore = 0.60
    case 1.0..<2.0:     precipScore = 0.85
    case 2.0..<3.5:     precipScore = 1.00
    case 3.5..<6.0:     precipScore = 0.75
    default:            precipScore = 0.45   // waterlogged
    }

    // Soil temperature score: optimal 45–65°F for morel fruiting
    let soilScore: Double
    if let tf = pt.soilTempF {
        switch tf {
        case ..<32:      soilScore = 0.00
        case 32..<42:    soilScore = 0.20
        case 42..<50:    soilScore = 0.75
        case 50..<60:    soilScore = 1.00
        case 60..<68:    soilScore = 0.65
        case 68..<78:    soilScore = 0.25
        default:         soilScore = 0.05
        }
    } else {
        soilScore = 0.5  // unknown — neutral
    }

    let combined = precipScore * 0.55 + soilScore * 0.45

    let details: [String: String] = [
        "Precipitation": String(format: "%.1f in", inches),
        "Soil Temp":     pt.soilTempF.map { String(format: "%.0f°F", $0) } ?? "N/A",
    ]
    return (combined, details)
}

// MARK: - Internal geometry helper

private func haversineMilesP(_ lat1: Double, _ lon1: Double, _ lat2: Double, _ lon2: Double) -> Double {
    let R = 3958.8
    let dLat = (lat2 - lat1) * .pi / 180
    let dLon = (lon2 - lon1) * .pi / 180
    let a = sin(dLat / 2) * sin(dLat / 2)
        + cos(lat1 * .pi / 180) * cos(lat2 * .pi / 180)
        * sin(dLon / 2) * sin(dLon / 2)
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))
}
