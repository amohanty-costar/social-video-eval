#!/usr/bin/env python3
"""
evaluate.py
===========
PURPOSE: The engine. All the evaluation logic lives here.

Runs one video through the pipeline:
    ingest -> CV gate (Tier 1) -> Gemini judge (Tier 2/3) -> composite score

A video that fails the gate scores 0 and never reaches the API.

Call `evaluate_video(source, template_type)` to run the whole thing. The web
app does exactly that, and the CLI at the bottom of this file is a thin wrapper
around the same function, so all three behave identically.

Settings live in config.py, the judge prompt in prompt.md, the rubric in
rubric.md. Nothing in this file needs editing to retune the evaluation.
"""

import argparse
import json
import random
import re
import sys
import tempfile
import time
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
# Tier-2 dimensions and Tier-3 compliance rules, judged by Gemini in a single
# structured-output call against the video.
#
# Shape of the call:
#   1. Upload the video via the Files API and poll until it reaches ACTIVE
#      (a file in PROCESSING cannot be referenced in a prompt yet).
#   2. One generate_content call with the video part first, prompt second (the
#      documented ordering for single-video prompts), and a response_schema
#      pinning the exact JSON we need.
#   3. Delete the uploaded file.
#
# One call rather than several: the model sees the whole tour once, so the room
# sequence it reports and the rules it evaluates against that sequence come from
# a single pass and are far more likely to agree. Nothing *forces* them to
# agree, so cross_check_stop_rules() flags it when they don't. The cost of one
# call is that a bad response loses everything, which is what the retry/backoff
# wrapper is for.
#
# Two things the judge is deliberately not told: the point value of each
# compliance rule, and the formula that turns scores into a composite. See
# _rubric_text().
#
# Note on fidelity: Gemini samples video at 1 FPS for visual understanding.
# Room sequencing and compliance rules survive that fine. The "first ~3s"
# hook dimension is working from roughly three frames, so treat its score as
# the weakest signal in the report. The rubric marks Hook and Framing as
# "LLM + CV"; today they are LLM-only, since no CV signal is passed in here.


class JudgeError(RuntimeError):
    """Raised when the judge cannot produce a usable verdict."""


# Both prompt.md and rubric.md open with an explanatory <!-- --> block written
# for whoever is reading the file. Neither should reach the model.
_PROMPT_COMMENT_RE = re.compile(r"^\s*<!--.*?-->\s*", re.DOTALL)


def _read_asset(path_value, label):
    """Load a runtime asset (prompt.md / rubric.md), resolved next to this file."""
    path = Path(path_value)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    if not path.is_file():
        raise JudgeError(f"{label} file not found: {path}")
    return path.read_text(encoding="utf-8")


# ============================================================================
# THE RUBRIC AS THE JUDGE SEES IT
# ============================================================================
# rubric.md is the single source of truth, but the judge does NOT receive all
# of it. Three things are removed on the way in:
#
#   1. TIER 1 (the technical motion gate).
#      The judge is only ever called on a video that already PASSED the gate --
#      run_cv_gate() runs first and evaluate_video() returns a score of 0
#      without constructing a client if it fails. So Tier 1 is not the judge's
#      job, and showing it invites the model to re-litigate motion quality it
#      was not asked about and cannot see properly at 1 FPS.
#
#   2. The Tier-3 DEDUCTION column.
#      A model that knows a critical rule costs 10 points goes soft on critical
#      rules. It rules on what it sees; score() applies the cost afterwards.
#
#   3. The "Final score" section.
#      Contains the composite formula. Same reasoning -- knowing the formula
#      invites reasoning backward from a total the model thinks is deserved.
#
# The Tier-2 table is passed through completely intact, including its "1"
# column, which is the last cell on those rows and must not be mistaken for a
# deduction cell.
# ============================================================================
def _rubric_text(for_prompt=True):
    """
    Load rubric.md. With for_prompt=True, return the judge-facing version with
    Tier 1, the Deduction column and the Final score section removed.
    Pass for_prompt=False for the unmodified file.
    """
    text = _read_asset(config.RUBRIC_PATH, "Rubric")
    if not for_prompt:
        return text

    # rubric.md opens with an explanatory <!-- --> block for humans reading the
    # file. Same treatment as prompt.md's: strip it so it never reaches Gemini.
    text = _PROMPT_COMMENT_RE.sub("", text, count=1)

    out = []
    in_tier1 = False
    in_tier3 = False
    for line in text.splitlines():
        if line.startswith("## Final score"):
            break  # everything from here down is scoring arithmetic

        # Section tracking. Any "## " heading closes the previous section, so
        # a renamed or reordered rubric degrades gracefully rather than
        # silently swallowing the wrong block.
        if line.startswith("## "):
            in_tier1 = line.startswith("## Tier 1")
            in_tier3 = line.startswith("## Tier 3")

        if in_tier1:
            continue  # drop the gate section entirely

        # Only the Tier-3 table has a Deduction column to drop.
        if in_tier3 and line.startswith("|"):
            cells = line.split("|")
            if cells and cells[-1].strip() == "":
                cells = cells[:-1]
            if len(cells) > 2:
                line = "|".join(cells[:-1]) + " |"

        out.append(line)

    # Collapse the blank-line run left behind where Tier 1 used to be.
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    return cleaned.rstrip() + "\n"


