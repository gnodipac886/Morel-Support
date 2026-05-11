import SwiftUI

// MARK: - PieWeightChart

struct PieWeightChart: View {

    @Binding var weights:      [String: Double]
    let layerEnabled: [String: Bool]

    private let layerOrder = ["inat", "precip", "fires", "trees", "season", "elevation", "soil"]

    private let layerColors: [String: Color] = [
        "inat":      .green,
        "precip":    .blue,
        "fires":     .orange,
        "trees":     Color(red: 0.55, green: 0.35, blue: 0.10),
        "season":    .yellow,
        "elevation": Color(white: 0.55),
        "soil":      Color(red: 0.76, green: 0.60, blue: 0.42)
    ]

    private let layerLabels: [String: String] = [
        "inat":      "iNat",
        "precip":    "Precip",
        "fires":     "Fires",
        "trees":     "Trees",
        "season":    "Season",
        "elevation": "Elev",
        "soil":      "Soil"
    ]

    // Active (enabled) layers in the defined order.
    private var activeLayers: [String] {
        layerOrder.filter { layerEnabled[$0] == true }
    }

    // Current weight slice for each active layer (sum = 1.0).
    private var slices: [String: Double] {
        normalised(weights: weights)
    }

    // Cumulative start angles (radians, 0 = top, clockwise).
    private func angles(in size: CGSize) -> [(key: String, start: Double, end: Double)] {
        let keys = activeLayers
        let w    = slices
        var result: [(key: String, start: Double, end: Double)] = []
        var cumulative = -Double.pi / 2  // start at 12 o'clock
        for key in keys {
            let fraction = w[key] ?? 0
            let sweep    = 2 * .pi * fraction
            result.append((key: key, start: cumulative, end: cumulative + sweep))
            cumulative += sweep
        }
        return result
    }

    // MARK: - Drag state

    /// Index into `activeLayers` of the boundary being dragged (boundary i lies between slice i-1 and slice i).
    @State private var draggingBoundary: Int? = nil
    @State private var dragStartWeights: [String: Double] = [:]

    // MARK: - Body

