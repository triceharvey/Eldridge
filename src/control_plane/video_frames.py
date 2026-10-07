"""Reproduce PNG samples from a local MP4 with the versioned macOS decoder."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from control_plane.video_trial import VideoFrameSample

EXTRACTOR = Path(__file__).with_name("extract_video_frames.swift")


def verify_frame_derivation(video: Path, samples: tuple[VideoFrameSample, ...]) -> dict[str, Any]:
    """Fail closed unless every exact-timestamp decoded PNG matches its bound digest."""

    swift = shutil.which("swift")
    if sys.platform != "darwin" or swift is None or not EXTRACTOR.is_file():
        raise ValueError("macOS Swift AVFoundation frame extractor is unavailable")
    extractor_digest = f"sha256:{sha256(EXTRACTOR.read_bytes()).hexdigest()}"
    with tempfile.TemporaryDirectory(prefix="eldridge-frames-") as temporary:
        output = Path(temporary)
        try:
            run = subprocess.run(  # noqa: S603 - fixed local executable and bundled script
                [
                    swift,
                    str(EXTRACTOR),
                    str(video),
                    str(output),
                    *(str(sample.timestamp_ms) for sample in samples),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("frame extraction timed out") from exc
        if run.returncode != 0:
            raise ValueError("frame extraction failed")
        lines = run.stdout.splitlines()
        if len(lines) != len(samples):
            raise ValueError("frame extraction returned an unexpected sample count")
        results = []
        for sample, line in zip(samples, lines, strict=True):
            parts = line.split()
            if len(parts) != 2 or parts[0] != str(sample.timestamp_ms):
                raise ValueError("frame extraction returned an unexpected timestamp")
            try:
                actual_ms = int(parts[1])
            except ValueError as exc:
                raise ValueError("frame extraction returned an invalid timestamp") from exc
            if abs(actual_ms - sample.timestamp_ms) > 50:
                raise ValueError("decoded frame timestamp exceeds 50 ms tolerance")
            frame = output / f"frame-{sample.timestamp_ms:06d}.png"
            if not frame.is_file() or frame.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("extracted frame missing or exceeds size boundary")
            digest = f"sha256:{sha256(frame.read_bytes()).hexdigest()}"
            if digest != sample.sha256:
                raise ValueError("extracted frame digest does not match archived sample")
            results.append({"requested_ms": sample.timestamp_ms, "actual_ms": actual_ms})
    return {
        "backend": "macOS AVFoundation PNG v1",
        "extractor_sha256": extractor_digest,
        "timestamps": results,
    }