# ============================================================================
# TEMPLATE TYPE -> WHICH RULES APPLY
# ============================================================================
# This is the single place the Template dropdown changes the evaluation.
#
# The path is:
#   video-eval-tool.py   st.selectbox("Template", ["short", "medium", "long"])
#     -> evaluate_video(source, template_type, judge=...)
#          -> run_judge(local_path, template_type)
#               -> _judge_prompt(template_type)     fills {{TEMPLATE_TYPE}}
#               -> _response_schema(template_type)  limits the rule fields
#          -> score(verdict, template_type)         limits the deductions
#
# All three of those call applicable_rules() below, so the dropdown decides
# what the model is asked, what it is allowed to answer, and what gets scored.
# They cannot drift apart.
#
# In practice only the bathroom rule differs: it is PROHIBITED in Short and
# REQUIRED in Medium/Long, so exactly one of the two bathroom rules applies to
# any given video and the other is never sent to the model at all. That is why
# a report always has 9 rules, not 10.
# ============================================================================
def applicable_rules(template_type):
    """The Tier-3 rules evaluated for this template type (bathroom differs)."""
    return [r for r in config.COMPLIANCE_RULES if template_type in r["applies"]]


# ============================================================================
# GUARDRAILS
# ============================================================================
# These live in the schema's `description` fields rather than in prompt.md,
# for two reasons: prompt.md stays clean and readable as an instruction, and
# Gemini reads schema descriptions as binding constraints on each field rather
# than as general advice it can weigh against everything else.
#
# Each one exists to prevent a specific, known failure mode:
#
#   RULE POLARITY (`violated` description)
#     The Tier-3 list mixes rules violated by ABSENCE ("kitchen not shown")
#     with rules violated by PRESENCE ("closet shown"). Models routinely invert
#     mixed-direction lists.
#
#   PANNING CARVE-OUT (`violated` description)
#     The judge is told a motion gate already ran. Two compliance rules are
#     nonetheless about panning -- where the camera points, and whether it
#     retraces itself -- so it needs telling those are still its job.
#
#   EVIDENCE BEFORE VERDICT (field ORDER, not text)
#     The SDK derives propertyOrdering from the order keys appear below, and
#     the model emits fields in that order. `evidence` and `rationale` come
#     before `score`, and `evidence` before `violated`, so the reasoning drives
#     the verdict instead of justifying one already committed to. Reordering
#     these keys silently changes model behaviour.
#
#   STOP DEFINITION (`room_sequence` description)
#     The two "first 3 stops" rules are only meaningful against a consistent
#     definition of a stop.
#
#   NO POINT VALUES (handled upstream in _rubric_text)
#     The judge never learns what a verdict costs.
#
# A fourth guardrail runs after the response arrives rather than constraining
# it: cross_check_stop_rules() compares the "first 3 stops" verdicts against
# the model's own room_sequence and flags disagreement.
# ============================================================================
def _response_schema(template_type):
    """
    JSON Schema for the judge's reply. Only the rules that apply to this
    template are included, so the model is never asked to rule on the
    prohibited-vs-required bathroom case that doesn't apply.
    """
    timestamp = {
        "type": "string",
        "description": "Timestamp in MM:SS format, e.g. 01:15.",
    }

    # Field order matters. The SDK derives propertyOrdering from the order of
    # these keys, and the model emits fields in that order -- so evidence and
    # rationale must come BEFORE score, or the score is generated first and the
    # rationale becomes post-hoc justification for a number already committed to.
    dimension = {
        "type": "object",
        "properties": {
            "evidence": {
                "type": "array", "items": timestamp, "minItems": 1,
                "description": "Timestamps of the moments you are basing this on. Gather these first.",
            },
            "rationale": {
                "type": "string",
                "description": "Two or three sentences on what those moments show, referring to what is actually on screen. Reason here before choosing a score.",
            },
            "score": {
                "type": "integer", "minimum": 1, "maximum": 5,
                "description": "1-5 per the rubric, following from the evidence and rationale above. Use 4 and 2 to interpolate between the anchored 5/3/1 descriptions.",
            },
        },
        "required": ["evidence", "rationale", "score"],
    }

    rule_ids = [r["id"] for r in applicable_rules(template_type)]

    return {
        "type": "object",
        "properties": {
            # GUARDRAIL: the "stop" definition, which the two "first 3 stops"
            # rules are evaluated against.
            "room_sequence": {
                "type": "array",
                "description": (
                    "The ordered tour stops. One entry per distinct space in order of first "
                    "appearance. Contiguous open-plan areas (e.g. a kitchen open to the living "
                    "room with no wall between them) count as ONE stop. Do not repeat a space "
                    "that is revisited later. The 'first 3 stops' compliance rules are judged "
                    "against this list, so keep those verdicts consistent with it."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "stop": {"type": "integer", "minimum": 1,
                                  "description": "1-based position in the tour."},
                        "room": {"type": "string",
                                  "description": "Short name, e.g. 'entry', 'kitchen', 'living room', 'primary bedroom'."},
                        "first_seen": timestamp,
                    },
                    "required": ["stop", "room", "first_seen"],
                },
            },
            "dimensions": {
                "type": "object",
                "properties": {name: dimension for name in config.DIMENSIONS},
                "required": list(config.DIMENSIONS),
            },
            "rules": {
                "type": "object",
                "description": "One verdict per compliance rule. `violated` true means the condition described by the rule IS present.",
                "properties": {
                    # evidence before violated, for the same reason as the
                    # dimensions above: state what you saw, then rule on it.
                    rid: {
                        "type": "object",
                        "properties": {
                            "evidence": {
                                "type": "string",
                                "description": (
                                    "What you actually saw that bears on this rule, with MM:SS "
                                    "timestamps. State this before deciding `violated`."
                                ),
                            },
                            # GUARDRAIL: rule polarity + panning carve-out.
                            "violated": {
                                "type": "boolean",
                                "description": (
                                    "True if the condition named by this rule HAS occurred. "
                                    "Read the direction carefully: some rules are violated by "
                                    "something being ABSENT ('not shown at all', 'not within "
                                    "the first 3 stops') and others by something being PRESENT "
                                    "('closet shown'). A separate technical check already judged "
                                    "motion smoothness, so do not consider stutters, glitches or "
                                    "camera lurches here -- but the two panning rules below ARE "
                                    "yours to judge, since they concern where the camera is "
                                    "pointed and whether it retraces itself, not how smoothly it "
                                    "moves. Each rule counts once no matter how often it recurs."
                                ),
                            },
                        },
                        "required": ["evidence", "violated"],
                    }
                    for rid in rule_ids
                },
                "required": rule_ids,
            },
            # Two distinct summaries. showcase_summary is DESCRIPTIVE (what is
            # in the video); summary is EVALUATIVE (why it scored as it did).
            # Kept apart so the descriptive one doesn't quietly become a second
            # verdict, and because the report shows them in different places.
            "showcase_summary": {
                "type": "string",
                "description": (
                    "Two or three sentences describing what the video showcases -- the "
                    "property and what is actually shown. Purely descriptive: no judgment, "
                    "no scoring language."
                ),
            },
            "summary": {
                "type": "string",
                "description": (
                    "Three or four sentences in plain language on why the video scored the "
                    "way it did. Written for a listing agent, not a video editor."
                ),
            },
            "top_fixes": {
                "type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3,
                "description": "The highest-leverage concrete changes, most impactful first.",
            },
        },
        "required": ["room_sequence", "dimensions", "rules",
                      "showcase_summary", "summary", "top_fixes"],
    }


