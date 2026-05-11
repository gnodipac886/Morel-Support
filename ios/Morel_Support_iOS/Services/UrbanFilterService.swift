import Foundation

enum UrbanFilterService {
    private struct OResp: Decodable {
        let elements: [OEl]
        struct OEl: Decodable { let lat: Double?; let lon: Double?; let tags: [String: String]? }
    }

    static func fetchPlaceNodes(bounds: Bounds) async -> [UrbanPlace]? {
        let pad  = 0.15
        let bbox = "\(bounds.south-pad),\(bounds.west-pad),\(bounds.north+pad),\(bounds.east+pad)"
        let q    = "[out:json][timeout:30];\n(\n  node[\"place\"~\"^(city|town)$\"](\(bbox));\n);\nout body;\n"
        guard let url = URL(string: "https://overpass-api.de/api/interpreter") else { return nil }
        guard let resp = await NetworkService.shared.postForm(url, body: q, as: OResp.self) else { return nil }
        var places: [UrbanPlace] = []
        for el in resp.elements {
            guard let lat = el.lat, let lon = el.lon else { continue }
            let tags  = el.tags ?? [:]
            let place = tags["place"] ?? ""
            let pop   = Int(tags["population"] ?? "0") ?? 0
            var r: Double
            if place == "city" {
                if      pop > 500_000 { r = 6.0 }
                else if pop > 100_000 { r = 4.0 }
                else if pop > 50_000  { r = 2.5 }
                else                  { r = 1.5 }
            } else {
                if pop > 0 && pop < 15_000 { continue }
                r = pop > 20_000 ? 1.2 : 0.8
            }
            places.append(UrbanPlace(lat: lat, lon: lon, name: tags["name"] ?? "", radiusMiles: r))
        }
        print("[urban] \(places.count) place nodes")
        return places
    }

    static func filter(grid: [GridCell], places: [UrbanPlace], scale: Double) -> (cells: [GridCell], removed: Int) {
        let before   = grid.count
        let filtered = grid.filter { cell in
            !places.contains { p in haversineMiles(cell.centerLat, cell.centerLon, p.lat, p.lon) <= p.radiusMiles * scale }
        }
        return (filtered, before - filtered.count)
    }
}
