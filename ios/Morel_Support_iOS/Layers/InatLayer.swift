import Foundation

// MARK: - InatLayer

struct InatLayer: Layer {
    let id = "inat"
    let cacheTTL: TimeInterval = 24 * 3600

    func fetch(bounds: Bounds, opts: LayerOptions, grid: [GridCell]) async -> LayerData {
        let quality = opts.str("quality", "research,needs_id")
        var components = URLComponents(string: "https://api.inaturalist.org/v1/observations")!
        components.queryItems = [
            URLQueryItem(name: "taxon_name",    value: "Morchella"),
            URLQueryItem(name: "nelat",         value: String(format: "%.4f", bounds.north)),
            URLQueryItem(name: "nelng",         value: String(format: "%.4f", bounds.east)),
            URLQueryItem(name: "swlat",         value: String(format: "%.4f", bounds.south)),
            URLQueryItem(name: "swlng",         value: String(format: "%.4f", bounds.west)),
            URLQueryItem(name: "per_page",      value: "200"),
            URLQueryItem(name: "quality_grade", value: quality),
            URLQueryItem(name: "order_by",      value: "observed_on"),
            URLQueryItem(name: "order",         value: "desc"),
        ]
        guard let url = components.url else { return .inat([]) }

        struct Response: Decodable {
            let results: [InatObservation]?
        }

        let response = await NetworkService.shared.fetchJSON(url, as: Response.self, timeout: 20)
        let all = response?.results ?? []
        // Filter out obscured or geoprivacy-restricted observations — their coordinates are random offsets
        let filtered = all.filter { obs in
            (obs.obscured ?? false) == false && (obs.geoprivacy == nil || obs.geoprivacy == "open")
        }
        print("[iNat] \(filtered.count) observations (non-obscured)")
        return .inat(filtered)
    }

    func toRawFeatures(data: LayerData) -> [RawFeature] {
        let obs = data.inatObs
        guard !obs.isEmpty else { return [] }

        let calendar = Calendar.current
        let today = Date()
        let currentYear = calendar.component(.year, from: today)

        var features: [RawFeature] = []
        features.reserveCapacity(obs.count)

        let isoFormatter = ISO8601DateFormatter()
        isoFormatter.formatOptions = [.withFullDate]

        for o in obs {
            guard let loc = o.location, !loc.isEmpty else { continue }
            let parts = loc.split(separator: ",")
            guard parts.count >= 2,
                  let lat = Double(parts[0]),
                  let lon = Double(parts[1]) else { continue }

            var yearsAgo = 0
            if let dateStr = o.observedOn, !dateStr.isEmpty,
               let d = isoFormatter.date(from: dateStr) {
                let obsYear = calendar.component(.year, from: d)
                yearsAgo = currentYear - obsYear
            }

            let props: [String: String] = [
                "observed_on":   o.observedOn   ?? "",
                "taxon_name":    o.taxon?.name  ?? "Morchella",
                "quality_grade": o.qualityGrade ?? "",
                "user":          o.user?.login  ?? "",
                "uri":           o.uri          ?? "",
                "years_ago":     "\(yearsAgo)",
            ]
            features.append(RawFeature(geometry: .point(lat: lat, lon: lon), props: props))
        }
        return features
    }
}

// MARK: - Scoring

/// Score a grid cell based on nearby iNaturalist Morchella observations.
///
/// - Parameters:
///   - cellLat: Cell center latitude.
///   - cellLon: Cell center longitude.
///   - observations: The full observation list returned by `InatLayer.fetch`.
///   - options: Layer options; reads `seasonal_weight` (bool, default true).
///   - targetDate: The date the user is planning to forage (drives DOY comparison).
/// - Returns: A score in [0, 1] and the count of observations within 25 miles.
func scoreInat(
    cellLat: Double,
    cellLon: Double,
    observations: [InatObservation],
    options: LayerOptions,
    targetDate: Date
) -> (score: Double, count: Int) {
    guard !observations.isEmpty else { return (0.0, 0) }

    let maxDistMiles = 25.0  // matches Python hardcoded max_dist
    let seasonalWeight = options.bool("seasonal_weight", true)

    let calendar = Calendar.current
    let targetDOY = calendar.ordinality(of: .day, in: .year, for: targetDate) ?? 1
    let targetYear = calendar.component(.year, from: targetDate)

    let isoFormatter = ISO8601DateFormatter()
    isoFormatter.formatOptions = [.withFullDate]

    var best = 0.0
    var count = 0

    for obs in observations {
        guard let loc = obs.location, !loc.isEmpty else { continue }
        let parts = loc.split(separator: ",")
        guard parts.count >= 2,
              let obsLat = Double(parts[0]),
              let obsLon = Double(parts[1]) else { continue }

        let dist = haversineMiles(cellLat, cellLon, obsLat, obsLon)
        guard dist <= maxDistMiles else { continue }
        count += 1

        // Distance score: non-linear decay so closer observations score much higher
        let distScore = max(0.0, 1.0 - pow(dist / maxDistMiles, 0.7))

        var timeScore = 0.5
        if let dateStr = obs.observedOn, !dateStr.isEmpty, seasonalWeight,
           let obsDate = isoFormatter.date(from: dateStr) {
            let obsDOY  = calendar.ordinality(of: .day, in: .year, for: obsDate) ?? 1
            let obsYear = calendar.component(.year, from: obsDate)

            // Circular DOY difference (handles year wrap-around)
            var doyDiff = abs(targetDOY - obsDOY)
            if doyDiff > 183 { doyDiff = 366 - doyDiff }
            // Peak score at same DOY, falls to ~0.05 at 45 days difference
            timeScore = max(0.05, 1.0 - Double(doyDiff) / 45.0)

            // Recency penalty: recent years preferred, older gets down to 25% weight
            let yearsAgo = targetYear - obsYear
            timeScore *= max(0.25, 1.0 - Double(yearsAgo) * 0.08)
        }

        best = max(best, distScore * timeScore)
    }

    // Small density bonus: more nearby sightings increase confidence slightly
    let densityBonus = min(0.15, Double(count) * 0.015)
    return (min(1.0, best + densityBonus), count)
}