# ============================================================================
# BUILDING THE PROMPT
# ============================================================================
# The prompt text itself lives in prompt.md, NOT here. This function only
# assembles it:
#
#   1. strips the HTML comment header from prompt.md
#   2. substitutes {{TEMPLATE_TYPE}} with the dropdown value
#   3. injects the judge-facing rubric between the <rubric></rubric> tags
#   4. appends the applicable rule ids and one neutral bathroom note
#
# To change what the judge is asked, edit prompt.md. To change what it is
# allowed to answer, edit _response_schema(). Behavioural guardrails live in
# the schema's field descriptions rather than here -- see the GUARDRAILS block
# above _response_schema().
# ============================================================================
_RUBRIC_SLOT_RE = re.compile(r"<rubric>\s*</rubric>", re.DOTALL)


def _judge_prompt(template_type):
    """Assemble the full judge instruction for one template type."""
    text = _read_asset(config.PROMPT_PATH, "Prompt")

    # 1. Drop the explanatory <!-- --> header; it is for humans reading the file.
    text = _PROMPT_COMMENT_RE.sub("", text, count=1)

    # 2. Template type from the web app's dropdown.
    if "{{TEMPLATE_TYPE}}" not in text:
        raise JudgeError(
            f"{config.PROMPT_PATH} is missing the {{{{TEMPLATE_TYPE}}}} placeholder; "
            "the judge would not be told which template it is evaluating."
        )
    text = text.replace("{{TEMPLATE_TYPE}}", template_type)

    # 3. Rubric injection -- Tier 1, deductions and the score formula already
    #    removed by _rubric_text().
    if not _RUBRIC_SLOT_RE.search(text):
        raise JudgeError(
            f"{config.PROMPT_PATH} is missing an empty <rubric></rubric> block "
            "for the rubric to be injected into."
        )
    text = _RUBRIC_SLOT_RE.sub(
        lambda _: "<rubric>\n" + _rubric_text().strip() + "\n</rubric>", text, count=1
    )

    # 4. The applicable rule ids, so the model's verdicts map onto the schema
    #    keys. Severities are deliberately omitted -- the judge rules on what it
    #    sees and score() applies the cost. See the TEMPLATE TYPE block above for
    #    why this list is 9 rules and not 10.
    rule_lines = "\n".join(
        f"- `{r['id']}`: {r['description']}" for r in applicable_rules(template_type)
    )

    # Neutral bathroom note. Phrased as "report whether one is in fact visible"
    # rather than "a bathroom must be shown": the Medium/Long rule is violated by
    # ABSENCE, and stating the requirement as an imperative makes hallucinating a
    # bathroom the path of least resistance.
    bathroom_note = (
        "For this template the rubric prohibits showing a bathroom."
        if template_type == "short"
        else "For this template the rubric expects a bathroom. Report whether one is "
             "in fact visible; do not assume one is present because the rubric expects it."
    )

    return f"""{text.rstrip()}

Rule the following compliance items, using exactly these ids:

{rule_lines}

{bathroom_note}"""


