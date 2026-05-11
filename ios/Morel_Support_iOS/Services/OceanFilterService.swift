import Foundation

// MARK: - OceanFilterService

/// Removes grid cells that fall over ocean, sea, or tidal water by querying
/// ETOPO1 global elevation for every cell centre via the opentopodata.org API.
///
/// ETOPO1 (1 arc-minute, ~1.8 km) returns negative values for ocean/seafloor.
/// Cells at or below +5 m are excluded to also catch tidal flats and shallow
/// estuaries whose MSL elevation is technically positive but are still water.
///
/// Cells for which the API returns no data are kept conservatively.
enum OceanFilterService {

    /// ETOPO1 accepts up to 100 locations per POST request.
    private static let batchSize = 100
    /// One request at a time — opentopodata.org free tier is ~1 req/s.
    private static let seaLevelThreshold = 5.0   // metres

    // MARK: - Public

    static func filter(grid: [GridCell]) async -> (cells: [GridCell], removed: Int) {
        guard !grid.isEmpty else { return (grid, 0) }

        let elevations = await fetchElevations(grid: grid)

        var filtered: [GridCell] = []
        var removed = 0
        for (i, cell) in grid.enumerated() {
            if let elev = elevations[i], elev < seaLevelThreshold {
                removed += 1
            } else {
                filtered.append(cell)
            }
        }

        print("[ocean] \(removed)/\(grid.count) ocean/tidal cells removed (ETOPO1)")
        return (filtered, removed)
    }

    // MARK: - Private

    /// Fetches ETOPO1 elevation for every cell sequentially in batches.
    private static func fetchElevations(grid: [GridCell]) async -> [Double?] {
        let count      = grid.count
        let batchCount = (count + batchSize - 1) / batchSize

        var results = [Double?](repeating: nil, count: count)

        for b in 0..<batchCount {
            let start = b * batchSize
            let end   = min(start + batchSize, count)
            let slice = Array(grid[start..<end])
            let elevs = await fetchBatch(cells: slice)
            for (j, e) in elevs.enumerated() {
                results[start + j] = e
            }
        }

        return results
    }

    /// POSTs up to 100 cell centres to ETOPO1; returns elevations in order.
    private static func fetchBatch(cells: [GridCell]) async -> [Double?] {
        guard let url = URL(string: "https://api.opentopodata.org/v1/etopo1") else {
            return [Double?](repeating: nil, count: cells.count)
        }

        let locStr = cells
            .map { String(format: "%.5f,%.5f", $0.centerLat, $0.centerLon) }
            .joined(separator: "|")

        struct Result:   Decodable { let elevation: Double? }
        struct Response: Decodable { let results: [Result]? }

        let resp = await NetworkService.shared.postForm(
            url,
            body: "locations=\(locStr)",
            as: Response.self,
            timeout: 30
        )
        let arr = resp?.results ?? []
        return (0..<cells.count).map { i in i < arr.count ? arr[i].elevation : nil }
    }
}
