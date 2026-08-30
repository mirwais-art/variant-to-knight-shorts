#!/usr/bin/env python3
import argparse, json, math, os, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

SOURCE_URL = os.environ.get("SOURCE_URL", "https://www.youtube.com/@VariantFPS/videos")
STATE_FILE = Path("state.json")
OUTPUT_FILE = Path("clip.mp4")
META_FILE = Path("metadata.json")
CLIP_SECONDS = 22
MIN_SOURCE_SECONDS = 55
GATE_HOURS = 72

def run(cmd, capture=False):
    print("+", " ".join(map(str, cmd)))
    return subprocess.run(cmd, check=True, text=True, capture_output=capture)

def now_utc():
    return datetime.now(timezone.utc)

def read_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"last_generated_at": None, "used_video_ids": []}

def set_output(key, value):
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")

def due(state, force):
    if force or not state.get("last_generated_at"):
        return True
    last = datetime.fromisoformat(state["last_generated_at"].replace("Z", "+00:00"))
    return (now_utc() - last).total_seconds() >= GATE_HOURS * 3600

def list_candidates():
    result = run([
        "yt-dlp", "--flat-playlist", "--dump-single-json",
        "--playlist-end", "120", SOURCE_URL
    ], capture=True)
    data = json.loads(result.stdout)
    return data.get("entries") or []

def pick_video(entries, used):
    candidates = []
    for index, item in enumerate(entries):
        vid = item.get("id")
        duration = item.get("duration") or 0
        if not vid or vid in used or duration < MIN_SOURCE_SECONDS:
            continue
        candidates.append((index, duration, item))
    if not candidates:
        return None
    # Prefer older long-form uploads while still choosing useful gameplay-length videos.
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2]

def download_video(url):
    run([
        "yt-dlp", "--no-playlist",
        "--extractor-args", "youtube:player_client=android,web",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]",
        "--merge-output-format", "mp4",
        "-o", "source.%(ext)s", url
    ])
    files = sorted(Path(".").glob("source.*"))
    if not files:
        raise RuntimeError("Source download produced no file")
    return files[0]

def duration_seconds(path):
    r = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ], capture=True)
    return float(r.stdout.strip())

def loudest_start(path, duration):
    if duration <= CLIP_SECONDS + 2:
        return 0.0
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
        "-af", "asetnsamples=n=24000:p=0,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
        "-f", "null", "-"
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    points, current_t = [], None
    for line in text.splitlines():
        mt = re.search(r"pts_time:([0-9.]+)", line)
        if mt:
            current_t = float(mt.group(1))
        mv = re.search(r"lavfi\.astats\.Overall\.RMS_level=([-+a-zA-Z0-9.]+)", line)
        if mv and current_t is not None:
            raw = mv.group(1)
            level = -100.0 if raw.lower() in {"-inf", "inf", "nan"} else float(raw)
            if 8 <= current_t <= max(8, duration - 8):
                points.append((current_t, level))
    if not points:
        return max(5.0, min(duration * 0.35, duration - CLIP_SECONDS - 1))
    best_t, best_score = points[0][0], -999.0
    for t, _ in points:
        vals = [db for pt, db in points if t <= pt <= t + CLIP_SECONDS]
        if len(vals) >= 8:
            score = sum(vals) / len(vals)
            if score > best_score:
                best_t, best_score = t, score
    return max(5.0, min(best_t, duration - CLIP_SECONDS - 1))

def make_vertical(path, start):
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    vf = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,gblur=sigma=28[bg];"
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        f"drawtext=fontfile={font}:text='VARIANTFPS HIGHLIGHT':"
        "fontcolor=white:fontsize=56:borderw=4:bordercolor=black:"
        "x=(w-text_w)/2:y=100,"
        f"drawtext=fontfile={font}:text='@real-knight':"
        "fontcolor=white:fontsize=42:borderw=3:bordercolor=black:"
        "x=(w-text_w)/2:y=h-150[v]"
    )
    run([
        "ffmpeg", "-y", "-ss", f"{start:.2f}", "-t", str(CLIP_SECONDS),
        "-i", str(path), "-filter_complex", vf,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        "-shortest", str(OUTPUT_FILE)
    ])

def clean_title(value):
    value = re.sub(r"\s+", " ", value or "VariantFPS Gaming Highlight").strip()
    return value[:72]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    state = read_state()
    if not due(state, args.force):
        print("Not due yet; waiting for the 72-hour interval.")
        set_output("generated", "false")
        return
    entries = list_candidates()
    selected = pick_video(entries, set(state.get("used_video_ids") or []))
    if selected is None:
        state["used_video_ids"] = []
        selected = pick_video(entries, set())
    if selected is None:
        raise RuntimeError("No eligible long-form VariantFPS video was found")
    video_id = selected["id"]
    source_url = f"https://www.youtube.com/watch?v={video_id}"
    title = clean_title(selected.get("title"))
    source = download_video(source_url)
    total = duration_seconds(source)
    start = loudest_start(source, total)
    make_vertical(source, start)
    generated = now_utc().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    metadata = {
        "title": f"{title} 🔥 #shorts"[:100],
        "description": f"VariantFPS gameplay highlight.\n\nSource: {source_url}\n#VariantFPS #Gaming #Shorts",
        "source_video_id": video_id,
        "source_url": source_url,
        "highlight_start_seconds": round(start, 2),
        "duration_seconds": CLIP_SECONDS,
        "generated_at": generated
    }
    META_FILE.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    used = list(state.get("used_video_ids") or [])
    used.append(video_id)
    state["used_video_ids"] = used[-200:]
    state["last_generated_at"] = generated
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    set_output("generated", "true")
    print(json.dumps(metadata, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        set_output("generated", "false")
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