def _get_client():
    """Build a Gemini client, with an actionable error if no key is configured."""
    try:
        from google import genai
    except ImportError as e:
        raise JudgeError(
            "The google-genai package is not installed. Run: pip install -r requirements.txt"
        ) from e

    api_key = config.gemini_api_key()
    if not api_key:
        raise JudgeError(
            "No Gemini API key configured. Set the GEMINI_API_KEY environment variable, "
            "or add GEMINI_API_KEY to .streamlit/secrets.toml. "
            "Run with --no-judge to skip the judge and use the CV gate only."
        )
    return genai.Client(api_key=api_key)


def _wait_until_active(client, uploaded):
    """
    Block until Gemini reports the uploaded file ACTIVE. A file still in
    PROCESSING cannot be referenced in a prompt. Raises on FAILED or timeout;
    the caller owns deleting the file either way.
    """
    waited = 0
    while True:
        state = getattr(uploaded.state, "name", str(uploaded.state or ""))
        if state == "ACTIVE":
            return uploaded
        if state == "FAILED":
            raise JudgeError(f"Gemini failed to process the uploaded video (file {uploaded.name}).")
        if waited >= config.GEMINI_UPLOAD_TIMEOUT_SEC:
            raise JudgeError(
                f"Uploaded video was still in state {state!r} after "
                f"{config.GEMINI_UPLOAD_TIMEOUT_SEC}s (file {uploaded.name})."
            )
        time.sleep(config.GEMINI_UPLOAD_POLL_SEC)
        waited += config.GEMINI_UPLOAD_POLL_SEC
        uploaded = client.files.get(name=uploaded.name)


