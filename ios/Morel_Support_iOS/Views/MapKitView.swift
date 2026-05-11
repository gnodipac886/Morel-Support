import SwiftUI
import MapKit

// MARK: - ProbabilityTileOverlay

/// Renders all probability grid cells as cached 256×256 PNG tiles.
///
/// MapKit calls `loadTile(at:result:)` on background threads for each visible
/// tile; Core Graphics fills only the cells that intersect each tile.  MapKit
/// then GPU-composites the tiles, giving a single draw call per tile instead of
/// one draw call per grid cell.
final class ProbabilityTileOverlay: MKTileOverlay {

    private struct CellRect {
        let mapRect: MKMapRect
        let cell:    ProbabilityCell
    }

    private let cellRects: [CellRect]

    init(cells: [ProbabilityCell]) {
        // Pre-compute MKMapRect for every visible cell once on creation.
        self.cellRects = cells.filter { $0.probability > 20 }.map { cell in
            let b  = cell.bounds
            // MKMapPoint y increases southward, so the NW corner has the smaller y.
            let nw = MKMapPoint(CLLocationCoordinate2D(latitude: b.north, longitude: b.west))
            let se = MKMapPoint(CLLocationCoordinate2D(latitude: b.south, longitude: b.east))
            let rect = MKMapRect(x: nw.x, y: nw.y,
                                 width: se.x - nw.x, height: se.y - nw.y)
            return CellRect(mapRect: rect, cell: cell)
        }
        super.init(urlTemplate: nil)
        canReplaceMapContent = false
    }

    override var boundingMapRect: MKMapRect { .world }

    // Called on a background thread by MapKit for each tile that enters the viewport.
    override func loadTile(at path: MKTileOverlayPath,
                           result: @escaping (Data?, Error?) -> Void) {
        let worldW = MKMapRect.world.width
        let worldH = MKMapRect.world.height
        let n      = pow(2.0, Double(path.z))
        let tileW  = worldW / n
        let tileH  = worldH / n
        let tileRect = MKMapRect(x: Double(path.x) * tileW,
                                 y: Double(path.y) * tileH,
                                 width: tileW, height: tileH)

        let hits = cellRects.filter { tileRect.intersects($0.mapRect) }
        guard !hits.isEmpty else { result(nil, nil); return }

        let tileSize = CGSize(width: 256, height: 256)
        let scaleX   = tileSize.width  / tileW
        let scaleY   = tileSize.height / tileH

        let renderer = UIGraphicsImageRenderer(size: tileSize)
        let img = renderer.image { ctx in
            let cg = ctx.cgContext
            for cr in hits {
                let inter = tileRect.intersection(cr.mapRect)
                guard !inter.isNull else { continue }

                let px = CGFloat((inter.minX - tileRect.minX) * scaleX)
                let py = CGFloat((inter.minY - tileRect.minY) * scaleY)
                let pw = CGFloat(inter.width  * scaleX)
                let ph = CGFloat(inter.height * scaleY)
                let rect = CGRect(x: px, y: py, width: pw, height: ph)

                cg.setFillColor(Self.color(for: cr.cell.probability).cgColor)
                cg.setStrokeColor(UIColor.white.withAlphaComponent(0.15).cgColor)
                cg.setLineWidth(0.5)
                cg.addRect(rect)
                cg.drawPath(using: .fillStroke)
            }
        }
        result(img.pngData(), nil)
    }

    // MARK: Colour ramp
    // Alpha scales linearly with probability: 0.15 at the low end → 0.45 at the top.

    static func color(for probability: Int) -> UIColor {
        let alpha = 0.15 + 0.30 * Double(max(0, probability - 20)) / 80.0
        switch probability {
        case 0..<20:  return .clear
        case 20..<40: return UIColor(red: 0.56, green: 0.93, blue: 0.56, alpha: alpha)
        case 40..<60: return UIColor.systemYellow.withAlphaComponent(alpha)
        case 60..<80: return UIColor.systemOrange.withAlphaComponent(alpha)
        default:      return UIColor.systemRed.withAlphaComponent(alpha)
        }
    }
}

// MARK: - FirePolygon

/// MKPolygon subclass for historic fire perimeters.
final class FirePolygon: MKPolygon {
    var fireName: String = ""
    var fireYear: Int    = 0
}

// MARK: - MapKitView

struct MapKitView: UIViewRepresentable {

    @Binding var region: MKCoordinateRegion
    var result: CalculationResult?
    var onCellTap: (ProbabilityCell) -> Void

    // MARK: makeUIView
    func makeUIView(context: Context) -> MKMapView {
        let mapView = MKMapView()
        mapView.delegate          = context.coordinator
        mapView.setRegion(region, animated: false)
        mapView.showsUserLocation = true
        mapView.mapType           = .mutedStandard

        let tap = UITapGestureRecognizer(
            target: context.coordinator,
            action: #selector(Coordinator.handleTap(_:))
        )
        mapView.addGestureRecognizer(tap)

        return mapView
    }

