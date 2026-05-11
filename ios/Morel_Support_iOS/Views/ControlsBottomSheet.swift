import SwiftUI

// MARK: - ControlsBottomSheet

struct ControlsBottomSheet: View {

    @Binding var layerEnabled:    [String: Bool]
    @Binding var weights:         [String: Double]
    @Binding var resolutionMiles: Double
    @Binding var lookaheadWeeks:  Int
    @Binding var skipUrban:       Bool
    @Binding var urbanScale:      Double
    @Binding var layerParams:     [String: [String: String]]

    var isCalculating: Bool
    var onCalculate:   () -> Void
    var onClear:       () -> Void

    // MARK: - Expand/Collapse State

    @State private var expandedLayers: Set<String> = []

    // MARK: - Private constants

    private let layerOrder = ["inat", "precip", "fires", "trees", "season", "elevation", "soil"]
    /// Layers that have no configurable settings — no dropdown shown.
    private let noSettingsLayers: Set<String> = ["season", "elevation", "soil"]

    private let layerLabels: [String: String] = [
        "inat":      "iNaturalist",
        "precip":    "Precipitation",
        "fires":     "Fire History",
        "trees":     "Host Trees",
        "season":    "Season",
        "elevation": "Elevation",
        "soil":      "Soil"
    ]

    private let layerIcons: [String: String] = [
        "inat":      "binoculars",
        "precip":    "cloud.rain",
        "fires":     "flame",
        "trees":     "leaf",
        "season":    "calendar",
        "elevation": "mountain.2",
        "soil":      "square.3.layers.3d"
    ]

    private let layerColors: [String: Color] = [
        "inat":      .green,
        "precip":    .blue,
        "fires":     .orange,
        "trees":     Color(red: 0.55, green: 0.35, blue: 0.10),
        "season":    .yellow,
        "elevation": Color(white: 0.55),
        "soil":      Color(red: 0.76, green: 0.60, blue: 0.42)
    ]

    // How many layers are currently enabled.
    private var enabledCount: Int {
        layerEnabled.values.filter { $0 }.count
    }

    // MARK: - Body

    var body: some View {
        ZStack {
            Color(.systemBackground)
                .ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    // Drag handle.
                    HStack { Spacer(); dragHandle; Spacer() }
                        .padding(.top, 8)
                        .padding(.bottom, 4)

                    // Title.
                    Text("Morel Forecast")
                        .font(.title2.bold())
                        .foregroundStyle(Color(red: 0.88, green: 0.80, blue: 0.62))
                        .padding(.horizontal, 20)
                        .padding(.bottom, 16)

                    // ── Resolution ───────────────────────────────────────────
                    sectionHeader("Grid Resolution")
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text("Cell size")
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Text(String(format: "%.0f mi", resolutionMiles))
                                .font(.subheadline.monospacedDigit())
                                .foregroundStyle(accentColor)
                        }
                        Slider(value: $resolutionMiles, in: 1...20, step: 0.5)
                            .tint(accentColor)
                    }
                    .cardStyle()

                    // ── Lookahead ─────────────────────────────────────────────
                    sectionHeader("Forecast Date")
                    HStack {
                        Text(lookaheadWeeks == 0 ? "Today" : "In \(lookaheadWeeks) week\(lookaheadWeeks == 1 ? "" : "s")")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        Spacer()
                        Stepper("", value: $lookaheadWeeks, in: 0...8)
                            .labelsHidden()
                            .tint(accentColor)
                    }
                    .cardStyle()