def _is_retryable(exc):
    """
    True for rate limits and transient server errors, which are worth another
    attempt. Everything else -- bad key, bad model name, safety block, malformed
    schema -- fails the same way every time, so retrying only burns quota.

    The status code is the reliable signal. The text fallback deliberately does
    NOT match bare numbers like "429" or "500": those appear in token counts and
    quota values inside the messages of genuinely fatal errors, which would then
    get retried five times for nothing.
    """
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (408, 429, 500, 502, 503, 504):
        return True
    text = str(exc).lower()
    return any(s in text for s in (
        "rate limit", "resource_exhausted", "quota exceeded", "too many requests",
        "internal error", "internal server error", "unavailable", "deadline exceeded",
        "overloaded", "try again later",
    ))


def _generate_with_retry(client, contents, schema):
    """
    One structured-output call, retried with exponential backoff on 429s and
    transient 5xxs. Anything else (bad key, bad model name, safety block) is
    raised immediately -- retrying those just wastes time and quota.
    """
    from google.genai import types

    cfg = dict(
        response_mime_type="application/json",
        response_schema=schema,
    )
    if config.GEMINI_TEMPERATURE is not None:
        cfg["temperature"] = config.GEMINI_TEMPERATURE
    if config.GEMINI_MEDIA_RESOLUTION:
        cfg["media_resolution"] = config.GEMINI_MEDIA_RESOLUTION

    last = None
    for attempt in range(config.GEMINI_MAX_RETRIES):
        try:
            return client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(**cfg),
            )
        except Exception as e:  # noqa: BLE001 - SDK raises a range of transport errors
            last = e
            if not _is_retryable(e) or attempt == config.GEMINI_MAX_RETRIES - 1:
                raise JudgeError(f"Gemini judge call failed: {e}") from e
            # BASE * 2^attempt (2, 4, 8, 16s at the default), plus jitter so a
            # batch run doesn't retry in lockstep and re-trip the rate limit.
            backoff = config.GEMINI_BACKOFF_BASE_SEC * (2 ** attempt)
            time.sleep(backoff + random.uniform(0, backoff * 0.25))
    raise JudgeError(f"Gemini judge call failed after {config.GEMINI_MAX_RETRIES} attempts: {last}")


