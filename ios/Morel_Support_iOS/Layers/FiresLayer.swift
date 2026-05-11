import Foundation

// MARK: - FiresLayer

struct FiresLayer: Layer {
    let id = "fires"
    let cacheTTL: TimeInterval = 12 * 3600

    func fetch(bounds: Bounds, opts: LayerOptions, grid: [GridCell]) async -> LayerData {
        let yearsBack = opts.int("years_back", 3)
        let ignoreCurrentYear = opts.bool("ignore_current_year", false)

        let calendar = Calendar.current
        let currentYear = calendar.component(.year, from: Date())
        let minYear = currentYear - max(1, yearsBack)

        let geomEnvelope = "\(bounds.west),\(bounds.south),\(bounds.east),\(bounds.north)"

        // Shared ESRI query parameters
        let baseParams: [String: String] = [
            "geometry":          geomEnvelope,
            "geometryType":      "esriGeometryEnvelope",
            "inSR":              "4326",
            "outSR":             "4326",
            "spatialRel":        "esriSpatialRelIntersects",
            "resultRecordCount": "200",
            "returnGeometry":    "true",
            "outFields":         "*",
            "where":             "1=1",
            "f":                 "json",
        ]

        // ── Endpoint definitions ───────────────────────────────────────────────
        struct EndpointDef {
            let url: URL
            let label: String
            let nameFields: [String]
            let tsFields: [String]
            let yearField: String?
        }

        var endpoints: [EndpointDef] = []

        // WFIGS YTD (current season) — most up-to-date perimeters
        let wfigNameFields = ["poly_IncidentName", "attr_IncidentName", "IncidentName", "INCIDENTNAME"]
        let wfigTSFields   = ["poly_PolygonDateTime", "poly_DateCurrent",
                              "attr_FireDiscoveryDateTime", "FireDiscoveryDateTime", "DISCOVERYDATETIME"]
        let wfigServices = [
            "WFIGS_Interagency_Perimeters_YTD",
            "WFIGS_Interagency_Perimeters",
        ]
        for svc in wfigServices {
            let urlStr = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/\(svc)/FeatureServer/0/query"
            if let url = buildURL(urlStr, params: baseParams) {
                endpoints.append(EndpointDef(url: url, label: "WFIGS/\(svc)",
                                             nameFields: wfigNameFields, tsFields: wfigTSFields, yearField: nil))
            }
        }

        // USFS EDW historic perimeters — covers multi-year history
        let usfsEndpoints: [(String, String, [String], String?)] = [
            ("https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_FirePerimeterHistoric_01/MapServer/0/query",
             "USFS/EDW_FirePerimeterHistoric", ["FIRE_NAME"], "FIRE_YEAR"),
            ("https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_MTBS_01/MapServer/0/query",
             "USFS/EDW_MTBS", ["Fire_Name", "FIRE_NAME"], "Year"),
        ]
        for (urlStr, label, nameFs, yrField) in usfsEndpoints {
            if let url = buildURL(urlStr, params: baseParams) {
                endpoints.append(EndpointDef(url: url, label: label,
                                             nameFields: nameFs, tsFields: [], yearField: yrField))
            }
        }

        // ── Concurrent fetch ───────────────────────────────────────────────────
        let rawFires: [FireFeature] = await withTaskGroup(of: [FireFeature].self) { group in
            for ep in endpoints {
                group.addTask {
                    await Self.fetchESRIEndpoint(ep.url, label: ep.label,
                                                 nameFields: ep.nameFields,
                                                 tsFields: ep.tsFields,
                                                 yearField: ep.yearField,
                                                 minYear: minYear)
                }
            }
            var all: [FireFeature] = []
            for await chunk in group { all.append(contentsOf: chunk) }
            return all
        }

        // Deduplicate by name + year
        var seen = Set<String>()
        var unique: [FireFeature] = []
        for fire in rawFires {
            let key = "\(fire.name)|\(fire.year)"
            if seen.insert(key).inserted {
                unique.append(fire)
            }
        }

        // Optionally drop fires from the current calendar year (e.g. still burning)
        if ignoreCurrentYear {
            let before = unique.count
            unique = unique.filter { $0.year != currentYear }
            print("[fires] dropped \(before - unique.count) current-year features")
        }

        print("[fires] \(unique.count) perimeters after dedup (minYear=\(minYear))")
        return .fires(unique)
    }

    func toRawFeatures(data: LayerData) -> [RawFeature] {
        data.fireFeats.map { fire in
            let props: [String: String] = [
                "name": fire.name,
                "year": "\(fire.year)",
            ]
            return RawFeature(geometry: .polygon(rings: fire.geometry.rings), props: props)
        }
    }

    // MARK: - Private ESRI fetch

