import Foundation

struct NetworkService {
    static let shared = NetworkService()
    private let agent = "MorelSupport/1.0"

    func fetchData(url: URL, timeout: TimeInterval = 15) async -> Data? {
        var req = URLRequest(url: url, timeoutInterval: timeout)
        req.setValue(agent, forHTTPHeaderField: "User-Agent")
        for attempt in 0..<3 {
            do {
                let (data, resp) = try await URLSession.shared.data(for: req)
                if let h = resp as? HTTPURLResponse, h.statusCode == 429, attempt < 2 {
                    try await Task.sleep(for: .seconds(pow(2.0, Double(attempt))))
                    continue
                }
                return data
            } catch { if attempt == 2 { print("[net] \(url.host ?? ""): \(error)") } }
        }
        return nil
    }

    func fetchJSON<T: Decodable>(_ url: URL, as type: T.Type, timeout: TimeInterval = 15) async -> T? {
        guard let data = await fetchData(url: url, timeout: timeout) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }

    func postForm<T: Decodable>(_ url: URL, body: String, as type: T.Type, timeout: TimeInterval = 35) async -> T? {
        var req = URLRequest(url: url, timeoutInterval: timeout)
        req.httpMethod = "POST"
        req.httpBody = body.data(using: .utf8)
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        req.setValue("MushroomMapApp/1.0", forHTTPHeaderField: "User-Agent")
        guard let (data, _) = try? await URLSession.shared.data(for: req) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }

    func postJSON<T: Decodable>(_ url: URL, body: Data, as type: T.Type, timeout: TimeInterval = 25) async -> T? {
        var req = URLRequest(url: url, timeoutInterval: timeout)
        req.httpMethod = "POST"
        req.httpBody = body
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(agent, forHTTPHeaderField: "User-Agent")
        guard let (data, _) = try? await URLSession.shared.data(for: req) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }
}