def _finish_reason(response):
    """The first candidate's finish_reason, if the SDK surfaced one."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    reason = getattr(candidates[0], "finish_reason", None)
    return getattr(reason, "name", None) or (str(reason) if reason else None)


def _coerce_score(value):
    """
    Normalise a dimension score to an int, or return None if it isn't one.
    The schema declares INTEGER so this should always already be an int, but a
    JSON `4.0` is legal and shouldn't fail the whole run. Booleans are rejected
    explicitly -- in Python `True` is an int and would silently score as 1.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _parse_judge_response(response, template_type):
    """Pull the JSON payload out of the response and check it has what scoring needs."""
    # .text returns None on an empty response in google-genai 1.x, but some
    # versions raise instead, so treat both as "no usable text".
    try:
        text = response.text
    except (ValueError, AttributeError):
        text = None

    finish = _finish_reason(response)

    if not text:
        raise JudgeError(
            "Gemini returned no text (the video may have been blocked or the response truncated"
            + (f"; finish_reason={finish}" if finish else "") + ")."
        )

    try:
        verdict = json.loads(text)
    except json.JSONDecodeError as e:
        # A MAX_TOKENS truncation produces partial JSON and lands here, so
        # surface finish_reason -- otherwise this reads as a model defect when
        # it is really a length limit.
        hint = f" (finish_reason={finish})" if finish else ""
        if finish == "MAX_TOKENS":
            hint += (
                " -- the response was cut off by the output token limit; "
                "raise max_output_tokens or shorten the schema."
            )
        raise JudgeError(f"Gemini returned text that is not valid JSON{hint}: {e}") from e

    # The schema is enforced server-side, but a schema-valid response can still
    # be semantically wrong, so validate what the scoring math depends on.
    dims = verdict.get("dimensions") or {}
    for name in config.DIMENSIONS:
        entry = dims.get(name)
        if not isinstance(entry, dict):
            raise JudgeError(f"Judge response is missing dimension {name!r}.")
        score_value = _coerce_score(entry.get("score"))
        if score_value is None:
            raise JudgeError(
                f"Judge response has no usable integer score for dimension {name!r} "
                f"(got {entry.get('score')!r})."
            )
        if not 1 <= score_value <= 5:
            raise JudgeError(
                f"Judge returned an out-of-range score for {name!r}: {score_value} (expected 1-5)."
            )
        entry["score"] = score_value

    rules = verdict.get("rules") or {}
    for rule in applicable_rules(template_type):
        entry = rules.get(rule["id"])
        if not isinstance(entry, dict) or not isinstance(entry.get("violated"), bool):
            raise JudgeError(f"Judge response is missing a verdict for rule {rule['id']!r}.")

    return verdict


# The two "first 3 stops" rules are the only ones mechanically derivable from
# the room_sequence the judge also returns, so they can be cross-checked. A
# disagreement means the judge contradicted itself; we surface it rather than
# silently overriding, because room naming is fuzzy ("kitchen/dining", "great
# room") and a naive string match is not authoritative enough to overrule the
# model that actually watched the video.
_STOP_RULE_KEYWORDS = {
    "kitchen_not_in_first_3_stops": ("kitchen",),
    "living_room_not_in_first_3_stops": ("living", "lounge", "family room", "great room"),
}


def cross_check_stop_rules(verdict):
    """
    Compare the judge's "not within the first 3 stops" verdicts against its own
    room_sequence. Returns a list of human-readable warnings; empty when the
    verdict is self-consistent or the sequence is too sparse to judge.
    """
    sequence = verdict.get("room_sequence") or []
    if not sequence:
        return []

    ordered = sorted(sequence, key=lambda s: s.get("stop", 0))
    first_three = " | ".join(str(s.get("room", "")).lower() for s in ordered[:3])

    warnings = []
    for rule_id, keywords in _STOP_RULE_KEYWORDS.items():
        entry = (verdict.get("rules") or {}).get(rule_id)
        if not isinstance(entry, dict) or not isinstance(entry.get("violated"), bool):
            continue
        present = any(k in first_three for k in keywords)
        if present and entry["violated"]:
            warnings.append(
                f"{rule_id}: judge marked this violated, but its own room sequence lists "
                f"{keywords[0]} within the first 3 stops ({first_three})."
            )
        elif not present and not entry["violated"] and len(ordered) >= 3:
            warnings.append(
                f"{rule_id}: judge marked this satisfied, but its own room sequence's first "
                f"3 stops ({first_three}) do not appear to include {keywords[0]}."
            )
    return warnings


