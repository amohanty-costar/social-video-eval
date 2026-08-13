"""
config.py
=========
PURPOSE: Every setting you might want to change, in one place. No logic here.

Holds the CV gate thresholds, the Gemini model settings, the compliance rule
table, the scoring weights and deductions, the score bands, and the API key
lookup. Edit this file rather than evaluate.py.

Every threshold marked CALIBRATION-PENDING is a starting guess only. Run
`python evaluate.py --metrics-only --source <video>` against known-good and
known-bad sample videos and adjust these until the raw metrics cleanly
separate the two groups.
"""

# ----------------------------------------------------------------------------
# INGEST
# ----------------------------------------------------------------------------
DOWNLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MB streaming chunks for URL downloads
DOWNLOAD_TIMEOUT_SEC = 60

# ----------------------------------------------------------------------------
# CV GATE — shared
# ----------------------------------------------------------------------------
WORK_W = 540  # downscale width for all CV math (speed only; signals unaffected)

# ----------------------------------------------------------------------------
# CV GATE — fade-to-black masking
# ----------------------------------------------------------------------------
# These videos intentionally fade to black between shots. Fades are NOT a
# rubric issue, but they produce large brightness/frame-diff swings that would
# otherwise look like stutters/glitches/jerk. This block finds fade regions
# purely so they can be excluded from those three signals.
FADE_DARK_FRAC = 0.18       # CALIBRATION-PENDING: "near-black" = brightness < this fraction of the shot's normal (75th-pct) brightness
FADE_MASK_BUFFER_FRAMES = 8  # CALIBRATION-PENDING: extra frames excluded on each side of a detected fade
FADE_HOLD_DIFF_THRESHOLD = 2.0  # CALIBRATION-PENDING: mean abs pixel diff below this = part of the pre-fade "hold" (settle on final frame before fading)
FADE_HOLD_MAX_FRAMES = 60       # CALIBRATION-PENDING: safety cap on how far back the pre-fade hold can extend (2s @ 30fps)

# ----------------------------------------------------------------------------
# CV GATE — lurch (ported from the validated video_quality_eval.py detector)
# ----------------------------------------------------------------------------
LURCH_MIN_MATCHES = 15      # CALIBRATION-PENDING: below this, a global-motion estimate is untrustworthy
LURCH_REL = 1.8              # CALIBRATION-PENDING: lurch = motion speed >= this multiple of the shot's own steady pan
LURCH_MIN_SPEED = 1.5        # CALIBRATION-PENDING: ignore micro-spikes below this absolute speed (px/frame @ WORK_W)
LURCH_LOOKBACK = 14          # CALIBRATION-PENDING: frames before a fade to search for the pre-fade lurch

# ----------------------------------------------------------------------------
# CV GATE — stutter / duplicate frames (mean absolute frame difference)
# ----------------------------------------------------------------------------
STUTTER_DIFF_THRESHOLD = 2.0     # CALIBRATION-PENDING: mean abs pixel diff below this = duplicate/frozen frame
STUTTER_MIN_RUN_FRAMES = 5       # CALIBRATION-PENDING: consecutive duplicate frames needed to count as one stutter event
STUTTER_RATE_THRESHOLD = 0.15    # CALIBRATION-PENDING: fraction of (non-masked) frames that are duplicates -> fail even without one long run

# ----------------------------------------------------------------------------
# CV GATE — glitch (frame-diff outlier vs. both neighbors)
# ----------------------------------------------------------------------------
GLITCH_STD_MULTIPLIER = 5.0  # CALIBRATION-PENDING: frame-diff must exceed mean + N*std (vs. both neighbors) to count as a glitch

# ----------------------------------------------------------------------------
# CV GATE — jerk / unsmooth panning (Farneback dense optical flow)
# ----------------------------------------------------------------------------
FARNEBACK_PARAMS = dict(
    pyr_scale=0.5, levels=3, winsize=15, iterations=3,
    poly_n=5, poly_sigma=1.2, flags=0,
)
JERK_THRESHOLD = 1.5          # CALIBRATION-PENDING: frame-to-frame change in flow magnitude counted as "jerky"
JERK_SUSTAINED_WINDOW = 10    # CALIBRATION-PENDING: number of frames over which high jerk must persist to fail the gate
JERK_SUSTAINED_FRACTION = 0.6  # CALIBRATION-PENDING: fraction of the window that must exceed JERK_THRESHOLD

# ----------------------------------------------------------------------------
# JUDGE (Gemini)
# ----------------------------------------------------------------------------
# Named explicitly rather than via the `gemini-flash-latest` alias, which is
# hot-swapped across model generations and would silently move the scores out
# from under a calibrated rubric. Note this is still a family name and tracks
# the latest stable revision of 3.6 Flash -- it just won't jump to a 4.x. For
# a fully frozen judge, use a dated revision id if Google publishes one.
GEMINI_MODEL = "gemini-3.6-flash"

