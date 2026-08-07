"""
config.py
=========
All tunable values for the evaluation pipeline: CV gate thresholds, the
Gemini model/prompt settings, and the scoring weights/deductions.

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
GEMINI_MODEL = "gemini-flash-latest"  # placeholder pending SDK/model verification in Part 5
GEMINI_TEMPERATURE = 0.1
GEMINI_MAX_RETRIES = 5
GEMINI_BACKOFF_BASE_SEC = 2  # exponential backoff base for HTTP 429s

RUBRIC_PATH = "rubric.md"

# ----------------------------------------------------------------------------
# SCORING
# ----------------------------------------------------------------------------
DIMENSION_WEIGHT = 0.25  # equal weighting across the 4 Tier-2 dimensions (must sum to 1.0)

DEDUCTION_POINTS = {
    "critical": 10,
    "moderate": 5,
}