def run_judge(local_path, template_type):
    """
    Run the Gemini rubric judge on a video that has already passed the CV gate.
    Returns the parsed verdict: room_sequence, dimensions, rules, summary,
    top_fixes. Raises JudgeError on any unusable outcome.
    """
    client = _get_client()

    # Upload first, then everything else inside the try -- a video that fails
    # processing or times out has still been created server-side, and must be
    # deleted on that path too.
    uploaded = client.files.upload(file=local_path)
    try:
        _wait_until_active(client, uploaded)
        response = _generate_with_retry(
            client,
            # Video part first, text second: the documented ordering for
            # single-video prompts.
            contents=[uploaded, _judge_prompt(template_type)],
            schema=_response_schema(template_type),
        )
        verdict = _parse_judge_response(response, template_type)
        verdict["consistency_warnings"] = cross_check_stop_rules(verdict)
        return verdict
    finally:
        if config.GEMINI_DELETE_UPLOAD_AFTER_USE:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass  # the file expires on its own; not worth failing the run over


# ============================================================================
# SCORING -- THIS IS WHERE THE COMPOSITE SCORE IS CALCULATED
# ============================================================================
# The division of labour between the model and this code:
#
#   THE LLM JUDGES            THIS CODE CALCULATES
#   the four 1-5 sub-scores   the composite out of 100
#   every rule verdict        the point value of each failed rule
#   the room sequence         nothing else
#   rationales and evidence
#
# So if you are looking for where the headline score comes from: it is here,
# not in prompt.md and not in anything Gemini returns. prompt.md item 3 asks
# for the composite as the report headline, but the model is told it is
# supplied for it, and _response_schema() gives it no field to put a total in.
# score() computes it from the sub-scores and verdicts the model DID return,
# and never overrides a judgment.
#
# Why it is done this way:
#   - LLM arithmetic is unreliable. It will occasionally drop a deduction or
#     average four numbers slightly wrong, and there is no way to tell from
#     the output that it happened.
#   - If the model returned both sub-scores and a total, the two could
#     disagree and there would be no principled way to pick one.
#   - Having the model total it up means telling it what each rule costs,
#     which makes it lenient on the expensive ones. _rubric_text() strips the
#     Tier-3 deduction column for exactly this reason.
#   - Identical verdicts now always produce an identical composite.
#
# The arithmetic itself:
#   base       = weighted sum of the four 1-5 dimension scores, x20
#                (achievable range 20-100, since the lowest average is 1)
#   deductions = config.DEDUCTION_POINTS per violated compliance rule
#   composite  = max(0, base - deductions)
#
# With all four weights at 0.25 the weighted sum is just the average, which is
# what rubric.md specifies. They are applied per-dimension so re-weighting one
# is a config.DIMENSION_WEIGHTS change and nothing else (they must still sum
# to 1.0). Deduction values are config.DEDUCTION_POINTS. Neither requires a
# prompt change or a re-run -- which is what rubric.md means when it says
# weights and deduction points are config values.


def score(verdict, template_type):
    """
    Turn a judge verdict into the scored report: per-dimension detail, failed
    rules with their deductions, and the composite out of 100.
    """
    weights = dict(config.DIMENSION_WEIGHTS)
    missing = [name for name in config.DIMENSIONS if name not in weights]
    if missing:
        raise ValueError(f"config.DIMENSION_WEIGHTS is missing an entry for: {missing}")
    total_weight = sum(weights[name] for name in config.DIMENSIONS)
    if abs(total_weight - 1.0) > 1e-9:
        raise ValueError(
            f"config.DIMENSION_WEIGHTS must sum to 1.0 across {list(config.DIMENSIONS)}, "
            f"got {total_weight}."
        )

    dimensions = {}
    weighted = 0.0
    for name in config.DIMENSIONS:
        entry = verdict["dimensions"][name]
        weighted += entry["score"] * weights[name]
        dimensions[name] = {
            "label": config.DIMENSION_LABELS[name],
            "score": entry["score"],
            "weight": weights[name],
            "rationale": entry.get("rationale", ""),
            "evidence": entry.get("evidence", []),
        }

    base_score = weighted * config.DIMENSION_SCORE_MULTIPLIER

    rules = []
    total_deductions = 0
    for rule in applicable_rules(template_type):
        entry = verdict["rules"][rule["id"]]
        violated = entry["violated"]
        deduction = config.DEDUCTION_POINTS[rule["severity"]] if violated else 0
        total_deductions += deduction
        rules.append({
            "id": rule["id"],
            # Matches the rubric's Tier-3 column heading: each row describes the
            # violation, and `passed` is True when it did NOT occur.
            "violation": rule["description"],
            "severity": rule["severity"],
            "passed": not violated,
            "deduction": deduction,
            "evidence": entry.get("evidence", ""),
        })

    composite = max(0, base_score - total_deductions)

    return {
        "overall_score": int(round(composite)),
        "base_score": round(base_score, 1),
        "total_deductions": total_deductions,
        "dimensions": dimensions,
        "rules": rules,
        "room_sequence": verdict.get("room_sequence", []),
        "showcase_summary": verdict.get("showcase_summary", ""),
        "summary": verdict.get("summary", ""),
        "top_fixes": verdict.get("top_fixes", []),
        "consistency_warnings": verdict.get("consistency_warnings", []),
    }