    private static func fetchESRIEndpoint(
        _ url: URL,
        label: String,
        nameFields: [String],
        tsFields: [String],
        yearField: String?,
        minYear: Int
    ) async -> [FireFeature] {
        struct ESRIResponse: Decodable {
            struct Feature: Decodable {
                let attributes: [String: JSONValue]?
                let geometry:   ESRIGeometry?
            }
            struct ESRIGeometry: Decodable {
                let rings: [[JSONPoint]]?
            }
            let features: [Feature]?
            let error:    ESRIError?
        }
        struct ESRIError: Decodable {
            let message: String?
        }
        // Use a loose Any-like wrapper so we can handle mixed attribute types
        enum JSONValue: Decodable {
            case string(String), number(Double), null
            init(from decoder: Decoder) throws {
                let c = try decoder.singleValueContainer()
                if c.decodeNil()               { self = .null; return }
                if let v = try? c.decode(Double.self) { self = .number(v); return }
                if let v = try? c.decode(String.self) { self = .string(v); return }
                self = .null
            }
            var string: String? { if case .string(let s) = self { return s }; return nil }
            var double: Double? { if case .number(let d) = self { return d }; return nil }
        }
        // ArcGIS ring coordinates are [lon, lat] pairs
        struct JSONPoint: Decodable {
            let values: [Double]
            init(from decoder: Decoder) throws {
                var c = try decoder.unkeyedContainer()
                var arr: [Double] = []
                while !c.isAtEnd { arr.append(try c.decode(Double.self)) }
                values = arr
            }
        }

        guard let resp = await NetworkService.shared.fetchJSON(url, as: ESRIResponse.self, timeout: 20) else {
            print("[fires] \(label): fetch returned nil")
            return []
        }
        if let err = resp.error {
            print("[fires] \(label): API error: \(err.message ?? "unknown")")
            return []
        }

        let currentYear = Calendar.current.component(.year, from: Date())
        var fires: [FireFeature] = []

        for feat in resp.features ?? [] {
            let attrs = feat.attributes ?? [:]

            // Resolve year
            var year = currentYear
            if let yf = yearField, let val = attrs[yf] {
                if let d = val.double { year = Int(d) }
                else if let s = val.string, let i = Int(s) { year = i }
            } else {
                // Fall back to timestamp fields (ms since epoch)
                for tf in tsFields {
                    if let val = attrs[tf], let ms = val.double, ms > 0 {
                        let ts = Date(timeIntervalSince1970: ms / 1000)
                        year = Calendar.current.component(.year, from: ts)
                        break
                    }
                }
            }
            guard year >= minYear else { continue }

            // Resolve name
            let name = nameFields.compactMap { attrs[$0]?.string }.first(where: { !$0.isEmpty }) ?? ""

            // Parse ESRI rings → FireFeature.Geo
            guard let esriRings = feat.geometry?.rings, !esriRings.isEmpty else { continue }
            let rings: [[[Double]]] = esriRings.map { ring in ring.map { $0.values } }

            let geoType = rings.count == 1 ? "Polygon" : "MultiPolygon"
            let geo = FireFeature.Geo(type: geoType, rings: rings)
            fires.append(FireFeature(name: name, year: year, geometry: geo))
        }

        print("[fires] \(label): \(fires.count) features (minYear=\(minYear))")
        return fires
    }
}

// MARK: - Scoring

/// Score a grid cell for post-fire morel habitat suitability.
///
/// - Parameters:
///   - cellLat: Cell center latitude.
///   - cellLon: Cell center longitude.
///   - cellBounds: The cell's geographic extent for overlap testing.
///   - fires: The `[FireFeature]` array from `FiresLayer.fetch`.
///   - options: Layer options (reserved for future tuning).
/// - Returns: A score in [0, 1] and the count of overlapping fire perimeters.
func scoreFires(
    cellLat: Double,
    cellLon: Double,
    cellBounds: CellBounds,
    fires: [FireFeature],
    options: LayerOptions
) -> (score: Double, count: Int) {
    guard !fires.isEmpty else { return (0.0, 0) }

    let currentYear  = Calendar.current.component(.year, from: Date())
    var best  = 0.0
    var count = 0

    for fire in fires {
        let yearsAgo = currentYear - fire.year

        // Post-fire age bonus: peak at 1–2 years after the burn,
        // still valuable up to ~5 years, diminishing thereafter
        let ageScore: Double
        switch yearsAgo {
        case ..<0:   ageScore = 0.40   // future-dated / data artefact
        case 0:      ageScore = 0.70   // same year (still burning or just contained)
        case 1:      ageScore = 1.00   // peak morel year
        case 2:      ageScore = 0.85
        case 3:      ageScore = 0.60
        case 4:      ageScore = 0.35
        default:     ageScore = 0.15   // very old burn
        }

        // Collect all ring points to derive an axis-aligned bounding box
        let allPts = fire.geometry.allPoints
        guard !allPts.isEmpty else { continue }

        let fireSouth = allPts.map { $0[1] }.min()!
        let fireNorth = allPts.map { $0[1] }.max()!
        let fireWest  = allPts.map { $0[0] }.min()!
        let fireEast  = allPts.map { $0[0] }.max()!

        // AABB overlap check between cell and fire bounding box
        let overlaps = cellBounds.south < fireNorth && cellBounds.north > fireSouth
                    && cellBounds.west  < fireEast  && cellBounds.east  > fireWest

        let score: Double
        if overlaps {
            score = ageScore
            count += 1
        } else {
            // Distance to nearest point on the fire bounding box
            let nearestLat = max(fireSouth, min(fireNorth, cellLat))
            let nearestLon = max(fireWest,  min(fireEast,  cellLon))
            let dist = haversineMilesF(cellLat, cellLon, nearestLat, nearestLon)
            // Score falls off linearly to zero at 20 miles from the perimeter edge
            score = ageScore * max(0.0, 1.0 - dist / 20.0)
        }

        best = max(best, score)
    }

    return (min(1.0, best), count)
}

// MARK: - Internal helpers

private func buildURL(_ base: String, params: [String: String]) -> URL? {
    var comps = URLComponents(string: base)
    comps?.queryItems = params.map { URLQueryItem(name: $0.key, value: $0.value) }
    return comps?.url
}

private func haversineMilesF(_ lat1: Double, _ lon1: Double, _ lat2: Double, _ lon2: Double) -> Double {
    let R = 3958.8
    let dLat = (lat2 - lat1) * .pi / 180
    let dLon = (lon2 - lon1) * .pi / 180
    let a = sin(dLat / 2) * sin(dLat / 2)
        + cos(lat1 * .pi / 180) * cos(lat2 * .pi / 180)
        * sin(dLon / 2) * sin(dLon / 2)
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))
}
