#!/usr/bin/env python3
"""Build a dated month of vertical Shorts from owned long-form gameplay.

This script does not upload anything. It converts source originals in a folder
into unique, non-overlapping Shorts and writes a manifest used for scheduling.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
PAKISTAN = ZoneInfo("Asia/Karachi")
DEFAULT_SLOTS = (time(10, 0), time(18, 0))


@dataclass(frozen=True)
class Candidate:
    source: Path
    start: float
    score: float


def run(cmd: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, check=True, text=True, capture_output=capture)


def duration_seconds(path: Path) -> float:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture=True,
    )
    return float(result.stdout.strip())


def rms_points(path: Path) -> list[tuple[float, float]]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-vn",
        "-af",
        (
            "asetnsamples=n=24000:p=0,astats=metadata=1:reset=1,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level"
        ),
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(command, text=True, capture_output=True, check=False)
    text_output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    points: list[tuple[float, float]] = []
    timestamp: float | None = None
    for line in text_output.splitlines():
        time_match = re.search(r"pts_time:([0-9.]+)", line)
        if time_match:
            timestamp = float(time_match.group(1))
        level_match = re.search(
            r"lavfi\.astats\.Overall\.RMS_level=([-+a-zA-Z0-9.]+)", line
        )
        if level_match and timestamp is not None:
            raw = level_match.group(1).lower()
            level = -100.0 if raw in {"-inf", "inf", "nan"} else float(raw)
            points.append((timestamp, level))
    return points


def source_candidates(path: Path, clip_seconds: int) -> list[Candidate]:
    total = duration_seconds(path)
    if total < clip_seconds + 12:
        return []
    points = rms_points(path)
    if not points:
        return []
    candidates: list[Candidate] = []
    # Score windows every two seconds. Loud gunfire and reaction moments tend to
    # rank above menus and quiet traversal in Valorant gameplay.
    for start in range(6, max(7, math.floor(total - clip_seconds - 4)), 2):
        values = [db for timestamp, db in points if start <= timestamp < start + clip_seconds]
        if len(values) < max(8, clip_seconds):
            continue
        values.sort(reverse=True)
        loud_tail = values[: max(2, len(values) // 5)]
        score = (sum(values) / len(values)) * 0.55 + (sum(loud_tail) / len(loud_tail)) * 0.45
        candidates.append(Candidate(path, float(start), score))
    return candidates


def overlaps(candidate: Candidate, selected: list[Candidate], clip_seconds: int, gap: int) -> bool:
    for existing in selected:
        if existing.source != candidate.source:
            continue
        if abs(existing.start - candidate.start) < clip_seconds + gap:
            return True
    return False


def select_candidates(
    sources: list[Path], target: int, clip_seconds: int, gap: int
) -> list[Candidate]:
    all_candidates: list[Candidate] = []
    for source in sources:
        print(f"Analysing {source.name}")
        all_candidates.extend(source_candidates(source, clip_seconds))
    all_candidates.sort(key=lambda item: item.score, reverse=True)

    selected: list[Candidate] = []
    # First pass distributes clips across sources, preventing one original from
    # dominating the month even if its soundtrack is consistently louder.
    per_source_limit = max(1, math.ceil(target / len(sources)) + 2)
    counts: dict[Path, int] = {source: 0 for source in sources}
    for candidate in all_candidates:
        if counts[candidate.source] >= per_source_limit:
            continue
        if overlaps(candidate, selected, clip_seconds, gap):
            continue
        selected.append(candidate)
        counts[candidate.source] += 1
        if len(selected) == target:
            return selected

    # Second pass fills remaining slots while preserving non-overlap.
    for candidate in all_candidates:
        if candidate in selected or overlaps(candidate, selected, clip_seconds, gap):
            continue
        selected.append(candidate)
        if len(selected) == target:
            return selected

    raise RuntimeError(
        f"Only {len(selected)} unique highlights were found; {target} are required. "
        "Download more owned source originals instead of repeating moments."
    )


def clean_source_title(path: Path) -> str:
    title = re.sub(r"[_-]+", " ", path.stem)
    title = re.sub(r"\s+", " ", title).strip()
    return title[:65] or "VariantFPS Highlight"


def render(
    candidate: Candidate,
    destination: Path,
    clip_seconds: int,
    preset: str,
    crf: int,
) -> None:
    if destination.exists():
        try:
            if duration_seconds(destination) >= clip_seconds - 0.25:
                print(f"Reusing completed {destination.name}")
                return
        except (OSError, ValueError, subprocess.CalledProcessError):
            pass
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    filter_graph = (
        "[0:v]scale=540:960:force_original_aspect_ratio=increase,"
        "crop=540:960,gblur=sigma=18,scale=1080:1920[bg];"
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        f"drawtext=fontfile={font}:text='CALM AIM. CLEAN FINISH.':"
        "fontcolor=white:fontsize=54:borderw=4:bordercolor=black:"
        "x=(w-text_w)/2:y=105,"
        f"drawtext=fontfile={font}:text='@real-knight':"
        "fontcolor=white:fontsize=40:borderw=3:bordercolor=black:"
        "x=(w-text_w)/2:y=h-145[v]"
    )
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{candidate.start:.2f}",
            "-t",
            str(clip_seconds),
            "-i",
            str(candidate.source),
            "-filter_complex",
            filter_graph,
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(destination),
        ]
    )


def schedule_times(start_day: date, days: int) -> list[datetime]:
    result: list[datetime] = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        for slot in DEFAULT_SLOTS:
            result.append(datetime.combine(day, slot, tzinfo=PAKISTAN))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("month_queue"))
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--clip-seconds", type=int, default=20)
    parser.add_argument("--gap-seconds", type=int, default=3)
    parser.add_argument("--preset", default="veryfast")
    parser.add_argument("--crf", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, help="Testing override; normally days x 2")
    args = parser.parse_args()

    sources = sorted(
        path for path in args.sources.iterdir() if path.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not sources:
        raise RuntimeError(f"No source videos found in {args.sources}")

    schedule = schedule_times(args.start_date, args.days)
    target = args.limit or len(schedule)
    if target > len(schedule):
        raise RuntimeError("The test limit cannot exceed the available schedule slots")
    schedule = schedule[:target]
    selected = select_candidates(sources, target, args.clip_seconds, args.gap_seconds)

    args.output.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[int, Candidate, datetime, str, Path]] = []
    for index, (candidate, publish_at) in enumerate(zip(selected, schedule), start=1):
        filename = f"{publish_at:%Y-%m-%d_%H%M}_{index:03d}.mp4"
        jobs.append((index, candidate, publish_at, filename, args.output / filename))

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [
            executor.submit(
                render,
                candidate,
                destination,
                args.clip_seconds,
                args.preset,
                args.crf,
            )
            for _, candidate, _, _, destination in jobs
        ]
        for future in futures:
            future.result()

    manifest_rows: list[dict[str, object]] = []
    for index, candidate, publish_at, filename, _ in jobs:
        source_title = clean_source_title(candidate.source)
        title = f"Calm Aim. Clean Finish. #{index:02d} 🎯 #valorant #shorts"
        description = (
            "VariantFPS gameplay highlight, edited for Knight.\n\n"
            f"Source original: {source_title}\n"
            "#VariantFPS #VALORANT #Gaming #Shorts"
        )
        manifest_rows.append(
            {
                "sequence": index,
                "file": filename,
                "publish_at_pakistan": publish_at.isoformat(),
                "publish_at_utc": publish_at.astimezone(timezone.utc).isoformat(),
                "title": title,
                "description": description,
                "source_file": candidate.source.name,
                "source_start_seconds": round(candidate.start, 2),
                "duration_seconds": args.clip_seconds,
                "selection_score": round(candidate.score, 3),
            }
        )

    with (args.output / "manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifest_rows[0]))
        writer.writeheader()
        writer.writerows(manifest_rows)
    (args.output / "manifest.json").write_text(
        json.dumps(manifest_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Prepared {len(manifest_rows)} unique Shorts in {args.output}")


if __name__ == "__main__":
    main()
