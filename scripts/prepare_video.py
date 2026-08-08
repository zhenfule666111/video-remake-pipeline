#!/usr/bin/env python3
"""Prepare a user-provided video for the video-remake-pipeline skill."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def fail(message: str) -> "NoReturn":
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        fail(f"missing required executable: {name}")
    return path


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def copy_or_download(source: str, source_dir: Path) -> Path:
    source_dir.mkdir(parents=True, exist_ok=True)
    if is_url(source):
        yt_dlp = require_binary("yt-dlp")
        output_template = source_dir / "source.%(ext)s"
        try:
            run(
                [
                    yt_dlp,
                    "--no-playlist",
                    "-f",
                    "bv*+ba/b",
                    "--merge-output-format",
                    "mp4",
                    "-o",
                    str(output_template),
                    source,
                ]
            )
        except subprocess.CalledProcessError as exc:
            fail(
                "video download failed; the link may require login, cookies, payment, "
                f"or unsupported access (exit {exc.returncode})"
            )
        candidates = sorted(source_dir.glob("source.*"))
        if not candidates:
            fail("yt-dlp finished without producing a source video")
        return candidates[0]

    local = Path(source).expanduser().resolve()
    if not local.is_file():
        fail(f"local video does not exist: {local}")
    destination = source_dir / local.name
    if local != destination.resolve():
        shutil.copy2(local, destination)
    return destination


def probe_video(video: Path) -> dict:
    ffprobe = require_binary("ffprobe")
    try:
        result = run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,avg_frame_rate,r_frame_rate,codec_name:format=duration",
                "-of",
                "json",
                str(video),
            ],
            capture=True,
        )
    except subprocess.CalledProcessError as exc:
        fail(f"ffprobe could not read the video: {exc.stderr.strip()}")
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    if not streams:
        fail("no video stream found")
    stream = streams[0]
    try:
        duration = float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        fail("video duration is unavailable")
    if not math.isfinite(duration) or duration <= 0:
        fail("video duration must be positive")
    return {
        "durationSeconds": round(duration, 3),
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "codec": stream.get("codec_name"),
        "averageFrameRate": stream.get("avg_frame_rate") or stream.get("r_frame_rate"),
    }


def frame_times(duration: float, interval: float, max_frames: int) -> list[float]:
    count = int(math.floor(max(duration - 0.001, 0) / interval)) + 1
    if count > max_frames:
        fail(
            f"sampling every {interval:g}s would create {count} frames, above the "
            f"safety limit of {max_frames}; increase --interval or --max-frames"
        )
    return [round(index * interval, 3) for index in range(count)]


def timestamp_label(seconds: float, width: int) -> str:
    if float(seconds).is_integer():
        return f"{int(seconds):0{width}d}s"
    value = f"{seconds:.3f}".rstrip("0").rstrip(".").replace(".", "p")
    return f"{value}s"


def extract_frames(video: Path, frames_dir: Path, times: list[float], duration: float) -> list[dict]:
    ffmpeg = require_binary("ffmpeg")
    frames_dir.mkdir(parents=True, exist_ok=True)
    width = max(2, len(str(max(0, int(duration)))))
    frames: list[dict] = []
    for index, timestamp in enumerate(times, start=1):
        label = timestamp_label(timestamp, width)
        output = frames_dir / f"frame-{label}.jpg"
        try:
            run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(video),
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    "-y",
                    str(output),
                ]
            )
        except subprocess.CalledProcessError as exc:
            fail(f"frame extraction failed at {timestamp:g}s (exit {exc.returncode})")
        if not output.is_file():
            fail(f"missing extracted frame: {output}")
        frames.append(
            {
                "shot": index,
                "timestampSeconds": timestamp,
                "sourceFrame": str(output.resolve()),
                "sceneName": None,
                "durationSeconds": 4,
                "generatedImage": None,
            }
        )
    return frames


def make_contact_sheet(frames_dir: Path, output: Path, frame_count: int) -> None:
    ffmpeg = require_binary("ffmpeg")
    columns = 4 if frame_count >= 4 else frame_count
    rows = math.ceil(frame_count / columns)
    try:
        run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-pattern_type",
                "glob",
                "-i",
                str(frames_dir / "frame-*.jpg"),
                "-vf",
                f"scale=480:-2,tile={columns}x{rows}:padding=8:margin=8:color=white",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-y",
                str(output),
            ]
        )
    except subprocess.CalledProcessError as exc:
        fail(f"contact sheet generation failed (exit {exc.returncode})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download/copy a video, probe it, and extract deterministic interval frames."
    )
    parser.add_argument("input", help="Public http(s) URL or local video path")
    parser.add_argument("--output-dir", required=True, help="Run directory to create or reuse")
    parser.add_argument("--interval", type=float, default=2.0, help="Sampling interval in seconds")
    parser.add_argument("--max-frames", type=int, default=60, help="Safety limit for extracted frames")
    parser.add_argument("--force", action="store_true", help="Allow replacing generated files")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.interval <= 0:
        fail("--interval must be positive")
    if args.max_frames <= 0:
        fail("--max-frames must be positive")

    output_dir = Path(args.output_dir).expanduser().resolve()
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists() and not args.force:
        fail(f"manifest already exists; use a unique directory or --force: {manifest_path}")
    output_dir.mkdir(parents=True, exist_ok=True)

    video = copy_or_download(args.input, output_dir / "source")
    probe = probe_video(video)
    times = frame_times(probe["durationSeconds"], args.interval, args.max_frames)
    frames = extract_frames(video, output_dir / "frames", times, probe["durationSeconds"])
    contact_sheet = output_dir / "contact-sheet.jpg"
    make_contact_sheet(output_dir / "frames", contact_sheet, len(frames))

    manifest = {
        "schemaVersion": 1,
        "input": args.input,
        "sourceVideo": str(video.resolve()),
        "samplingIntervalSeconds": args.interval,
        "video": probe,
        "frameCount": len(frames),
        "contactSheet": str(contact_sheet.resolve()),
        "frames": frames,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "runDirectory": str(output_dir),
                "manifest": str(manifest_path),
                "contactSheet": str(contact_sheet),
                "frameCount": len(frames),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