# None = send no temperature and use the model default of 1.0. Gemini 3 models
# are tuned around their default sampling; Google documents that lowering
# temperature can cause looping and degraded reasoning, so the old "0.1 for
# deterministic output" habit backfires here. Determinism comes from the
# response schema instead.
GEMINI_TEMPERATURE = None

GEMINI_MAX_RETRIES = 5
GEMINI_BACKOFF_BASE_SEC = 2  # first retry waits this long, then doubles (2, 4, 8, 16s)

# Files API upload -> the video sits in PROCESSING until Gemini has ingested
# it; it cannot be referenced in a prompt before it reaches ACTIVE.
GEMINI_UPLOAD_POLL_SEC = 5
GEMINI_UPLOAD_TIMEOUT_SEC = 600
GEMINI_DELETE_UPLOAD_AFTER_USE = True  # don't leave listing footage in Gemini's file store

# Gemini samples video for visual understanding at 1 FPS. Nothing here can
# change that, so a ~30s tour is judged on ~30 frames -- fine for room
# sequencing, thin for the "first ~3s" hook (3 frames). The CV gate is what
# sees every frame; the judge is deliberately the coarse pass.
# None = model default. Valid values are the SDK's MediaResolution names:
# "MEDIA_RESOLUTION_LOW" (64 tokens/frame), "..._MEDIUM" (256), "..._HIGH" --
# so LOW is roughly 4x cheaper than MEDIUM, at the cost of fine detail like
# tilted horizons and cramped crops, which the Framing dimension depends on.
# Lowercase strings like "low" are accepted by the SDK with only a UserWarning
# and then ignored, so always use the full name.
GEMINI_MEDIA_RESOLUTION = None

# ----------------------------------------------------------------------------
# WHERE THE PROMPT AND THE RUBRIC LIVE
# ----------------------------------------------------------------------------
# Both are plain files loaded at runtime by evaluate.py, not baked into the
# Python. Edit either one and the next evaluation picks it up -- no code change
# and no redeploy needed.
#
#   prompt.md  the instruction sent to Gemini. Contains a {{TEMPLATE_TYPE}}
#              placeholder and an empty <rubric></rubric> block.
#   rubric.md  the evaluation rubric. Injected into that <rubric> block, with
#              Tier 1, the Deduction column and the Final score section
#              stripped out first (see evaluate.py::_rubric_text).
#
# Paths are resolved relative to evaluate.py, not the working directory, so the
# CLI works from anywhere.
PROMPT_PATH = "prompt.md"
RUBRIC_PATH = "rubric.md"

# Machine-readable form of the rubric's Tier-3 table. rubric.md is still the
# source of truth for wording and nuance (it is pasted into the prompt
# verbatim); this list is what the response schema and the scoring math are
# built from, so the two must be kept in step.
#   description: the violation, phrased exactly as the rubric's Violation column
#   applies:     which template types the rule is evaluated for
# "violated" means the described violation has occurred -> deduct.
COMPLIANCE_RULES = [
    {"id": "kitchen_not_shown", "severity": "critical", "applies": ("short", "medium", "long"),
     "description": "Kitchen not shown at all"},
    {"id": "living_room_not_shown", "severity": "critical", "applies": ("short", "medium", "long"),
     "description": "Living room not shown at all"},
    {"id": "closet_shown", "severity": "critical", "applies": ("short", "medium", "long"),
     "description": "Closet shown"},
    {"id": "laundry_room_shown", "severity": "critical", "applies": ("short", "medium", "long"),
     "description": "Laundry room shown"},
    {"id": "bathroom_shown_prohibited", "severity": "critical", "applies": ("short",),
     "description": "Bathroom shown (prohibited in Short)"},
    {"id": "bathroom_not_shown_required", "severity": "critical", "applies": ("medium", "long"),
     "description": "Bathroom not shown (required in Medium/Long)"},
    {"id": "kitchen_not_in_first_3_stops", "severity": "moderate", "applies": ("short", "medium", "long"),
     "description": "Kitchen not within the first 3 stops"},
    {"id": "living_room_not_in_first_3_stops", "severity": "moderate", "applies": ("short", "medium", "long"),
     "description": "Living room not within the first 3 stops"},
    {"id": "panning_into_blank_walls", "severity": "moderate", "applies": ("short", "medium", "long"),
     "description": "Panning into blank walls / dead space"},
    {"id": "back_and_forth_panning", "severity": "moderate", "applies": ("short", "medium", "long"),
     "description": "Back-and-forth panning over the same room"},
]

