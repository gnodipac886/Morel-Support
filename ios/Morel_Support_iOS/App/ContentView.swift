import SwiftUI
import MapKit
import Observation

// MARK: - ContentView

struct ContentView: View {
    @State private var viewModel = MapViewModel()
    @State private var showSettings = false

    var body: some View {
        ZStack(alignment: .bottom) {

            // ── Full-screen map ───────────────────────────────────────────────
            MapKitView(
                region: $viewModel.region,
                result: viewModel.result,
                onCellTap: { cell in viewModel.selectedCell = cell }
            )
            .ignoresSafeArea()

            // ── Progress bar ─────────────────────────────────────────────────
            if viewModel.isCalculating {
                VStack(spacing: 0) {
                    ProgressView(value: viewModel.progress)
                        .progressViewStyle(LinearProgressViewStyle(tint: .green))
                        .frame(height: 4)
                    Spacer()
                }
                .ignoresSafeArea(edges: .top)
            }

            // ── Error snackbar ────────────────────────────────────────────────
            if let errorMessage = viewModel.errorMessage {
                VStack {
                    HStack(spacing: 8) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundColor(.yellow)
                        Text(errorMessage)
                            .font(.footnote)
                            .foregroundColor(.white)
                            .lineLimit(2)
                        Spacer()
                        Button {
                            viewModel.errorMessage = nil
                        } label: {
                            Image(systemName: "xmark")
                                .foregroundColor(.white.opacity(0.8))
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 12)
                    .background(Color.red.opacity(0.92))
                    .cornerRadius(10)
                    .padding(.horizontal, 16)
                    .padding(.top, 60)
                    Spacer()
                }
                .transition(.move(edge: .top).combined(with: .opacity))
                .animation(.easeInOut(duration: 0.3), value: viewModel.errorMessage)
                .zIndex(10)
            }

            // ── Top bar ───────────────────────────────────────────────────────
            VStack {
                HStack {
                    Text("🍄 Morel Support")
                        .font(.headline)
                        .fontWeight(.semibold)
                        .foregroundColor(.primary)
                    Spacer()
                    Button {
                        showSettings = true
                    } label: {
                        Image(systemName: "gearshape.fill")
                            .font(.title3)
                            .foregroundColor(.primary)
                            .padding(8)
                            .background(.ultraThinMaterial)
                            .clipShape(Circle())
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(.ultraThinMaterial)
                .cornerRadius(14)
                .padding(.horizontal, 16)
                .padding(.top, 8)
                Spacer()
            }
            .zIndex(5)

            // ── Bottom sheet ─────────────────────────────────────────────────
            BottomSheetView(viewModel: viewModel)
                .padding(.horizontal, 12)
                .padding(.bottom, 12)
            .zIndex(4)
        }
        .ignoresSafeArea(edges: .bottom)
        // ── Cell detail sheet ────────────────────────────────────────────────
        .sheet(item: $viewModel.selectedCell) { cell in
            CellDetailSheet(cell: cell)
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
        }
        .sheet(isPresented: $showSettings) {
            let vm = Bindable(viewModel)
            ControlsBottomSheet(
                layerEnabled:    vm.layerEnabled,
                weights:         vm.weights,
                resolutionMiles: vm.resolutionMiles,
                lookaheadWeeks:  vm.lookaheadWeeks,
                skipUrban:       vm.skipUrban,
                urbanScale:      vm.urbanScale,
                layerParams:     vm.layerParams,
                isCalculating:   viewModel.isCalculating,
                onCalculate:     { Task { await viewModel.calculate() } },
                onClear:         { viewModel.clearResults() }
            )
            .presentationDetents([.medium, .large])
            .presentationDragIndicator(.visible)
        }
    }
}

// MARK: - BottomSheetView

private struct BottomSheetView: View {
    var viewModel: MapViewModel

    var body: some View {
        VStack(spacing: 0) {
            CollapsedSheetContent(viewModel: viewModel)

            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity)
        .frame(height: 160)
        .background(.ultraThinMaterial)
        .cornerRadius(20)
        .shadow(color: .black.opacity(0.15), radius: 8, y: -2)
    }
}

// MARK: - CollapsedSheetContent

private struct CollapsedSheetContent: View {
    var viewModel: MapViewModel

    private let layerOrder: [(key: String, label: String, icon: String)] = [
        ("inat",      "iNat",      "leaf.fill"),
        ("precip",    "Rain",      "cloud.rain.fill"),
        ("fires",     "Fires",     "flame.fill"),
        ("trees",     "Trees",     "tree.fill"),
        ("season",    "Season",    "calendar"),
        ("elevation", "Elevation", "mountain.2.fill"),
        ("soil",      "Soil",      "square.3.layers.3d")
    ]

    var body: some View {
        VStack(spacing: 10) {
            // Calculate button
            Button {
                Task { await viewModel.calculate() }
            } label: {
                HStack(spacing: 6) {
                    if viewModel.isCalculating {
                        ProgressView()
                            .progressViewStyle(CircularProgressViewStyle(tint: .white))
                            .scaleEffect(0.8)
                    } else {
                        Image(systemName: "wand.and.stars")
                    }
                    Text(viewModel.isCalculating ? "Calculating…" : "Calculate")
                        .fontWeight(.semibold)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .background(viewModel.isCalculating ? Color.gray : Color.green.opacity(0.85))
                .foregroundColor(.white)
                .cornerRadius(12)
            }
            .disabled(viewModel.isCalculating)
            .padding(.horizontal, 16)

            // Layer toggle chips — two rows
            let row1 = Array(layerOrder.prefix(4))
            let row2 = Array(layerOrder.dropFirst(4))
            VStack(spacing: 6) {
                HStack(spacing: 8) {
                    ForEach(row1, id: \.key) { item in
                        chip(item).frame(maxWidth: .infinity)
                    }
                }
                HStack(spacing: 8) {
                    ForEach(row2, id: \.key) { item in
                        chip(item).frame(maxWidth: .infinity)
                    }
                }
            }
            .padding(.horizontal, 16)
        }
        .padding(.top, 16)
        .padding(.bottom, 6)
    }

    @ViewBuilder
    private func chip(_ item: (key: String, label: String, icon: String)) -> some View {
        LayerChip(
            label: item.label,
            icon: item.icon,
            isOn: viewModel.layerEnabled[item.key] ?? true
        ) {
            viewModel.layerEnabled[item.key] = !(viewModel.layerEnabled[item.key] ?? true)
        }
    }
}

// MARK: - LayerChip

private struct LayerChip: View {
    let label: String
    let icon: String
    let isOn: Bool
    let toggle: () -> Void

    var body: some View {
        Button(action: toggle) {
            HStack(spacing: 4) {
                Image(systemName: icon)
                    .font(.caption2)
                Text(label)
                    .font(.caption)
                    .fontWeight(.medium)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(isOn ? Color.green.opacity(0.85) : Color.secondary.opacity(0.18))
            .foregroundColor(isOn ? .white : .secondary)
            .cornerRadius(20)
        }
    }
}

// MARK: - CellDetailSheet

struct CellDetailSheet: View {
    let cell: ProbabilityCell

    private var probabilityColor: Color {
        switch cell.probability {
        case 75...100: return .green
        case 50..<75:  return .yellow
        case 25..<50:  return .orange
        default:       return .red
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {

                    // ── Probability header ────────────────────────────────────
                    HStack {
                        Spacer()
                        VStack(spacing: 4) {
                            Text("\(cell.probability)%")
                                .font(.system(size: 64, weight: .bold, design: .rounded))
                                .foregroundColor(probabilityColor)
                            Text("Morel Probability")
                                .font(.subheadline)
                                .foregroundColor(.secondary)
                        }
                        Spacer()
                    }
                    .padding(.top, 8)

                    Divider()

                    // ── Layer scores ─────────────────────────────────────────
                    if !cell.layerScores.isEmpty {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Layer Scores")
                                .font(.headline)
                                .padding(.horizontal, 16)

                            ForEach(cell.layerScores.sorted(by: { $0.key < $1.key }), id: \.key) { key, score in
                                LayerScoreRow(key: key, score: score)
                            }
                        }

                        Divider()
                    }

                    // ── Details ───────────────────────────────────────────────
                    if !cell.details.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Details")
                                .font(.headline)
                                .padding(.horizontal, 16)

                            ForEach(cell.details.sorted(by: { $0.key < $1.key }), id: \.key) { key, value in
                                HStack {
                                    Text(key.replacingOccurrences(of: "_", with: " ").capitalized)
                                        .font(.subheadline)
                                        .foregroundColor(.secondary)
                                    Spacer()
                                    Text(value)
                                        .font(.subheadline)
                                        .fontWeight(.medium)
                                }
                                .padding(.horizontal, 16)
                                .padding(.vertical, 4)
                            }
                        }

                        Divider()
                    }

                    // ── Coordinates ───────────────────────────────────────────
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Location")
                            .font(.headline)
                        let lat = (cell.bounds.south + cell.bounds.north) / 2
                        let lon = (cell.bounds.west  + cell.bounds.east)  / 2
                        Text(String(format: "%.4f°, %.4f°", lat, lon))
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                    }
                    .padding(.horizontal, 16)
                    .padding(.bottom, 24)
                }
            }
            .navigationTitle("Cell Details")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

// MARK: - LayerScoreRow

private struct LayerScoreRow: View {
    let key: String
    let score: Int

    private var barColor: Color {
        switch score {
        case 75...100: return .green
        case 50..<75:  return .yellow
        case 25..<50:  return .orange
        default:       return .red
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(key.replacingOccurrences(of: "_", with: " ").capitalized)
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                Spacer()
                Text("\(score)")
                    .font(.subheadline)
                    .fontWeight(.semibold)
                    .foregroundColor(barColor)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 4)
                        .fill(Color.secondary.opacity(0.15))
                        .frame(height: 8)
                    RoundedRectangle(cornerRadius: 4)
                        .fill(barColor)
                        .frame(width: geo.size.width * CGFloat(max(0, min(100, score))) / 100,
                               height: 8)
                }
            }
            .frame(height: 8)
        }
        .padding(.horizontal, 16)
    }
}

// MARK: - Corner Radius Helper

extension View {
    func cornerRadius(_ radius: CGFloat, corners: UIRectCorner) -> some View {
        clipShape(RoundedCorner(radius: radius, corners: corners))
    }
}

private struct RoundedCorner: Shape {
    var radius: CGFloat
    var corners: UIRectCorner

    func path(in rect: CGRect) -> Path {
        let path = UIBezierPath(
            roundedRect: rect,
            byRoundingCorners: corners,
            cornerRadii: CGSize(width: radius, height: radius)
        )
        return Path(path.cgPath)
    }
}
