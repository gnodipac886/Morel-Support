import Foundation
import MapKit
import Observation

@Observable
@MainActor
final class MapViewModel {

    // MARK: - Map state
    var region: MKCoordinateRegion = MKCoordinateRegion(
        center: CLLocationCoordinate2D(latitude: 37.5, longitude: -119.5),
        span: MKCoordinateSpan(latitudeDelta: 3, longitudeDelta: 3)
    )

    // MARK: - Layer toggles and weights
    var layerEnabled: [String: Bool] = [
        "inat":      true,
        "precip":    true,
        "fires":     true,
        "trees":     true,
        "season":    true,
        "elevation": true,
        "soil":      true
    ]
    /// Empty dict means use ScoringEngine defaults.
    var weights: [String: Double] = [:]
    /// Per-layer option params, keyed by layer name then param name.
    var layerParams: [String: [String: String]] = [:]

    // MARK: - Settings
    var resolutionMiles: Double = 5.0
    var lookaheadWeeks:  Int    = 0
    var skipUrban:       Bool   = true
    var urbanScale:      Double = 1.4

    // MARK: - State
    var isCalculating: Bool            = false
    var errorMessage:  String?         = nil
    var result:        CalculationResult? = nil
    var progress:      Double          = 0   // 0–1

    // MARK: - Selected cell
    var selectedCell: ProbabilityCell? = nil

    // MARK: - Calculate
    func calculate() async {
        isCalculating = true
        progress      = 0
        errorMessage  = nil

        // 1. Build bounds from current map region.
            let bounds = Bounds(region: region)

            // 2. Create grid.
            var grid = GridService.createGrid(bounds: bounds, resolutionMiles: resolutionMiles)

            // 3. Urban filter (network call, so do it before layer fetches).
            var urbanRemoved = 0
            if skipUrban {
                if let places = await UrbanFilterService.fetchPlaceNodes(bounds: bounds) {
                    let filtered  = UrbanFilterService.filter(grid: grid, places: places, scale: urbanScale)
                    grid          = filtered.cells
                    urbanRemoved  = filtered.removed
                }
            }
            progress = 0.10

            // 4. Ocean filter — remove cells over ocean/sea/large lakes.
            let oceanResult = await OceanFilterService.filter(grid: grid)
            grid = oceanResult.cells
            let oceanRemoved = oceanResult.removed
            progress = 0.20

            // 5. Determine which layers are active.
            let activeKeys = layerEnabled.filter { $0.value }.map(\.key)

            // 6. Fetch all enabled layers concurrently.
            let targetDate = Calendar.current.date(
                byAdding: .weekOfYear,
                value: lookaheadWeeks,
                to: Date()
            ) ?? Date()

            var layerResults: [String: LayerData] = [:]

            await withTaskGroup(of: (String, LayerData).self) { group in
                for key in activeKeys where key != "season" {
                    guard let layer = layerRegistry[key] else { continue }
                    group.addTask {
                        let data = await layer.fetch(
                            bounds: bounds,
                            opts: LayerOptions(enabled: true, params: self.layerParams[key] ?? [:]),
                            grid: grid
                        )
                        return (key, data)
                    }
                }
                for await (key, data) in group {
                    layerResults[key] = data
                }
            }
            progress = 0.55

            // 7. Score each grid cell.
            let effectiveWeights = weights.isEmpty ? defaultWeights : weights
            let layerOptions: [String: LayerOptions] = Dictionary(
                uniqueKeysWithValues: layerEnabled.map { key, enabled in
                    (key, LayerOptions(enabled: enabled, params: layerParams[key] ?? [:]))
                }
            )
            var cells: [ProbabilityCell] = []

            let total = Double(grid.count)
            for (index, cell) in grid.enumerated() {
                let scored = scoreCell(
                    cell:         cell,
                    layerData:    layerResults,
                    layerOptions: layerOptions,
                    weights:      effectiveWeights,
                    targetDate:   targetDate
                )
                cells.append(ProbabilityCell(
                    bounds:      cell.bounds,
                    probability: scored.probability,
                    layerScores: scored.layerScores,
                    details:     scored.details
                ))

                if index % 50 == 0 {
                    let p = 0.55 + 0.40 * (Double(index) / max(total, 1))
                    progress = p
                }
            }
            progress = 0.95

            // 7. Filter overlay data through urban exclusion zones.
            //    inat observations and fire perimeters that fall inside removed urban
            //    areas are not especially useful, but we keep the raw lists here and
            //    let the view decide — the UrbanFilterService only has grid-cell
            //    semantics so we pass them through unmodified.
            let overlayInat  = layerResults["inat"]?.inatObs   ?? []
            let overlayFires = layerResults["fires"]?.fireFeats ?? []

            // 8. Publish result.
            result = CalculationResult(
                cells:           cells,
                targetDate:      targetDate,
                resolutionMiles: resolutionMiles,
                urbanFiltered:   urbanRemoved,
                oceanFiltered:   oceanRemoved,
                overlayInat:     overlayInat,
                overlayFires:    overlayFires,
                rawLayer:        nil,
                rawData:         nil
            )
            progress      = 1.0
            isCalculating = false
    }

    // MARK: - Clear
    func clearResults() {
        result       = nil
        selectedCell = nil
        errorMessage = nil
        progress     = 0
    }
}
