#!/usr/bin/env python3
"""
evaluate.py
===========
Property marketing video evaluation pipeline: ingest -> CV gate -> Gemini
judge -> composite score. See rubric.md for the evaluation rubric and
config.py for all tunable values.

Import `evaluate_video(source, template_type)` to run the full pipeline
programmatically (used by a dashboard or batch runner); the CLI at the
bottom is a thin wrapper around the same function.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np
import requests

import config


# ============================================================================
# INGEST
# ============================================================================
def _download_video(url):
    """Stream a hosted video URL to a temp .mp4 file; return the local path."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")

    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    try:
        with requests.get(url, stream=True, timeout=config.DOWNLOAD_TIMEOUT_SEC) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=config.DOWNLOAD_CHUNK_SIZE):
                if chunk:
                    tmp.write(chunk)
    except Exception:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise
    tmp.close()
    return tmp.name


def resolve_source(source):
    """
    Resolve `source` (an http(s) URL or a local file path) to a local file
    path used by the rest of the pipeline.

    Returns (local_path, is_temp) so the caller can clean up downloaded temp
    files once the pipeline is done with them.
    """
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        return _download_video(source), True

    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {source}")
    return str(path), False


def probe_video(local_path):
    """Read basic metadata (fps, frame count, duration, resolution) via OpenCV."""
    cap = cv2.VideoCapture(local_path)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open video: {local_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()

    if fps <= 0:
        raise RuntimeError(f"Could not determine a valid FPS for video: {local_path}")

    return {
        "fps": round(fps, 2),
        "frame_count": frame_count,
        "duration_sec": round(frame_count / fps, 2),
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}",
    }


def ingest(source):
    """Resolve `source` to a local file and probe its metadata."""
    local_path, is_temp = resolve_source(source)
    metadata = probe_video(local_path)
    return local_path, is_temp, metadata


# ============================================================================
# CV GATE
# ============================================================================
# Signal-to-issue mapping (each issue type is read off a different shape in
# the per-frame time series, not just "how different are two frames"):
#
#   lurch         <- ORB/RANSAC affine pan speed spike right before a fade,
#                    relative to that shot's own steady pan speed. Ported
#                    from the validated video_quality_eval.py detector.
#   stutter       <- mean abs frame difference near ZERO over a sustained
#                    run (duplicate/frozen frames from a rendering stall).
#   glitch        <- mean abs frame difference an OUTLIER vs both immediate
#                    neighbors, while the neighbors themselves stay similar
#                    to each other (a corrupt/artifact frame, not real
#                    motion continuing in one direction).
#   unsmooth_pan  <- Farneback dense optical flow; jerk = frame-to-frame
#                    change in flow magnitude (acceleration), sustained high
#                    over a window rather than a single spike.
#
# Fades to black are intentional in this footage and are not a rubric issue.
# A fade-detection pre-pass (ported from video_quality_eval.py's brightness-
# dip logic) finds those frame ranges purely to mask them out of the
# stutter/glitch/jerk signals -- a fade's own brightness swing would
# otherwise look exactly like all three. Lurch detection uses the fade
# boundaries directly, since it is specifically a pre-fade phenomenon.


def _format_timestamp(frame_idx, fps):
    """Convert a frame index to an MM:SS timestamp string."""
    total_sec = frame_idx / fps if fps else 0
    m, s = divmod(int(round(total_sec)), 60)
    return f"{m:02d}:{s:02d}"


def _orb_affine_motion(prev_kp, prev_des, kp, des, bf):
    """Estimate global (dx, dy) pan motion between two frames via ORB+RANSAC."""
    dx = dy = 0.0
    nmatch = 0
    if des is not None and prev_des is not None and len(des) > 10 and len(prev_des) > 10:
        m = bf.match(prev_des, des)
        if len(m) >= 10:
            src = np.float32([prev_kp[x.queryIdx].pt for x in m])
            dst = np.float32([kp[x.trainIdx].pt for x in m])
            M, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC)
            if M is not None:
                dx, dy = float(M[0, 2]), float(M[1, 2])
                nmatch = int(inliers.sum()) if inliers is not None else 0
    return dx, dy, nmatch


