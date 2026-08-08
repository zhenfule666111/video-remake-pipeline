#!/usr/bin/env python3
"""Validate a video-remake-pipeline run directory without external packages."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a video remake run directory")
    parser.add_argument("run_dir")
    parser.add_argument("--require-generated", action="store_true")
    parser.add_argument("--require-prompts", action="store_true")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    errors: list[str] = []
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        errors.append(f"missing manifest: {manifest_path}")
        report(errors)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frames = manifest.get("frames") or []
    if manifest.get("frameCount") != len(frames):
        errors.append("manifest frameCount does not match frames length")
    if not frames:
        errors.append("manifest contains no frames")

    seen_shots: set[int] = set()
    for expected, frame in enumerate(frames, start=1):
        shot = frame.get("shot")
        if shot != expected:
            errors.append(f"shot order mismatch at index {expected}: {shot}")
        if shot in seen_shots:
            errors.append(f"duplicate shot number: {shot}")
        seen_shots.add(shot)
        source_frame = Path(frame.get("sourceFrame") or "")
        if not source_frame.is_file():
            errors.append(f"missing source frame for shot {shot}: {source_frame}")
        generated = frame.get("generatedImage")
        if args.require_generated and not generated:
            errors.append(f"shot {shot} has no generatedImage")
        if generated and not Path(generated).is_file():
            errors.append(f"missing generated image for shot {shot}: {generated}")

    contact_sheet = Path(manifest.get("contactSheet") or "")
    if not contact_sheet.is_file():
        errors.append(f"missing contact sheet: {contact_sheet}")

    if args.require_prompts:
        for filename in ("image-prompts-zh.md", "video-prompts-storyboard-zh.md"):
            document = run_dir / filename
            if not document.is_file():
                errors.append(f"missing prompt document: {document}")
                continue
            content = document.read_text(encoding="utf-8")
            shot_count = len(re.findall(r"^## Shot \d{2}\b", content, flags=re.MULTILINE))
            if shot_count != len(frames):
                errors.append(
                    f"{filename} contains {shot_count} Shot sections; expected {len(frames)}"
                )
            for link in re.findall(r"!\[[^\]]*\]\((/[^)]+)\)", content):
                if not Path(link).is_file():
                    errors.append(f"broken image link in {filename}: {link}")

    report(errors, frame_count=len(frames))


def report(errors: list[str], frame_count: int = 0) -> None:
    if errors:
        print("validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({"status": "ok", "frameCount": frame_count}, ensure_ascii=False))


if __name__ == "__main__":
    main()
