import Foundation

struct CellBounds: Sendable, Hashable {
    let south, north, west, east: Double
}

struct GridCell: Sendable {
    let centerLat: Double
    let centerLon: Double
    let bounds: CellBounds
}