# ============================================================================
# ORCHESTRATION
# ============================================================================
GATE_FAIL_REASON = (
    "Failed on technical quality: noticeable motion issues detected "
    "(lurches / stutters / glitches / unsmooth panning)"
)


def _gate_fail_reason(issues):
    """
    The rubric mandates this exact reason string, so it is emitted verbatim.
    The rubric separately requires the timestamps be reported, which they are:
    the specific issues and their MM:SS live in gate["issues"], and are appended
    here only as a human-readable trailer.
    """
    detail = ", ".join(f'{i["type"]} at {i["timestamp"]}' for i in issues)
    return f"{GATE_FAIL_REASON}. Detected: {detail}." if detail else GATE_FAIL_REASON


def _empty_result(source, template_type, gate, reason, overall_score=None):
    """The result envelope, with everything downstream of the gate left blank."""
    return {
        "source": source,
        "template_type": template_type,
        "gate": gate,
        "overall_score": overall_score,
        "reason": reason,
        "room_sequence": [],
        "dimensions": {},
        "rules": [],
        "base_score": 0,
        "total_deductions": 0,
        "showcase_summary": "",
        "summary": "",
        "top_fixes": [],
        "consistency_warnings": [],
        "judged": False,
    }


def evaluate_video(source, template_type, judge=True):
    """
    Run the full pipeline for one video: ingest -> CV gate -> (if the gate
    passes) Gemini judge -> composite score.

    template_type: one of "short", "medium", "long".
    judge:         set False to stop after the CV gate (threshold calibration,
                   or running without an API key).

    A video that fails the Tier-1 gate scores 0 and the judge is skipped
    entirely, per the rubric -- that is a deliberate cost saving as well as a
    scoring rule, since there is no point paying to judge footage that is
    already disqualified.
    """
    if template_type not in ("short", "medium", "long"):
        raise ValueError(f"template_type must be one of 'short', 'medium', 'long', got {template_type!r}")

    local_path, is_temp, _ = ingest(source)
    try:
        gate = run_cv_gate(local_path)

        if not gate["passed"]:
            return _empty_result(
                source, template_type, gate,
                reason=_gate_fail_reason(gate["issues"]),
                overall_score=0,
            )

        if not judge:
            return _empty_result(
                source, template_type, gate,
                reason="Gate passed -- judge skipped (judge=False)",
            )

        verdict = run_judge(local_path, template_type)
        scored = score(verdict, template_type)

        result = _empty_result(source, template_type, gate, reason="")
        result.update(scored)
        result["judged"] = True
        result["reason"] = (
            f"Gate passed. Composite {scored['overall_score']}/100 "
            f"(base {scored['base_score']} - {scored['total_deductions']} in deductions)."
        )
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
    parser.add_argument("--no-judge", action="store_true",
                         help="Stop after the CV gate; skip the Gemini judge and scoring (no API key needed)")
    args = parser.parse_args()

    try:
        if args.metrics_only:
            _cli_metrics_only(args.source)
        else:
            if not args.template:
                parser.error("--template is required unless --metrics-only is set")
            result = evaluate_video(args.source, args.template, judge=not args.no_judge)
            output = json.dumps(result, indent=2)
            if args.output:
                Path(args.output).write_text(output)
                print(f"Result written to {args.output}", file=sys.stderr)
            else:
                print(output)
    except (FileNotFoundError, RuntimeError, ValueError, requests.exceptions.RequestException) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
