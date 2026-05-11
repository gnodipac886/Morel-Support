import Foundation

enum GridService {
    static func createGrid(bounds: Bounds, resolutionMiles: Double) -> [GridCell] {
        let n = bounds.north, s = bounds.south, e = bounds.east, w = bounds.west
        let clat    = (n + s) / 2
        let latStep = resolutionMiles / 69.0
        let lonStep = resolutionMiles / (69.0 * cos(clat * .pi / 180) + 1e-9)
        var cells: [GridCell] = []
        var lat = s
        while lat < n {
            var lon = w
            while lon < e {
                let cn = min(lat + latStep, n)
                let ce = min(lon + lonStep, e)
                cells.append(GridCell(
                    centerLat: (lat + cn) / 2,
                    centerLon: (lon + ce) / 2,
                    bounds: CellBounds(south: lat, north: cn, west: lon, east: ce)
                ))
                lon += lonStep
            }
            lat += latStep
        }
        return cells
    }
}
