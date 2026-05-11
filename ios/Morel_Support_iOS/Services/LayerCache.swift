import Foundation

actor LayerCache {
    static let shared = LayerCache()
    private struct Entry { let data: Any; let lat, lon: Double; let at: Date }
    private var store: [String: Entry] = [:]
    private let maxMiles = 75.0

    func get(key: String, bounds: Bounds, ttl: TimeInterval) -> Any? {
        guard let e = store[key] else { return nil }
        let age  = Date().timeIntervalSince(e.at)
        let dist = haversineMiles(bounds.centerLat, bounds.centerLon, e.lat, e.lon)
        if age > ttl || dist > maxMiles { store.removeValue(forKey: key); return nil }
        return e.data
    }

    func set(key: String, bounds: Bounds, data: Any) {
        store[key] = Entry(data: data, lat: bounds.centerLat, lon: bounds.centerLon, at: Date())
    }
}
