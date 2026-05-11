import Foundation

// ── Associated value types ─────────────────────────────────────────────────────

struct InatObservation: Sendable, Decodable {
    let location: String?
    let obscured: Bool?
    let geoprivacy: String?
    let user: InatUser?
    let taxon: InatTaxon?
    let uri: String?
    let observedOn: String?
    let qualityGrade: String?
    enum CodingKeys: String, CodingKey {
        case location, obscured, geoprivacy, user, taxon, uri
        case observedOn  = "observed_on"
        case qualityGrade = "quality_grade"
    }
}
struct InatUser:  Sendable, Decodable { let login: String? }
struct InatTaxon: Sendable, Decodable { let name:  String? }

struct PrecipPoint: Sendable {
    let lat, lon: Double
    let bounds: CellBounds?
    let precipIn: Double
    let snowIn: Double
    let soilTempF: Double?
    let source: String
}

struct FireFeature: Sendable {
    let name: String
    let year: Int
    struct Geo: Sendable {
        let type: String          // "Polygon" | "MultiPolygon"
        let rings: [[[Double]]]   // normalised so always [ring] for Polygon, [poly][ring] for Multi

        var allPoints: [[Double]] { rings.flatMap { $0 } }
    }
    let geometry: Geo
}

struct TreeObservation: Sendable {
    let lat, lon: Double
    let species: String
    let source: String
}

struct ElevationPoint: Sendable {
    let lat, lon: Double
    let elevationM: Double?
}

struct SoilPoint: Sendable {
    let lat, lon: Double
    let ph: Double?
    let clay: Double?
}

struct UrbanPlace: Sendable {
    let lat, lon: Double
    let name: String
    let radiusMiles: Double
}

// ── Enum ───────────────────────────────────────────────────────────────────────

enum LayerData: Sendable {
    case none
    case inat([InatObservation])
    case precip([PrecipPoint])
    case fires([FireFeature])
    case trees([TreeObservation])
    case elevation([ElevationPoint])
    case soil([SoilPoint])

    var inatObs:    [InatObservation] { if case .inat(let v)      = self { return v }; return [] }
    var precipPts:  [PrecipPoint]     { if case .precip(let v)    = self { return v }; return [] }
    var fireFeats:  [FireFeature]     { if case .fires(let v)     = self { return v }; return [] }
    var treeObs:    [TreeObservation] { if case .trees(let v)     = self { return v }; return [] }
    var elevPts:    [ElevationPoint]  { if case .elevation(let v) = self { return v }; return [] }
    var soilPts:    [SoilPoint]       { if case .soil(let v)      = self { return v }; return [] }
}
