import CoreLocation
import MapKit

struct Bounds: Sendable {
    let north, south, east, west: Double

    init(north: Double, south: Double, east: Double, west: Double) {
        self.north = north; self.south = south; self.east = east; self.west = west
    }

    init(region: MKCoordinateRegion) {
        let c = region.center; let s = region.span
        north = c.latitude  + s.latitudeDelta  / 2
        south = c.latitude  - s.latitudeDelta  / 2
        east  = c.longitude + s.longitudeDelta / 2
        west  = c.longitude - s.longitudeDelta / 2
    }

    var centerLat: Double { (north + south) / 2 }
    var centerLon: Double { (east  + west)  / 2 }
    var center: CLLocationCoordinate2D {
        CLLocationCoordinate2D(latitude: centerLat, longitude: centerLon)
    }
}
