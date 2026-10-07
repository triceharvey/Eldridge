// Eldridge AVFoundation frame extractor v1. Local-only; writes to the supplied temp directory.
import AVFoundation
import Foundation
import ImageIO
import UniformTypeIdentifiers

guard CommandLine.arguments.count >= 5 else {
    fputs("usage: extract_video_frames <video> <output-dir> <milliseconds...>\n", stderr)
    exit(2)
}
let videoURL = URL(fileURLWithPath: CommandLine.arguments[1])
let outputURL = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
let rawTimes = CommandLine.arguments.dropFirst(3)
guard rawTimes.count >= 2, rawTimes.count <= 24,
      rawTimes.allSatisfy({ UInt32($0) != nil }),
      FileManager.default.fileExists(atPath: videoURL.path),
      FileManager.default.fileExists(atPath: outputURL.path) else {
    fputs("invalid extractor arguments\n", stderr)
    exit(2)
}
let asset = AVURLAsset(url: videoURL)
let generator = AVAssetImageGenerator(asset: asset)
generator.appliesPreferredTrackTransform = true
generator.requestedTimeToleranceBefore = .zero
generator.requestedTimeToleranceAfter = .zero
do {
    for rawTime in rawTimes {
        let requestedMs = Int(rawTime)!
        let time = CMTime(value: CMTimeValue(requestedMs), timescale: 1000)
        var actual = CMTime.zero
        let frame = try generator.copyCGImage(at: time, actualTime: &actual)
        let filename = String(format: "frame-%06d.png", requestedMs)
        let destinationURL = outputURL.appendingPathComponent(filename) as CFURL
        guard let destination = CGImageDestinationCreateWithURL(
            destinationURL, UTType.png.identifier as CFString, 1, nil
        ) else { throw NSError(domain: "EldridgeExtractor", code: 1) }
        CGImageDestinationAddImage(destination, frame, nil)
        guard CGImageDestinationFinalize(destination) else {
            throw NSError(domain: "EldridgeExtractor", code: 2)
        }
        print("\(requestedMs) \(Int((CMTimeGetSeconds(actual) * 1000).rounded()))")
    }
} catch {
    fputs("frame decode failed: \(error)\n", stderr)
    exit(1)
}