    var body: some View {
        let layers = activeLayers
        guard layers.count >= 2 else {
            return AnyView(
                Text("Enable at least 2 layers to adjust weights.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity)
            )
        }

        return AnyView(
            VStack(spacing: 12) {
                GeometryReader { geo in
                    let size   = geo.size
                    let side   = min(size.width, size.height)
                    let radius = side / 2
                    let center = CGPoint(x: size.width / 2, y: size.height / 2)
                    let holeR  = radius * 0.45
                    let sliceAngles = angles(in: size)

                    ZStack {
                        // Draw slices via Canvas for performance.
                        Canvas { ctx, _ in
                            for entry in sliceAngles {
                                let color = layerColors[entry.key] ?? .gray
                                var path  = Path()
                                path.move(to: center)
                                path.addArc(
                                    center:    center,
                                    radius:    radius,
                                    startAngle: .radians(entry.start),
                                    endAngle:   .radians(entry.end),
                                    clockwise:  false
                                )
                                path.closeSubpath()
                                ctx.fill(path, with: .color(color.opacity(0.85)))

                                // Slice border.
                                ctx.stroke(path, with: .color(.black.opacity(0.25)), lineWidth: 1)
                            }

                            // Donut hole.
                            var hole = Path()
                            hole.addArc(
                                center:     center,
                                radius:     holeR,
                                startAngle: .radians(0),
                                endAngle:   .radians(2 * .pi),
                                clockwise:  false
                            )
                            ctx.fill(hole, with: .color(Color(uiColor: .systemBackground)))
                        }
                        .frame(width: side, height: side)

                        // Percentage labels inside slices.
                        ForEach(sliceAngles, id: \.key) { entry in
                            let mid     = (entry.start + entry.end) / 2
                            let labelR  = (radius + holeR) / 2
                            let lx      = center.x + labelR * cos(mid)
                            let ly      = center.y + labelR * sin(mid)
                            let pct     = Int(((slices[entry.key] ?? 0) * 100).rounded())
                            if (entry.end - entry.start) > 0.25 {  // only if slice is big enough
                                VStack(spacing: 1) {
                                    Text(layerLabels[entry.key] ?? entry.key)
                                        .font(.system(size: 9, weight: .semibold))
                                    Text("\(pct)%")
                                        .font(.system(size: 8))
                                }
                                .foregroundStyle(.white)
                                .shadow(color: .black.opacity(0.6), radius: 1, x: 0, y: 0)
                                .position(x: lx, y: ly)
                            }
                        }
                    }
                    .contentShape(Circle())
                    .gesture(
                        DragGesture(minimumDistance: 2)
                            .onChanged { value in
                                handleDragChanged(
                                    value:       value,
                                    center:      center,
                                    sliceAngles: sliceAngles,
                                    layers:      layers
                                )
                            }
                            .onEnded { _ in
                                draggingBoundary = nil
                                dragStartWeights = [:]
                            }
                    )
                }
                .aspectRatio(1, contentMode: .fit)
                .frame(maxWidth: 260)

                // Legend.
                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 80))],
                    spacing: 4
                ) {
                    ForEach(activeLayers, id: \.self) { key in
                        HStack(spacing: 4) {
                            Circle()
                                .fill(layerColors[key] ?? .gray)
                                .frame(width: 8, height: 8)
                            Text(layerLabels[key] ?? key)
                                .font(.caption2)
                                .foregroundStyle(.primary)
                        }
                    }
                }
            }
        )
    }

    // MARK: - Drag handling

    private func handleDragChanged(
        value:       DragGesture.Value,
        center:      CGPoint,
        sliceAngles: [(key: String, start: Double, end: Double)],
        layers:      [String]
    ) {
        let pt       = value.location
        let dx       = pt.x - center.x
        let dy       = pt.y - center.y
        var angle    = atan2(dy, dx)  // -π .. π

        // Normalise to -π/2 .. 3π/2 to match our 12-o'clock start convention.
        if angle < -Double.pi / 2 {
            angle += 2 * .pi
        }

        if draggingBoundary == nil {
            // Find which boundary (between slices) is closest to the tap angle.
            // Boundary i is the start angle of slice i (for i > 0).
            var bestIdx  = -1
            var bestDist = Double.infinity
            for (i, entry) in sliceAngles.enumerated() where i > 0 {
                let dist = abs(angleDiff(a: angle, b: entry.start))
                if dist < bestDist && dist < 0.35 {
                    bestDist = dist
                    bestIdx  = i
                }
            }
            guard bestIdx > 0 else { return }
            draggingBoundary = bestIdx
            dragStartWeights = normalised(weights: weights)
        }

        guard let boundary = draggingBoundary, boundary > 0 else { return }

        // The two slices around this boundary are [boundary-1] and [boundary].
        let leftKey  = layers[boundary - 1]
        let rightKey = layers[boundary]

        // Combined budget for the two neighbouring slices.
        let baseL    = dragStartWeights[leftKey]  ?? (1.0 / Double(layers.count))
        let baseR    = dragStartWeights[rightKey] ?? (1.0 / Double(layers.count))
        let budget   = baseL + baseR

        // The drag angle relative to the boundary between these two slices.
        let startBoundaryAngle = sliceAngles[boundary].start
        let totalArc           = 2 * .pi * budget
        guard totalArc > 0 else { return }

        var arcOffset = angleDiff(a: angle, b: startBoundaryAngle)
        arcOffset     = max(-totalArc * 0.95, min(totalArc * 0.95, arcOffset))

        let newLeftFraction  = (baseL + arcOffset / (2 * .pi))
            .clamped(to: 0.02...max(0.02, budget - 0.02))
        let newRightFraction = budget - newLeftFraction

        var updated = normalised(weights: weights)
        updated[leftKey]  = newLeftFraction
        updated[rightKey] = newRightFraction

        // Write back as raw values; normalisation is done on read.
        weights = updated
    }

    // MARK: - Helpers

    /// Returns a weight dict where every active layer has a value summing to 1.0.
    private func normalised(weights raw: [String: Double]) -> [String: Double] {
        let keys = activeLayers
        guard !keys.isEmpty else { return [:] }

        var w: [String: Double] = [:]
        for key in keys {
            w[key] = max(0.01, raw[key] ?? (1.0 / Double(keys.count)))
        }
        let total = w.values.reduce(0, +)
        if total > 0 {
            for key in keys { w[key] = (w[key] ?? 0) / total }
        }
        return w
    }

    /// Signed difference between two angles, result in -π .. π.
    private func angleDiff(a: Double, b: Double) -> Double {
        var d = a - b
        while d >  .pi { d -= 2 * .pi }
        while d < -.pi { d += 2 * .pi }
        return d
    }
}

// MARK: - Comparable clamping helper

private extension Comparable {
    func clamped(to range: ClosedRange<Self>) -> Self {
        min(max(self, range.lowerBound), range.upperBound)
    }
}
