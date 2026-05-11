import Foundation

// MARK: - SeasonLayer

struct SeasonLayer: Layer {
    let id = "season"
    let cacheTTL: TimeInterval = 3600

    /// Season has no external data to fetch — scoring is purely date + location.
    func fetch(bounds: Bounds, opts: LayerOptions, grid: [GridCell]) async -> LayerData {
        return .none
    }

    func toRawFeatures(data: LayerData) -> [RawFeature] {
        return []
    }
}

// MARK: - Scoring

/// Estimate morel probability based on target date vs. the typical seasonal window for a given latitude.
///
/// The peak DOY is latitude-adjusted: ~DOY 70 at 30°N, ~DOY 120 at 50°N.
/// Within 25 days of peak a Gaussian-style score is returned; beyond 55 days out the season
/// is essentially closed.
///
/// - Parameters:
///   - lat: Cell center latitude (degrees north).
///   - targetDate: The date the user intends to forage.
/// - Returns: A score in [0.05, 1.0].
func scoreSeasonality(lat: Double, targetDate: Date) -> Double {
    let calendar = Calendar.current
    let doy = Double(calendar.ordinality(of: .day, in: .year, for: targetDate) ?? 1)

    // Peak DOY: ~70 at 30°N, ~120 at 50°N (2.5 days per degree of latitude)
    let peakDOY = 70.0 + (lat - 30.0) * 2.5
    let window  = 25.0
    let diff    = abs(doy - peakDOY)

    if diff < window {
        return max(0.30, 1.0 - pow(diff / window, 1.5))
    } else if diff < 55.0 {
        return max(0.05, 0.40 * (1.0 - (diff - window) / 30.0))
    } else {
        return 0.05
    }
}