                    // ── Urban filter ──────────────────────────────────────────
                    sectionHeader("Urban Filter")
                    VStack(alignment: .leading, spacing: 10) {
                        Toggle(isOn: $skipUrban) {
                            Label("Skip urban areas", systemImage: "building.2")
                                .font(.subheadline)
                                .foregroundStyle(.primary)
                        }
                        .tint(accentColor)

                        if skipUrban {
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text("Exclusion radius scale")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                    Spacer()
                                    Text(String(format: "%.1fx", urbanScale))
                                        .font(.caption.monospacedDigit())
                                        .foregroundStyle(accentColor)
                                }
                                Slider(value: $urbanScale, in: 0.5...3.0, step: 0.1)
                                    .tint(accentColor)
                            }
                            .transition(.opacity.combined(with: .move(edge: .top)))
                        }
                    }
                    .cardStyle()
                    .animation(.easeInOut(duration: 0.2), value: skipUrban)

                    // ── Layers ────────────────────────────────────────────────
                    sectionHeader("Layers")
                    VStack(spacing: 0) {
                        ForEach(layerOrder, id: \.self) { key in
                            layerRow(key: key)
                            if key != layerOrder.last {
                                Divider()
                                    .background(Color.primary.opacity(0.08))
                                    .padding(.leading, 48)
                            }
                        }
                    }
                    .background(cardBackground)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .padding(.horizontal, 16)
                    .padding(.bottom, 4)

                    // ── Pie weight chart ───────────────────────────────────────
                    if enabledCount >= 2 {
                        sectionHeader("Weight Distribution")
                        VStack(spacing: 8) {
                            PieWeightChart(weights: $weights, layerEnabled: layerEnabled)
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 8)

                            Text("Drag slice boundaries to adjust weights")
                                .font(.caption2)
                                .foregroundStyle(.tertiary)
                        }
                        .cardStyle()
                        .transition(.opacity)
                        .animation(.easeInOut, value: enabledCount)
                    }

                    // ── Action buttons ─────────────────────────────────────────
                    VStack(spacing: 10) {
                        // Calculate button.
                        Button(action: onCalculate) {
                            HStack(spacing: 8) {
                                if isCalculating {
                                    ProgressView()
                                        .progressViewStyle(.circular)
                                        .tint(.black)
                                        .scaleEffect(0.85)
                                } else {
                                    Image(systemName: "magnifyingglass.circle.fill")
                                        .font(.headline)
                                }
                                Text(isCalculating ? "Calculating…" : "Calculate")
                                    .font(.headline)
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(
                                isCalculating
                                    ? Color(red: 0.40, green: 0.30, blue: 0.15)
                                    : accentColor
                            )
                            .foregroundStyle(.black)
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                        }
                        .disabled(isCalculating)

                        // Clear button.
                        Button(action: onClear) {
                            Text("Clear Results")
                                .font(.subheadline)
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 11)
                                .background(Color.primary.opacity(0.08))
                                .foregroundStyle(.secondary)
                                .clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                        .disabled(isCalculating)
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 8)
                    .padding(.bottom, 32)
                }
            }
        }
    }

    // MARK: - Subviews

    private var dragHandle: some View {
        Capsule()
            .fill(Color.white.opacity(0.25))
            .frame(width: 36, height: 4)
    }

    @ViewBuilder
    private func sectionHeader(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.caption.bold())
            .tracking(1.2)
            .foregroundStyle(Color(red: 0.88, green: 0.80, blue: 0.62).opacity(0.65))
            .padding(.horizontal, 20)
            .padding(.top, 16)
            .padding(.bottom, 4)
    }

    @ViewBuilder
    private func layerRow(key: String) -> some View {
        let isExpanded = expandedLayers.contains(key)
        let hasSettings = !noSettingsLayers.contains(key)

        VStack(spacing: 0) {
            // ── Row header ────────────────────────────────────────────────
            Button {
                if hasSettings {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        if expandedLayers.contains(key) {
                            expandedLayers.remove(key)
                        } else {
                            expandedLayers.insert(key)
                        }
                    }
                }
            } label: {
                HStack(spacing: 12) {
                    // Icon with layer colour.
                    ZStack {
                        RoundedRectangle(cornerRadius: 7)
                            .fill((layerColors[key] ?? .gray).opacity(0.25))
                            .frame(width: 32, height: 32)
                        Image(systemName: layerIcons[key] ?? "circle")
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundStyle(layerColors[key] ?? .gray)
                    }

                    Text(layerLabels[key] ?? key)
                        .font(.subheadline)
                        .foregroundStyle(.primary)

                    Spacer()

                    if hasSettings {
                        Image(systemName: "chevron.right")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                            .rotationEffect(.degrees(isExpanded ? 90 : 0))
                            .animation(.easeInOut(duration: 0.2), value: isExpanded)
                    }

                    Toggle("", isOn: Binding(
                        get:  { layerEnabled[key] ?? false },
                        set: { layerEnabled[key] = $0 }
                    ))
                    .labelsHidden()
                    .tint(accentColor)
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 9)
                .background(Color.clear)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            // ── Expandable settings panel ─────────────────────────────────
            if hasSettings && isExpanded {
                layerSettingsPanel(key: key)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
    }

    @ViewBuilder
    private func layerSettingsPanel(key: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            switch key {

            // ── iNaturalist ───────────────────────────────────────────────
            case "inat":
                let qualityBinding = layerParamBinding(key: key, param: "quality", default: "research,needs_id")
                VStack(alignment: .leading, spacing: 6) {
                    Text("Observation quality")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Picker("Quality", selection: qualityBinding) {
                        Text("Research grade only").tag("research")
                        Text("Research + Needs ID").tag("research,needs_id")
                    }
                    .pickerStyle(.segmented)
                    .tint(accentColor)
                }

                Toggle(isOn: Binding(
                    get: { (layerParams["inat"]?["seasonal_weight"] ?? "true") == "true" },
                    set: { layerParamBinding(key: key, param: "seasonal_weight", default: "true").wrappedValue = $0 ? "true" : "false" }
                )) {
                    Text("Seasonal weighting")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .tint(accentColor)

            // ── Precipitation ─────────────────────────────────────────────
            case "precip":
                let windowBinding = layerParamBinding(key: key, param: "time_window", default: "14")
                VStack(alignment: .leading, spacing: 6) {
                    Text("Rain lookback")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Picker("Rain lookback", selection: windowBinding) {
                        Text("7 days").tag("7")
                        Text("14 days").tag("14")
                        Text("21 days").tag("21")
                    }
                    .pickerStyle(.segmented)
                    .tint(accentColor)
                }

            // ── Fire History ──────────────────────────────────────────────
            case "fires":
                let yearsBack = intParam(key: key, param: "years_back", default: 3)
                let ignoreCurrentYear = Binding<Bool>(
                    get: { (layerParams[key]?["ignore_current_year"] ?? "false") == "true" },
                    set: { layerParamBinding(key: key, param: "ignore_current_year", default: "false").wrappedValue = $0 ? "true" : "false" }
                )

                HStack {
                    Text("Years back")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Text("\(yearsBack.wrappedValue) yr")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(accentColor)
                    Stepper("", value: yearsBack, in: 1...10)
                        .labelsHidden()
                        .tint(accentColor)
                }

                Toggle(isOn: ignoreCurrentYear) {
                    Text("Ignore current year")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .tint(accentColor)

            // ── Host Trees ────────────────────────────────────────────────
            case "trees":
                let burnList: [(key: String, label: String)] = [
                    ("douglas_fir", "Douglas Fir"),
                    ("pine",        "Pine"),
                    ("white_fir",   "White Fir")
                ]
                let nonBurnList: [(key: String, label: String)] = [
                    ("ash",          "Ash"),
                    ("elm",          "Elm"),
                    ("tulip_poplar", "Tulip Poplar"),
                    ("cottonwood",   "Cottonwood"),
                    ("sycamore",     "Sycamore"),
                    ("apple",        "Apple")
                ]

                VStack(alignment: .leading, spacing: 10) {
                    Text("Burn species")
                        .font(.caption.bold())
                        .foregroundStyle(accentColor)
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6), count: 3), spacing: 6) {
                        ForEach(burnList, id: \.key) { item in
                            speciesToggle(key: key, item: item)
                        }
                    }

                    Text("Non-burn species")
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)
                        .padding(.top, 2)
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6), count: 3), spacing: 6) {
                        ForEach(nonBurnList, id: \.key) { item in
                            speciesToggle(key: key, item: item)
                        }
                    }
                }

            // ── Season ────────────────────────────────────────────────────
            case "season":
                EmptyView()

            // ── Elevation ─────────────────────────────────────────────────
            case "elevation":
                EmptyView()

            // ── Soil ──────────────────────────────────────────────────────
            case "soil":
                EmptyView()

            default:
                EmptyView()
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .padding(.leading, 12)
        .background(Color.primary.opacity(0.06))
    }

    // MARK: - Style helpers

    private var accentColor: Color {
        Color(red: 0.78, green: 0.62, blue: 0.30)  // earthy amber/gold
    }

    private var cardBackground: Color {
        Color(.secondarySystemBackground)
    }

    // MARK: - Param binding helpers

    private func layerParamBinding(key: String, param: String, default defaultVal: String) -> Binding<String> {
        Binding(
            get: { layerParams[key]?[param] ?? defaultVal },
            set: {
                var d = layerParams[key] ?? [:]
                d[param] = $0
                layerParams[key] = d
            }
        )
    }

    private func doubleParam(key: String, param: String, default defaultVal: Double) -> Binding<Double> {
        Binding(
            get: { Double(layerParams[key]?[param] ?? "") ?? defaultVal },
            set: {
                var d = layerParams[key] ?? [:]
                d[param] = String($0)
                layerParams[key] = d
            }
        )
    }

    private func intParam(key: String, param: String, default defaultVal: Int) -> Binding<Int> {
        Binding(
            get: { Int(layerParams[key]?[param] ?? "") ?? defaultVal },
            set: {
                var d = layerParams[key] ?? [:]
                d[param] = String($0)
                layerParams[key] = d
            }
        )
    }

    @ViewBuilder
    private func speciesToggle(key: String, item: (key: String, label: String)) -> some View {
        let binding = speciesBinding(key: key, species: item.key)
        Button { binding.wrappedValue.toggle() } label: {
            HStack(spacing: 4) {
                Image(systemName: binding.wrappedValue ? "checkmark.square.fill" : "square")
                    .font(.caption)
                    .foregroundStyle(binding.wrappedValue ? accentColor : .secondary)
                Text(item.label)
                    .font(.caption2)
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .buttonStyle(.plain)
    }

    private func speciesBinding(key: String, species: String) -> Binding<Bool> {
        Binding(
            get: {
                // If param absent → all enabled
                guard let raw = layerParams[key]?["species"], !raw.isEmpty else { return true }
                return raw.split(separator: ",").map(String.init).contains(species)
            },
            set: { enabled in
                let all = ["ash","elm","tulip_poplar","cottonwood","sycamore","apple","douglas_fir","white_fir","pine"]
                var current: Set<String>
                if let raw = layerParams[key]?["species"], !raw.isEmpty {
                    current = Set(raw.split(separator: ",").map(String.init))
                } else {
                    current = Set(all) // was "all", now toggling one
                }
                if enabled { current.insert(species) } else { current.remove(species) }
                // If all enabled, store empty string (means "all")
                var d = layerParams[key] ?? [:]
                d["species"] = current.count == all.count ? "" : current.sorted().joined(separator: ",")
                layerParams[key] = d
            }
        )
    }
}

// MARK: - ViewModifier for card styling

private struct CardStyleModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(14)
            .background(Color(.secondarySystemBackground))
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .padding(.horizontal, 16)
            .padding(.bottom, 4)
    }
}

private extension View {
    func cardStyle() -> some View {
        modifier(CardStyleModifier())
    }
}

// MARK: - Preview

#Preview {
    ControlsBottomSheet(
        layerEnabled: .constant([
            "inat": true, "precip": true, "fires": true, "trees": true,
            "season": true, "elevation": false, "soil": true
        ]),
        weights:         .constant([:]),
        resolutionMiles: .constant(5.0),
        lookaheadWeeks:  .constant(0),
        skipUrban:       .constant(true),
        urbanScale:      .constant(1.4),
        layerParams:     .constant([:]),
        isCalculating:   false,
        onCalculate:     {},
        onClear:         {}
    )
    .frame(height: 700)
}