# The four Tier-2 dimensions, in report order.
DIMENSIONS = ("tour_flow", "hook_opening", "emotional_appeal", "framing_composition")

DIMENSION_LABELS = {
    "tour_flow": "Tour Flow & Sequencing",
    "hook_opening": "Hook / Opening",
    "emotional_appeal": "Emotional / Aspirational Appeal",
    "framing_composition": "Framing & Composition",
}

# ----------------------------------------------------------------------------
# SCORING
# ----------------------------------------------------------------------------
# Per-dimension weights, so a single dimension can be re-weighted without
# touching the scoring code. Must sum to 1.0. Equal weighting (0.25 each) is
# what rubric.md currently specifies, which makes the weighted sum the plain
# average.
DIMENSION_WEIGHT = 0.25  # the equal-weight default, kept for reference

DIMENSION_WEIGHTS = {
    "tour_flow": 0.25,
    "hook_opening": 0.25,
    "emotional_appeal": 0.25,
    "framing_composition": 0.25,
}

# 1-5 dimension scale -> base score. Achievable base range is 20-100, since
# the lowest possible dimension average is 1.
#
# These values feed the composite score, which is calculated in
# evaluate.py::score() -- NOT by the LLM. The model returns the four
# sub-scores and the rule verdicts; the arithmetic happens in code. See the
# SCORING comment block in evaluate.py for the reasoning.
DIMENSION_SCORE_MULTIPLIER = 20

DEDUCTION_POINTS = {
    "critical": 10,
    "moderate": 5,
}

# ----------------------------------------------------------------------------
# SCORE BANDS (presentation only)
# ----------------------------------------------------------------------------
# The red/amber/green spectrum shown under the composite score in the web app.
#
# IMPORTANT: these bands are NOT part of rubric.md. The rubric defines how to
# reach a score out of 100 and stops there -- it never says 70 is "Strong".
# These cut-points are an interpretation layered on top, and they live here
# rather than buried in the app so that the choice is explicit and calibratable
# alongside the weights and deduction points. Nothing in the scoring or the
# judge depends on them; change them freely.
#
# The defaults map to dimension averages, which is what makes them defensible:
#   67.5  ->  the model averaged 3.5+ across the four dimensions   -> Strong
#   47.5  ->  the model averaged 2.5+                              -> Fair
#   below ->                                                       -> Weak
#
# Why the cut-points are .5 rather than round numbers: every reachable score is
# a multiple of 5 (four integer 1-5 scores, averaged, x20 -- see the SCORING
# block in evaluate.py). A cut-point at exactly 70 would put a real, common
# score right on a colour boundary, where the marker straddles two bands and
# the label looks arbitrary. Offsetting by 2.5 means every possible score sits
# unambiguously inside one band.
#
# Each entry is (lower_bound_inclusive, label, bar_colour, chip_bg, chip_text).
SCORE_BANDS = [
    (67.5, "Strong", "#5e8c4a", "#e3efdf", "#3d6b2e"),
    (47.5, "Fair",   "#c9a227", "#f7eed2", "#7a5f14"),
    (0.0,  "Weak",   "#c2622f", "#f6e3dc", "#8f3d24"),
]


def score_band(score):
    """
    The band a composite score falls into, as
    (label, bar_colour, chip_bg, chip_text).

    Bands are checked highest-first, so the first match wins. Falls back to the
    lowest band if SCORE_BANDS has been edited into something that doesn't
    cover 0 -- a mis-edited config should not crash the report.
    """
    for lower, label, bar, chip_bg, chip_fg in SCORE_BANDS:
        if score >= lower:
            return label, bar, chip_bg, chip_fg
    lower, label, bar, chip_bg, chip_fg = SCORE_BANDS[-1]
    return label, bar, chip_bg, chip_fg


# ----------------------------------------------------------------------------
# CREDENTIALS
# ----------------------------------------------------------------------------
def gemini_api_key():
    """
    Resolve the Gemini API key, in priority order:
      1. GEMINI_API_KEY environment variable (works for the CLI, cron, CI)
      2. GOOGLE_API_KEY environment variable (the SDK's other conventional name)
      3. .streamlit/secrets.toml (how the Streamlit app and Streamlit Cloud
         supply it)
    Returns None if no key is configured anywhere; callers decide whether that
    is fatal.
    """
    import os

    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        key = os.environ.get(var)
        if key:
            return key.strip()

    # st.secrets raises (not returns None) when there is no secrets.toml at
    # all, and importing streamlit outside a Streamlit process is harmless but
    # noisy -- so this is best-effort only.
    try:
        import streamlit as st

        for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            if var in st.secrets:
                return str(st.secrets[var]).strip()
    except Exception:
        pass

    return None
