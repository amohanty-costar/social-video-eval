"""
video-eval-tool.py
==================
PURPOSE: The web app. This is what users open. Screen layout only, no
evaluation logic.

Upload a video or paste a path, pick the template type, get a quality report.
All the actual work is done by evaluate.py's evaluate_video(), so the app can
never disagree with the CLI.

Run with: streamlit run video-eval-tool.py
"""

import html
import json
import tempfile
from pathlib import Path

import streamlit as st

import config
import evaluate


# ============================================================================
# SCORE SPECTRUM BAR
# ============================================================================
# The red/amber/green track under the composite score. Purely presentational --
# it renders numbers that evaluate.py::score() already computed and changes
# nothing about them.
#
# What is on it:
#   - three coloured zones, widths and colours from config.SCORE_BANDS
#   - faint ticks at every multiple of 5, because those are the only scores
#     that can actually occur (see the SCORING block in evaluate.py). Without
#     them a smooth bar implies a precision the score does not have.
#   - a tall dark NEEDLE at the composite score
#   - a thinner marker at the base score, and a hatched region between the two,
#     showing what compliance deductions took away
#
# Streamlit has no primitive for this, so it is inline HTML in one st.markdown
# call. Every interpolated value is a float we computed or is escaped, so
# unsafe_allow_html is safe here -- do not interpolate model output into it.
# ============================================================================
def _pct(value):
    """Clamp a 0-100 score to a percentage string usable as a CSS offset."""
    return f"{max(0.0, min(100.0, float(value))):.4f}%"


def render_score_spectrum(composite, base, deductions):
    """Draw the banded spectrum for one result. Returns nothing; writes to the page."""
    label, bar_colour, chip_bg, chip_fg = config.score_band(composite)

    # Zone widths come straight from the configured cut-points, so editing
    # config.SCORE_BANDS moves the colours without touching this code.
    ordered = sorted(config.SCORE_BANDS, key=lambda b: b[0])  # low -> high
    zones = []
    for i, (lower, _lbl, colour, _bg, _fg) in enumerate(ordered):
        upper = ordered[i + 1][0] if i + 1 < len(ordered) else 100.0
        width = max(0.0, upper - lower)
        if width:
            zones.append(f'<div class="sb-zone" style="width:{width:.4f}%;background:{colour};"></div>')

    # Band captions, centred in their own zone.
    captions = []
    for i, (lower, lbl, _c, _bg, _fg) in enumerate(ordered):
        upper = ordered[i + 1][0] if i + 1 < len(ordered) else 100.0
        captions.append(
            f'<span style="left:{(lower + upper) / 2:.4f}%">{html.escape(lbl)}</span>'
        )

    ticks = "".join(
        f'<div class="sb-tick{" sb-major" if v % 25 == 0 else ""}" style="left:{v}%"></div>'
        for v in range(5, 100, 5)
    )
    scale = "".join(f'<span style="left:{v}%">{v}</span>' for v in (0, 25, 50, 75, 100))

    # The hatched "lost to deductions" region only exists if points were lost.
    lost = ""
    base_mark = ""
    if deductions and base > composite:
        lost = (f'<div class="sb-lost" style="left:{_pct(composite)};'
                f'width:{max(0.0, min(100.0, base) - max(0.0, composite)):.4f}%"></div>')
        base_mark = f'<div class="sb-base" style="left:{_pct(base)}"></div>'

    st.markdown(
        f"""
<style>
.sb-box {{ margin: 2px 0 6px; }}
.sb-chip {{ display:inline-block; font-size:13px; font-weight:600; padding:3px 10px;
            border-radius:20px; background:{chip_bg}; color:{chip_fg}; margin-bottom:14px; }}
.sb-track {{ position:relative; height:22px; border-radius:4px; display:flex; }}
.sb-zone:first-child {{ border-radius:4px 0 0 4px; }}
.sb-zone:last-child {{ border-radius:0 4px 4px 0; }}
.sb-ticks {{ position:absolute; inset:0; overflow:hidden; border-radius:4px; pointer-events:none; }}
.sb-tick {{ position:absolute; top:0; bottom:0; width:1px; background:rgba(255,255,255,.40); }}
.sb-tick.sb-major {{ background:rgba(255,255,255,.75); }}
.sb-lost {{ position:absolute; top:0; bottom:0;
            background-image:repeating-linear-gradient(135deg,
              rgba(0,0,0,.34) 0 4px, rgba(0,0,0,.10) 4px 8px); }}
.sb-base {{ position:absolute; top:-2px; bottom:-2px; width:2px;
            background:rgba(0,0,0,.45); border-radius:2px; }}
.sb-needle {{ position:absolute; top:-9px; bottom:-9px; width:4px;
              background:#12100e; border-radius:2px; box-shadow:0 0 0 3px rgba(255,255,255,.92); }}
.sb-cap {{ position:absolute; top:-16px; transform:translateX(-50%); width:0; height:0;
           border-left:5px solid transparent; border-right:5px solid transparent;
           border-top:7px solid #12100e; }}
.sb-bands {{ position:relative; height:17px; margin-top:7px; font-size:11px; opacity:.75; }}
.sb-bands span {{ position:absolute; transform:translateX(-50%); white-space:nowrap; }}
.sb-scale {{ position:relative; height:15px; font-size:10.5px; opacity:.55;
             font-variant-numeric:tabular-nums; }}
.sb-scale span {{ position:absolute; transform:translateX(-50%); }}
.sb-legend {{ font-size:11.5px; opacity:.7; margin-top:9px; }}
</style>
<div class="sb-box">
  <span class="sb-chip">{html.escape(label)}</span>
  <div class="sb-track">
    {''.join(zones)}
    <div class="sb-ticks">{ticks}</div>
    {lost}
    {base_mark}
    <div class="sb-cap" style="left:{_pct(composite)}"></div>
    <div class="sb-needle" style="left:{_pct(composite)}"></div>
  </div>
  <div class="sb-bands">{''.join(captions)}</div>
  <div class="sb-scale">{scale}</div>
  {'<div class="sb-legend">Needle = composite &nbsp;·&nbsp; thin line = base before deductions'
   ' &nbsp;·&nbsp; hatched = lost to compliance rules &nbsp;·&nbsp;'
   ' ticks = the only reachable scores</div>' if lost else
   '<div class="sb-legend">Needle = composite &nbsp;·&nbsp;'
   ' ticks = the only reachable scores</div>'}
</div>
""",
        unsafe_allow_html=True,
    )