def extract_cv_signals(local_path):
    """
    Single decode pass over the video producing the raw per-frame series the
    rest of the CV gate operates on:
      brightness      - mean grayscale level (fade detection)
      frame_diff      - mean abs diff vs the previous frame (stutter / glitch)
      skip_diff       - mean abs diff vs the frame two back (glitch: confirms
                        the neighbors on either side of a candidate glitch
                        frame are similar to EACH OTHER, not just each
                        different from the candidate)
      orb_speed       - ORB/RANSAC affine pan speed vs previous frame (lurch)
      orb_matches     - inlier match count backing orb_speed (trustworthiness)
      flow_magnitude  - mean Farneback dense-flow magnitude (unsmooth panning)
    Index 0 of every "vs previous frame" series is a meaningless placeholder
    (there is no frame -1); callers must exclude it.
    """
    cap = cv2.VideoCapture(str(local_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open video: {local_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

    orb = cv2.ORB_create(1000)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

    brightness, frame_diff, skip_diff = [], [], []
    orb_speed, orb_matches, flow_magnitude = [], [], []
    prev_gray = prev_prev_gray = prev_kp = prev_des = None

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (config.WORK_W, max(1, int(h * config.WORK_W / w))))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        brightness.append(float(gray.mean()))

        kp, des = orb.detectAndCompute(gray, None)

        if prev_gray is not None:
            fd = float(np.abs(gray.astype(np.int16) - prev_gray.astype(np.int16)).mean())
            frame_diff.append(fd)
            dx, dy, nmatch = _orb_affine_motion(prev_kp, prev_des, kp, des, bf)
            orb_speed.append((dx ** 2 + dy ** 2) ** 0.5)
            orb_matches.append(nmatch)
            flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None, **config.FARNEBACK_PARAMS)
            mag, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
            flow_magnitude.append(float(mag.mean()))
        else:
            frame_diff.append(0.0)
            orb_speed.append(0.0)
            orb_matches.append(0)
            flow_magnitude.append(0.0)

        if prev_prev_gray is not None:
            skip_diff.append(float(np.abs(gray.astype(np.int16) - prev_prev_gray.astype(np.int16)).mean()))
        else:
            skip_diff.append(0.0)

        prev_prev_gray, prev_gray, prev_kp, prev_des = prev_gray, gray, kp, des

    cap.release()
    n = len(brightness)
    if n < 2:
        raise RuntimeError(f"Video too short to analyze ({n} frame(s)): {local_path}")

    return dict(
        fps=fps, n_frames=n, width=width, height=height,
        brightness=np.array(brightness),
        frame_diff=np.array(frame_diff),
        skip_diff=np.array(skip_diff),
        orb_speed=np.array(orb_speed),
        orb_matches=np.array(orb_matches),
        flow_magnitude=np.array(flow_magnitude),
    )


def detect_fades(sig):
    """
    Find fade-to-black regions as contiguous near-black brightness runs.
    Ported from video_quality_eval.py's transition detector (the fade-dip
    half only -- there is no "missing fade" check here; fades are purely
    masked out of the other three signals, not evaluated themselves).

    Each shot also settles to a complete stop for a stretch of frames right
    before the brightness starts ramping down -- a deliberate "hold on the
    final frame" pause, not part of the fade itself. The brightness ramp and
    the hold are found in two steps, walking backward from the trough:
      A. while brightness is strictly darkening frame-to-frame (the ramp
         itself) -- this naturally stops right at the plateau where the
         hold begins, no fixed cutoff needed.
      B. from there, while frame-to-frame motion stays near zero (the hold).
    Without step A, a hold separated from the trough by the ramp is never
    reached: the ramp's own frames have large brightness-driven diffs, which
    looks like neither "dark" nor "near-zero motion" and blocks the walk.
    """
    b, fd = sig["brightness"], sig["frame_diff"]
    if len(b) == 0:
        return []
    bright_level = float(np.percentile(b, 75))
    dark_cut = bright_level * config.FADE_DARK_FRAC

    fades = []
    is_dark = b < dark_cut
    i = 0
    while i < len(b):
        if is_dark[i]:
            j = i
            while j < len(b) and is_dark[j]:
                j += 1
            center = i + int(np.argmin(b[i:j]))

            start = i
            k = i - 1
            while k >= 1 and b[k] > b[k + 1] and i - k <= config.FADE_HOLD_MAX_FRAMES:
                start = k
                k -= 1
            k = start - 1
            while (k >= 1 and fd[k] < config.FADE_HOLD_DIFF_THRESHOLD
                   and i - k <= config.FADE_HOLD_MAX_FRAMES):
                start = k
                k -= 1

            end = j - 1
            k = j
            while k < len(b) and b[k] > b[k - 1] and k - (j - 1) <= config.FADE_HOLD_MAX_FRAMES:
                end = k
                k += 1

            fades.append({"start": start, "end": end, "boundary_frame": center})
            i = j
        else:
            i += 1
    return fades


def _fade_mask(n_frames, fades):
    """Boolean array, True where a frame falls inside a fade (+ buffer) and should be excluded."""
    mask = np.zeros(n_frames, dtype=bool)
    buf = config.FADE_MASK_BUFFER_FRAMES
    for f in fades:
        lo = max(0, f["start"] - buf)
        hi = min(n_frames, f["end"] + buf + 1)
        mask[lo:hi] = True
    return mask


def detect_lurches(sig, fades):
    """
    For each fade, inspect the bright, well-tracked frames just before it
    begins. Flag a lurch when ORB/RANSAC pan speed spikes well above the
    shot's own steady pan speed in that window. Ported from the validated
    video_quality_eval.py detector.
    """
    b, v, mt = sig["brightness"], sig["orb_speed"], sig["orb_matches"]
    fps = sig["fps"]
    bright_level = float(np.percentile(b, 75))
    lurches = []

    for f in fades:
        c = f["boundary_frame"]
        lo = max(1, c - config.LURCH_LOOKBACK - 8)
        bright_tracked = [i for i in range(lo, c)
                           if b[i] > 0.6 * bright_level and mt[i] >= config.LURCH_MIN_MATCHES]
        if not bright_tracked:
            continue
        fade_start = max(bright_tracked)
        win = [i for i in range(max(1, fade_start - config.LURCH_LOOKBACK), fade_start + 1)
               if mt[i] >= config.LURCH_MIN_MATCHES and b[i] > 0.6 * bright_level]
        if len(win) < 4:
            continue

        win = np.array(win)
        wspeed = v[win]
        baseline = float(np.median(wspeed))
        if baseline <= 0:
            baseline = float(np.mean(wspeed)) or 1e-6
        peak_i = int(win[np.argmax(wspeed)])
        peak = float(v[peak_i])

        if peak >= config.LURCH_REL * baseline and peak >= config.LURCH_MIN_SPEED:
            lurches.append({
                "type": "lurch",
                "timestamp": _format_timestamp(peak_i, fps),
                "value": round(peak, 2),
                "threshold": round(config.LURCH_REL * baseline, 2),
            })
    return lurches


def detect_stutters(sig, exclude):
    """A sustained run (or elevated overall rate) of near-zero frame-to-frame difference."""
    fd = sig["frame_diff"]
    fps = sig["fps"]
    is_dup = (fd < config.STUTTER_DIFF_THRESHOLD) & ~exclude

    issues = []
    i = 0
    while i < len(is_dup):
        if is_dup[i]:
            j = i
            while j < len(is_dup) and is_dup[j]:
                j += 1
            run_len = j - i
            if run_len >= config.STUTTER_MIN_RUN_FRAMES:
                issues.append({
                    "type": "stutter",
                    "timestamp": _format_timestamp(i, fps),
                    "value": run_len,
                    "threshold": config.STUTTER_MIN_RUN_FRAMES,
                })
            i = j
        else:
            i += 1

    unmasked_count = int((~exclude).sum())
    dup_rate = float(is_dup.sum() / unmasked_count) if unmasked_count else 0.0
    if dup_rate > config.STUTTER_RATE_THRESHOLD:
        first_dup = int(np.argmax(is_dup)) if is_dup.any() else 0
        issues.append({
            "type": "stutter",
            "timestamp": _format_timestamp(first_dup, fps),
            "value": round(dup_rate, 3),
            "threshold": config.STUTTER_RATE_THRESHOLD,
        })
    return issues


def detect_glitches(sig, exclude):
    """
    A frame whose diff to both immediate neighbors is a statistical outlier,
    while those neighbors stay close to each other (skip_diff), is treated
    as corruption rather than real (if fast) motion continuing through it.
    """
    fd, skip = sig["frame_diff"], sig["skip_diff"]
    fps = sig["fps"]
    valid = fd[~exclude]
    if len(valid) < 3:
        return []
    mean, std = float(valid.mean()), float(valid.std())
    thresh = mean + config.GLITCH_STD_MULTIPLIER * std

    issues = []
    for i in range(1, len(fd) - 1):
        if exclude[i] or exclude[i - 1] or exclude[i + 1]:
            continue
        if fd[i] > thresh and fd[i + 1] > thresh and skip[i + 1] <= thresh:
            issues.append({
                "type": "glitch",
                "timestamp": _format_timestamp(i, fps),
                "value": round(float(fd[i]), 2),
                "threshold": round(thresh, 2),
            })
    return issues


def detect_unsmooth_panning(sig, exclude):
    """
    Jerk = frame-to-frame change in Farneback flow magnitude (the second
    derivative of position). A smooth pan holds a near-constant flow
    magnitude; a jerky one has repeated large accelerations. Sustained high
    jerk over a window -- not a single spike, which is `lurch`'s job --
    fails the gate.
    """
    flow = sig["flow_magnitude"]
    fps = sig["fps"]
    jerk = np.zeros(len(flow))
    if len(flow) > 2:
        jerk[2:] = np.abs(np.diff(flow[1:]))
    high = (jerk > config.JERK_THRESHOLD) & ~exclude

    issues = []
    w = config.JERK_SUSTAINED_WINDOW
    flagged = np.zeros(len(high), dtype=bool)
    i = 0
    while i <= len(high) - w:
        window = high[i:i + w]
        if window.mean() >= config.JERK_SUSTAINED_FRACTION and not flagged[i:i + w].any():
            issues.append({
                "type": "unsmooth_pan",
                "timestamp": _format_timestamp(i, fps),
                "value": round(float(jerk[i:i + w].mean()), 3),
                "threshold": config.JERK_THRESHOLD,
            })
            flagged[i:i + w] = True
        i += 1
    return issues, jerk


def run_cv_gate(local_path):
    """
    Run the full Tier-1 CV gate: extract signals, mask fade regions, detect
    the four issue types, and decide pass/fail. Always returns metrics +
    issues regardless of the outcome (this is what --metrics-only prints).
    """
    sig = extract_cv_signals(local_path)
    fades = detect_fades(sig)
    fade_mask = _fade_mask(sig["n_frames"], fades)
    exclude = fade_mask.copy()
    exclude[0] = True  # frame 0 has no previous frame to diff/flow against

    lurch_issues = detect_lurches(sig, fades)
    stutter_issues = detect_stutters(sig, exclude)
    glitch_issues = detect_glitches(sig, exclude)
    pan_issues, _ = detect_unsmooth_panning(sig, exclude)

    issues = lurch_issues + stutter_issues + glitch_issues + pan_issues
    issues.sort(key=lambda x: x["timestamp"])

    metrics = {
        "fps": round(sig["fps"], 2),
        "duration_sec": round(sig["n_frames"] / sig["fps"], 2) if sig["fps"] else None,
        "resolution": f"{sig['width']}x{sig['height']}",
        "duplicate_frames": int(((sig["frame_diff"] < config.STUTTER_DIFF_THRESHOLD) & ~exclude).sum()),
        "glitch_frames": len(glitch_issues),
        "fade_regions_detected": len(fades),
    }

    return {"passed": len(issues) == 0, "metrics": metrics, "issues": issues}


# ============================================================================
# JUDGE
# ============================================================================
# Built in Part 5 (blocked pending data-privacy sign-off): Gemini Files API
# upload, rubric-based prompt, structured JSON output, retry/backoff.


# ============================================================================
# SCORING
# ============================================================================
# Built in Part 6: composite score from the judge's dimension scores and
# rule pass/fail results, using config.DIMENSION_WEIGHT and
# config.DEDUCTION_POINTS.


# ============================================================================
# ORCHESTRATION
# ============================================================================
def _gate_fail_reason(issues):
    detail = ", ".join(f'{i["type"]} at {i["timestamp"]}' for i in issues)
    return f"Failed on technical quality: noticeable motion issues detected ({detail})"


def evaluate_video(source, template_type):
    """
    Run the full pipeline for one video: ingest -> CV gate -> (if the gate
    passes) Gemini judge -> composite score.

    template_type: one of "short", "medium", "long".

    The Gemini judge and scoring stages (Part 5/6) are not wired in yet --
    a video that passes the gate currently returns with the gate's own
    metrics/issues populated and everything downstream left empty, rather
    than raising, so this can already be used to validate the gate on real
    template videos end to end.
    """
    if template_type not in ("short", "medium", "long"):
        raise ValueError(f"template_type must be one of 'short', 'medium', 'long', got {template_type!r}")

    local_path, is_temp, _ = ingest(source)
    try:
        gate = run_cv_gate(local_path)

        result = {
            "source": source,
            "template_type": template_type,
            "gate": gate,
            "overall_score": 0 if not gate["passed"] else None,
            "reason": (_gate_fail_reason(gate["issues"]) if not gate["passed"]
                       else "Gate passed -- LLM judge not yet implemented (Part 5), stopping here"),
            "room_sequence": [],
            "dimensions": {},
            "rules": [],
            "base_score": 0,
            "total_deductions": 0,
            "summary": "",
            "top_fixes": [],
        }
        return result
    finally:
        if is_temp:
            Path(local_path).unlink(missing_ok=True)


# ============================================================================
# CLI
# ============================================================================
def _cli_metrics_only(source):
    """Run just the CV gate and print raw metrics/issues, with no pass/fail framing."""
    local_path, is_temp, _ = ingest(source)
    try:
        gate = run_cv_gate(local_path)
    finally:
        if is_temp:
            Path(local_path).unlink(missing_ok=True)
    print(json.dumps({"source": source, "metrics": gate["metrics"], "issues": gate["issues"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Property marketing video evaluation pipeline.")
    parser.add_argument("--source", required=True, help="Video URL or local file path")
    parser.add_argument("--template", choices=["short", "medium", "long"],
                         help="Video template type (required unless --metrics-only)")
    parser.add_argument("--output", help="Path to write the result JSON (default: print to stdout)")
    parser.add_argument("--metrics-only", action="store_true",
                         help="Run only the CV gate and print raw metrics/issues for threshold calibration")
    args = parser.parse_args()

    try:
        if args.metrics_only:
            _cli_metrics_only(args.source)
        else:
            if not args.template:
                parser.error("--template is required unless --metrics-only is set")
            output = json.dumps(evaluate_video(args.source, args.template), indent=2)
            if args.output:
                Path(args.output).write_text(output)
                print(f"Result written to {args.output}", file=sys.stderr)
            else:
                print(output)
    except (FileNotFoundError, RuntimeError, ValueError, requests.exceptions.RequestException) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
