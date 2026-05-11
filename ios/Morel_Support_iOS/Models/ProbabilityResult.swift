import Foundation

struct ProbabilityCell: Identifiable, Sendable {
    let id = UUID()
    let bounds: CellBounds
    let probability: Int          // 0–100
    let layerScores: [String: Int]
    let details: [String: String]
}

struct CalculationResult: Sendable {
    let cells: [ProbabilityCell]
    let targetDate: Date
    let resolutionMiles: Double
    let urbanFiltered: Int
    let oceanFiltered: Int
    let overlayInat: [InatObservation]
    let overlayFires: [FireFeature]
    let rawLayer: String?         // nil = probability mode
    let rawData: LayerData?
}