    // MARK: updateUIView
    func updateUIView(_ mapView: MKMapView, context: Context) {
        // Sync region binding when it drifts.
        let cur = mapView.region
        let tol = 0.0001
        let centerDiff = abs(cur.center.latitude  - region.center.latitude)
                       + abs(cur.center.longitude - region.center.longitude)
        let spanDiff   = abs(cur.span.latitudeDelta  - region.span.latitudeDelta)
                       + abs(cur.span.longitudeDelta - region.span.longitudeDelta)
        if centerDiff > tol || spanDiff > tol {
            mapView.setRegion(region, animated: false)
        }

        let newResult = result
        guard context.coordinator.lastResultID != newResult?.cells.first?.id else { return }
        context.coordinator.lastResultID = newResult?.cells.first?.id

        mapView.removeOverlays(mapView.overlays)
        mapView.removeAnnotations(mapView.annotations)
        context.coordinator.allCells = []

        guard let res = newResult else { return }

        // ── Probability tile overlay (single overlay, tiles rendered off main thread) ──
        let tileOverlay = ProbabilityTileOverlay(cells: res.cells)
        context.coordinator.allCells = res.cells
        mapView.addOverlay(tileOverlay, level: .aboveRoads)

        // ── Fire perimeter polygons ───────────────────────────────────────────────────
        var fireOverlays: [MKOverlay] = []
        for fire in res.overlayFires {
            let geo = fire.geometry
            if geo.type == "Polygon" {
                if let ring = geo.rings.first {
                    let coords  = ring.map { CLLocationCoordinate2D(latitude: $0[1], longitude: $0[0]) }
                    var mutable = coords
                    let poly    = FirePolygon(coordinates: &mutable, count: mutable.count)
                    poly.fireName = fire.name
                    poly.fireYear = fire.year
                    fireOverlays.append(poly)
                }
            } else if geo.type == "MultiPolygon" {
                for polyRings in geo.rings {
                    let coords  = polyRings.map { CLLocationCoordinate2D(latitude: $0[1], longitude: $0[0]) }
                    var mutable = coords
                    let poly    = FirePolygon(coordinates: &mutable, count: mutable.count)
                    poly.fireName = fire.name
                    poly.fireYear = fire.year
                    fireOverlays.append(poly)
                }
            }
        }
        mapView.addOverlays(fireOverlays, level: .aboveRoads)

        // ── iNat observation pins ─────────────────────────────────────────────────────
        for obs in res.overlayInat {
            guard let locStr = obs.location else { continue }
            let parts = locStr.split(separator: ",")
            guard parts.count == 2,
                  let lat = Double(parts[0].trimmingCharacters(in: .whitespaces)),
                  let lon = Double(parts[1].trimmingCharacters(in: .whitespaces))
            else { continue }

            let ann        = MKPointAnnotation()
            ann.coordinate = CLLocationCoordinate2D(latitude: lat, longitude: lon)
            ann.title      = obs.taxon?.name ?? "Morel"
            ann.subtitle   = obs.observedOn
            mapView.addAnnotation(ann)
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    // MARK: - Coordinator

    final class Coordinator: NSObject, MKMapViewDelegate {

        var parent:       MapKitView
        var lastResultID: UUID?
        /// Flat list of all cells — used for tap-to-select without polygon renderers.
        var allCells:     [ProbabilityCell] = []

        init(parent: MapKitView) { self.parent = parent }

        // MARK: Renderers
        func mapView(_ mapView: MKMapView, rendererFor overlay: MKOverlay) -> MKOverlayRenderer {
            if let tileOvl = overlay as? ProbabilityTileOverlay {
                return MKTileOverlayRenderer(tileOverlay: tileOvl)
            }
            if let firePoly = overlay as? FirePolygon {
                let r         = MKPolygonRenderer(polygon: firePoly)
                r.fillColor   = UIColor.systemOrange.withAlphaComponent(0.25)
                r.strokeColor = UIColor.systemRed.withAlphaComponent(0.75)
                r.lineWidth   = 1.0
                return r
            }
            return MKOverlayRenderer(overlay: overlay)
        }

        // MARK: Annotation views
        func mapView(_ mapView: MKMapView,
                     viewFor annotation: MKAnnotation) -> MKAnnotationView? {
            guard !(annotation is MKUserLocation) else { return nil }
            let reuseID = "InatPin"
            let pin = mapView.dequeueReusableAnnotationView(withIdentifier: reuseID)
                      as? MKMarkerAnnotationView
                      ?? MKMarkerAnnotationView(annotation: annotation, reuseIdentifier: reuseID)
            pin.annotation      = annotation
            pin.markerTintColor = .systemGreen
            pin.glyphImage      = UIImage(systemName: "leaf.fill")
            pin.canShowCallout  = true
            return pin
        }

        // MARK: Region sync
        func mapView(_ mapView: MKMapView, regionDidChangeAnimated animated: Bool) {
            let r   = mapView.region
            let tol = 0.0001
            let cd  = abs(r.center.latitude  - parent.region.center.latitude)
                    + abs(r.center.longitude - parent.region.center.longitude)
            let sd  = abs(r.span.latitudeDelta  - parent.region.span.latitudeDelta)
                    + abs(r.span.longitudeDelta - parent.region.span.longitudeDelta)
            if cd > tol || sd > tol {
                DispatchQueue.main.async { [weak self] in self?.parent.region = r }
            }
        }

        // MARK: Tap → cell selection
        @objc func handleTap(_ recognizer: UITapGestureRecognizer) {
            guard let mapView = recognizer.view as? MKMapView else { return }
            let pt    = recognizer.location(in: mapView)
            let coord = mapView.convert(pt, toCoordinateFrom: mapView)
            let lat   = coord.latitude
            let lon   = coord.longitude

            // O(n) bounds scan — no polygon renderer needed.
            for cell in allCells.reversed() {
                let b = cell.bounds
                if lat >= b.south && lat <= b.north && lon >= b.west && lon <= b.east {
                    parent.onCellTap(cell)
                    return
                }
            }
        }
    }
}