st.set_page_config(page_title="Auto Video Quality Evaluator", layout="wide")
st.title("Auto Video Quality Evaluator")
st.markdown(
    "A two-layer quality evaluation tool for Auto Video: OpenCV analyzes the raw frames "
    "for technical defects like jitter, stutter, and dropped frames, while Gemini acts as "
    "an LLM-as-a-Judge to score the video against a quality rubric."
)
st.caption(
    "Ready to go! "
    "Upload an .mp4 or paste a direct video link from QA3/ Matterport, pick your template "
    "type (short, medium, or long), and hit Evaluate!"
)

has_key = bool(config.gemini_api_key())
if not has_key:
    st.warning(
        "No Gemini API key found, so only the CV gate can run. Add "
        "`GEMINI_API_KEY` to `.streamlit/secrets.toml` (or set it as an "
        "environment variable) and restart to enable the rubric judge."
    )

with st.form("evaluate_form"):
    col1, col2 = st.columns([3, 1])
    with col1:
        source_url = st.text_input(
            "Video URL or local file path",
            placeholder="https://... or /path/to/video.mp4",
        )
    with col2:
        template = st.selectbox("Template", ["short", "medium", "long"])
    uploaded = st.file_uploader("...or upload a video file", type=["mp4", "mov", "m4v"])
    run_judge = st.checkbox(
        "Run the Gemini rubric judge",
        value=has_key,
        disabled=not has_key,
        help="Uncheck to run only the Tier-1 CV gate — useful for calibrating thresholds "
             "without spending API calls.",
    )
    submitted = st.form_submit_button("Evaluate", use_container_width=True)


def _resolve_input(source_url, uploaded_file):
    """Return (source, cleanup_path). cleanup_path is a temp file to delete after evaluation, if any."""
    if uploaded_file is not None and source_url:
        st.info("Both a URL and an upload were provided — evaluating the uploaded file.")
    if uploaded_file is not None:
        suffix = Path(uploaded_file.name).suffix or ".mp4"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(uploaded_file.read())
        tmp.close()
        return tmp.name, tmp.name
    if source_url:
        return source_url, None
    return None, None


