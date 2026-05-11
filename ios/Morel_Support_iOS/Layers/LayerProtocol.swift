import Foundation

protocol Layer: Sendable {
    var id: String { get }
    var cacheTTL: TimeInterval { get }
    func fetch(bounds: Bounds, opts: LayerOptions, grid: [GridCell]) async -> LayerData
    func toRawFeatures(data: LayerData) -> [RawFeature]
}

struct LayerOptions: Sendable {
    var enabled: Bool = false
    var params: [String: String] = [:]
    func str(_ k: String, _ def: String = "") -> String { params[k] ?? def }
    func int(_ k: String, _ def: Int = 0)    -> Int    { Int(params[k] ?? "") ?? def }
    func bool(_ k: String, _ def: Bool = false) -> Bool {
        guard let v = params[k] else { return def }
        return v == "true" || v == "1"
    }
    func double(_ k: String, _ def: Double = 0.0) -> Double {
        guard let v = params[k] else { return def }
        return Double(v) ?? def
    }
}

struct RawFeature: Sendable {
    enum Geo: Sendable {
        case point(lat: Double, lon: Double)
        case polygon(rings: [[[Double]]])
    }
    let geometry: Geo
    let props: [String: String]
}