if submitted:
    source, cleanup_path = _resolve_input(source_url, uploaded)

    if not source:
        st.error("Provide a video URL/path, or upload a file, before evaluating.")
    else:
        spinner_text = (
            "Analyzing video — the CV gate decodes every frame, then Gemini judges the "
            "rubric. This can take a few minutes..."
            if run_judge
            else "Analyzing video — the CV gate decodes every frame, this can take a minute or two..."
        )

        result, error = None, None
        try:
            with st.spinner(spinner_text):
                result = evaluate.evaluate_video(source, template, judge=run_judge)
        except Exception as e:
            error = str(e)
        finally:
            if cleanup_path:
                Path(cleanup_path).unlink(missing_ok=True)

        if error:
            st.error(f"Evaluation failed: {error}")

        if result:
            gate = result["gate"]

            if gate["passed"]:
                st.success("Gate: PASSED")
            else:
                st.error(f"Gate: FAILED — {result['reason']}")
                # The rubric assigns gate failures a score of 0 and asks for the
                # composite as the headline, so show it here too.
                #
                # Deliberately NO spectrum bar on this path: the bands describe
                # how a video did against the Tier-2/Tier-3 rubric, and a
                # gate-failed video was never judged against it. A needle pinned
                # at 0 in the red zone would imply "scored badly on quality"
                # when the truth is "disqualified before quality was assessed".
                st.metric("Composite score", "0/100")

            st.subheader("Gate metrics")
            m = gate["metrics"]
            mc = st.columns(3)
            mc[0].metric("Duration", f"{m['duration_sec']}s")
            mc[1].metric("Resolution", m["resolution"])
            mc[2].metric("FPS", m["fps"])
            mc2 = st.columns(3)
            mc2[0].metric("Duplicate frames", m["duplicate_frames"])
            mc2[1].metric("Glitch frames", m["glitch_frames"])
            mc2[2].metric("Fade regions detected", m["fade_regions_detected"])

            st.subheader(f"Issues ({len(gate['issues'])})" if gate["issues"] else "No issues detected")
            if gate["issues"]:
                st.dataframe(gate["issues"], use_container_width=True)

            if gate["passed"]:
                if result["judged"]:
                    st.divider()

                    if result["consistency_warnings"]:
                        st.warning(
                            "The judge contradicted its own room sequence on "
                            + ("these rules:\n\n" if len(result["consistency_warnings"]) > 1
                               else "this rule:\n\n")
                            + "\n\n".join(f"- {w}" for w in result["consistency_warnings"])
                            + "\n\nThe scores below use the judge's verdicts as returned. "
                              "Worth an eyeball before you trust the deduction."
                        )

                    # ------------------------------------------------------
                    # REPORT ORDER
                    #   1. what the video showcases       prompt.md item 1
                    #   2. room sequence                  prompt.md item 2
                    #   3. composite score (headline)     COMPUTED HERE-ish:
                    #      not from Gemini. evaluate.py::score() calculates it
                    #      from the sub-scores and rule verdicts the model DID
                    #      return; this line just displays it. See the SCORING
                    #      comment block in evaluate.py for why the arithmetic
                    #      is kept out of the model's hands.
                    #   4. why it scored that way + fixes prompt.md item 3
                    #   5. dimension sub-scores           prompt.md item 4
                    #   6. failed compliance rules        prompt.md item 5
                    #
                    # prompt.md asks the model for 5 things and never mentions
                    # a composite; this page shows 6 sections, slotting the
                    # computed score in at position 3. Everything else here is
                    # the model's own output. Reorder in both files together --
                    # test_judge_scoring.py fails if they drift apart.
                    #
                    # Display order is not generation order: the model emits
                    # evidence and rationale before each score, enforced by
                    # field order in evaluate.py::_response_schema().
                    # ------------------------------------------------------

                    # 1. What the video showcases
                    if result["showcase_summary"]:
                        st.subheader("What this video showcases")
                        st.write(result["showcase_summary"])

                    # 2. Room sequence
                    st.subheader("Room sequence")
                    if result["room_sequence"]:
                        st.dataframe(result["room_sequence"], use_container_width=True)
                    else:
                        st.caption("No room sequence returned.")

                    # 3. Composite score as the headline, with the spectrum bar
                    sc = st.columns(3)
                    sc[0].metric("Composite score", f"{result['overall_score']}/100")
                    sc[1].metric("Base (dimensions)", result["base_score"])
                    sc[2].metric("Deductions", f"-{result['total_deductions']}")
                    render_score_spectrum(
                        result["overall_score"],
                        result["base_score"],
                        result["total_deductions"],
                    )

                    # 4. Why it scored that way, then the top fixes
                    if result["summary"]:
                        st.subheader("Why it scored this way")
                        st.write(result["summary"])

                    if result["top_fixes"]:
                        st.subheader("Top fixes")
                        for i, fix in enumerate(result["top_fixes"], 1):
                            st.markdown(f"{i}. {fix}")

                    # 5. The four dimension sub-scores
                    st.subheader("Dimension scores")
                    for dim in result["dimensions"].values():
                        st.markdown(f"**{dim['label']} — {dim['score']}/5**")
                        st.write(dim["rationale"])
                        if dim["evidence"]:
                            st.caption("Evidence: " + ", ".join(dim["evidence"]))

                    # 6. Failed compliance rules. Deduction values come from
                    #    config.DEDUCTION_POINTS via score(), never from the model.
                    failed = [r for r in result["rules"] if not r["passed"]]
                    st.subheader(
                        f"Compliance rules — {len(failed)} of {len(result['rules'])} failed"
                    )
                    st.dataframe(
                        [
                            {
                                "Severity": r["severity"],
                                "Violation": r["violation"],
                                "Result": "PASS" if r["passed"] else "FAIL",
                                "Deduction": -r["deduction"] if r["deduction"] else 0,
                                "Evidence": r["evidence"],
                            }
                            for r in result["rules"]
                        ],
                        use_container_width=True,
                    )
                else:
                    st.info(result["reason"])

            with st.expander("Raw JSON"):
                st.json(result)

            st.download_button(
                "Download report as JSON",
                data=json.dumps(result, indent=2),
                file_name="video_eval_report.json",
                mime="application/json",
            )
