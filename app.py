"""
Property Marketing Video — Quality Evaluation
=============================================
Streamlit scaffold (proof of concept).

This file is a SKELETON. It runs as-is with dummy data so you can see the
layout, click around, and demo the flow. Wherever you see `# TODO`, that's
where your real deliverable-3 logic plugs in.

Design principle (keep this true as you build):
    The dashboard contains NO evaluation logic of its own. It only calls
    `evaluate_video(...)` and renders the result. The exact same function is
    what your batch script calls. One source of truth -> dashboard and batch
    stay consistent, which is the clean story for your final presentation.

Run it:
    pip install streamlit plotly pandas
    streamlit run app.py
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TEMPLATES = ["Short", "Medium", "Long", "Guided Tour"]

# Expected duration (seconds) per template — used to flag "too long / too short".
# TODO: set these to whatever your templates actually target.
EXPECTED_DURATION = {
    "Short": (15, 30),
    "Medium": (30, 60),
    "Long": (60, 120),
    "Guided Tour": (90, 180),
}

# The rubric dimensions. These should mirror your criterion-2 rubric exactly.
# TODO: replace with your finalized rubric dimensions.
RUBRIC_DIMENSIONS = [
    "Pacing",
    "Visual Quality",
    "Storytelling Arc",
    "Branding",
    "Audio Fit",
    "Platform Suitability",
]

PASS_THRESHOLD = 7.0  # out of 10. TODO: set your real quality bar.


# ---------------------------------------------------------------------------
# Evaluation function  (THE PLUG-IN POINT)
# ---------------------------------------------------------------------------
# This is the single shared entry point. Your batch script imports and calls
# this same function. Right now it returns dummy data so the UI renders.

def evaluate_video(video_url: str, template: str) -> dict:
    """Run the full evaluation for one video and return a structured report.

    Returns a dict shaped like the DUMMY_RESULT below. Keep this shape stable
    so the dashboard and batch script both rely on the same contract.
    """
    # TODO: 1. Download the video (e.g. yt-dlp) from video_url
    # TODO: 2. Sample frames + compute CV metrics
    # TODO: 3. Send sampled frames + metadata to your vision LLM with the rubric prompt
    # TODO: 4. Assemble everything into the dict below
    return _dummy_result(video_url, template)


def _dummy_result(video_url: str, template: str) -> dict:
    """Fake but realistically-shaped result so the UI has something to show."""
    rubric_scores = {
        "Pacing": 8.0,
        "Visual Quality": 7.5,
        "Storytelling Arc": 6.0,
        "Branding": 5.5,
        "Audio Fit": 7.0,
        "Platform Suitability": 8.5,
    }
    overall = round(sum(rubric_scores.values()) / len(rubric_scores), 1)
    return {
        "video_url": video_url,
        "template": template,
        "overall_score": overall,
        "passed": overall >= PASS_THRESHOLD,
        "rubric_scores": rubric_scores,
        "rubric_justifications": {
            "Pacing": "Shot changes are brisk and hold attention through the tour.",
            "Visual Quality": "Sharp, well-exposed footage; minor shake in two shots.",
            "Storytelling Arc": "Strong hook, but the tour meanders in the middle third.",
            "Branding": "Logo appears only once; no consistent brand color or handle.",
            "Audio Fit": "Music energy matches the pacing; slightly loud over voiceover.",
            "Platform Suitability": "Correct vertical framing and length for Reels.",
        },
        "cv_metrics": {
            "Resolution": "1080x1920",
            "Frame rate": "30 fps",
            "Duration": "0:24",
            "Duration vs target": "OK (target 15-30s)",
            "Shot count": 9,
            "Avg shot length": "2.7s",
            "Sharpness": "Good",
            "Exposure": "Balanced",
            "Camera shake": "Low",
            "Audio present": "Yes",
        },
        "strengths": [
            "Fast, platform-appropriate pacing.",
            "Clean, well-lit footage throughout.",
            "Correct vertical format and length for the target platform.",
        ],
        "weaknesses": [
            "Weak branding — logo shown only once, no handle or brand color.",
            "No clear call-to-action in the final seconds.",
            "Middle of the tour loses narrative momentum.",
        ],
        "timestamped_issues": [
            {"time": "0:12", "issue": "Abrupt transition between rooms."},
            {"time": "0:18", "issue": "Music briefly overpowers narration."},
            {"time": "0:24", "issue": "Ends without a call-to-action."},
        ],
        "recommendations": [
            "Add a persistent brand element (handle or logo) in a corner.",
            "Close with a 2-3s call-to-action ('DM to book a viewing').",
            "Tighten the middle third to keep momentum.",
        ],
        # TODO: replace with real sampled keyframe image paths / bytes.
        "keyframes": [],  # e.g. list of file paths or PIL images
    }


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def render_radar(rubric_scores: dict):
    dims = list(rubric_scores.keys())
    vals = list(rubric_scores.values())
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=vals + [vals[0]],
        theta=dims + [dims[0]],
        fill="toself",
        name="Score",
    ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 10])),
        showlegend=False,
        margin=dict(l=40, r=40, t=20, b=20),
        height=380,
    )
    st.plotly_chart(fig, use_container_width=True)


def render_verdict(result: dict):
    col1, col2 = st.columns([1, 2])
    with col1:
        st.metric("Overall Score", f"{result['overall_score']} / 10")
    with col2:
        if result["passed"]:
            st.success(f"PASS — meets the quality bar ({PASS_THRESHOLD}/10)")
        else:
            st.error(f"NEEDS WORK — below the quality bar ({PASS_THRESHOLD}/10)")


def render_report(result: dict):
    """Renders the full structured report for one evaluated video."""
    render_verdict(result)
    st.divider()

    # Rubric breakdown -------------------------------------------------------
    st.subheader("Rubric Breakdown")
    left, right = st.columns([1, 1])
    with left:
        render_radar(result["rubric_scores"])
    with right:
        for dim, score in result["rubric_scores"].items():
            st.markdown(f"**{dim} — {score}/10**")
            st.caption(result["rubric_justifications"].get(dim, ""))
    st.divider()

    # CV metrics + Visual context ------------------------------------------
    st.subheader("Computer-Vision Metrics")
    cv = result["cv_metrics"]
    mcols = st.columns(3)
    for i, (k, v) in enumerate(cv.items()):
        mcols[i % 3].metric(k, v)
    st.divider()

    st.subheader("Visual Context")
    vc1, vc2 = st.columns([1, 1])
    with vc1:
        if result["video_url"]:
            try:
                st.video(result["video_url"])
            except Exception:
                st.info("Video preview unavailable for this URL.")
    with vc2:
        st.caption("Sampled keyframes (what the LLM actually looked at):")
        if result["keyframes"]:
            st.image(result["keyframes"], width=110)
        else:
            st.info("Keyframes appear here once frame sampling is wired in.")
    st.divider()

    # LLM judge panel -------------------------------------------------------
    st.subheader("LLM Judge — Summary")
    jc1, jc2 = st.columns(2)
    with jc1:
        st.markdown("**Strengths**")
        for s in result["strengths"]:
            st.markdown(f"- {s}")
    with jc2:
        st.markdown("**Weaknesses**")
        for w in result["weaknesses"]:
            st.markdown(f"- {w}")

    st.markdown("**Timestamped issues**")
    st.dataframe(
        pd.DataFrame(result["timestamped_issues"]),
        hide_index=True,
        use_container_width=True,
    )

    st.markdown("**Recommendations**")
    for r in result["recommendations"]:
        st.markdown(f"- {r}")

    st.divider()

    # Human override (worth-adding item) -----------------------------------
    with st.expander("Add a human rating (for human-vs-LLM agreement)"):
        st.slider("Your overall score", 0.0, 10.0, result["overall_score"], 0.5)
        st.text_area("Notes")
        st.button("Save human rating")  # TODO: persist to history store


# ---------------------------------------------------------------------------
# Video embedding helper
# ---------------------------------------------------------------------------
# Handles the three source types cleanly:
#   - local .mp4 file        -> st.video (most reliable; recommended for demos)
#   - YouTube link           -> st.video (works out of the box)
#   - TikTok / Instagram     -> official embed iframe via components.html
#
# Recommendation: download your hero examples as short .mp4 files into an
# examples/ folder and use kind="file". That makes the findings page fully
# self-contained — nothing breaks if a creator deletes a post, and it renders
# identically every time. Keep clips short/compressed (Community Cloud ~1 GB).

def render_example_video(source: str, kind: str = "file", caption: str = ""):
    """Embed one example video.

    source: file path, or URL.
    kind:   "file" | "youtube" | "tiktok" | "instagram"
    """
    if kind in ("file", "youtube"):
        # st.video handles both a local path and a YouTube URL.
        st.video(source)
    elif kind == "tiktok":
        # TikTok official embed. The post must be public.
        st.components.v1.html(
            f'<blockquote class="tiktok-embed" cite="{source}" '
            f'data-video-id=""><a href="{source}"></a></blockquote>'
            '<script async src="https://www.tiktok.com/embed.js"></script>',
            height=740,
        )
    elif kind == "instagram":
        # Instagram official embed. The post must be public.
        st.components.v1.html(
            f'<blockquote class="instagram-media" data-instgrm-permalink="{source}">'
            f'<a href="{source}"></a></blockquote>'
            '<script async src="https://www.instagram.com/embed.js"></script>',
            height=740,
        )
    else:
        st.warning(f"Unknown video kind: {kind}")
    if caption:
        st.caption(caption)


def example_showcase(insight: str, source: str, kind: str, takeaway: str):
    """A reusable 'insight -> clip -> takeaway' block for the findings page."""
    st.markdown(f"**{insight}**")
    render_example_video(source, kind)
    st.caption(f"Takeaway: {takeaway}")
    st.markdown("")  # small spacer


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

def findings_tab():
    """Full start-to-finish project summary with embedded example videos.

    This is a long-form report, not a condensed blurb. Fill each section with
    your real content; the example_showcase() calls are where clips go.
    Swap the placeholder sources for your real files/links and set `kind`.
    """
    st.header("Property Marketing Video Evaluation — Project Summary")
    st.caption("Full write-up: research → rubric → system → results → recommendations.")

    # --- 0. Overview -------------------------------------------------------
    st.subheader("Overview")
    st.markdown(
        "_One-paragraph summary of the whole project: the problem (judging video "
        "quality at scale beyond subjective opinion), what you built, and the "
        "headline outcome._"
    )
    # TODO: write the overview.

    st.divider()

    # --- 1. Background & motivation ---------------------------------------
    st.subheader("1. Background & Motivation")
    st.markdown(
        "_Why this matters: scaling video generation needs a reliable quality bar "
        "to compare outputs, catch regressions, and make faster product calls. "
        "Note the four templates: Short, Medium, Long, Guided Tour._"
    )
    # TODO: expand.

    st.divider()

    # --- 2. How the current system works ----------------------------------
    st.subheader("2. The Current Template Algorithm")
    st.markdown(
        "_Summarize how today's generation templates work and the reasoning "
        "behind them — the baseline you're evaluating against._"
    )
    # TODO: expand.

    st.divider()

    # --- 3. Competitive research ------------------------------------------
    st.subheader("3. Competitive Landscape")
    st.markdown(
        "_What leading AI real-estate video platforms do, and how they differ._"
    )
    st.dataframe(
        pd.DataFrame(
            {
                "Platform": ["Reel-E", "AutoReel", "BetterSpace", "AgentPulse"],
                "Approach": ["_…_", "_…_", "_…_", "_…_"],
                "Strength": ["_…_", "_…_", "_…_", "_…_"],
                "Gap": ["_…_", "_…_", "_…_", "_…_"],
            }
        ),
        hide_index=True,
        use_container_width=True,
    )
    # TODO: fill from your competitive analysis; add platforms you identified.

    st.divider()

    # --- 4. What works on social (with video examples) --------------------
    st.subheader("4. What Actually Works on Social")
    st.markdown(
        "_Patterns from high-performing property videos on TikTok, YouTube "
        "Shorts, and Instagram Reels. Examples below illustrate each pattern._"
    )

    # >>> VIDEO EXAMPLE SLOTS <<<
    # Replace the sources and `kind`. Recommended: download as .mp4 and use
    # kind="file" so the page is self-contained. YouTube links also just work.
    example_showcase(
        insight="Strong hook in the first 2 seconds",
        source="examples/hook_example.mp4",       # TODO: your file / link
        kind="file",                                # "file" | "youtube" | "tiktok" | "instagram"
        takeaway="Fast opening on the best room prevents the scroll-away.",
    )
    example_showcase(
        insight="Brisk pacing with short shots",
        source="https://www.youtube.com/watch?v=PLACEHOLDER",  # TODO
        kind="youtube",
        takeaway="~2-3s average shot length keeps energy up on vertical feeds.",
    )
    example_showcase(
        insight="Clear call-to-action at the end",
        source="examples/cta_example.mp4",         # TODO
        kind="file",
        takeaway="A closing CTA ('DM to book a viewing') drives the marketing goal.",
    )
    # TODO: add as many example_showcase(...) blocks as you need.

    st.divider()

    # --- 5. Synthesis: the patterns ---------------------------------------
    st.subheader("5. Synthesis — The Patterns That Matter")
    st.markdown(
        "_Pull the research together into clear patterns around pacing, "
        "storytelling, visual style, and marketing effectiveness. These patterns "
        "are what the rubric operationalizes._"
    )
    # TODO: expand.

    st.divider()

    # --- 6. The rubric -----------------------------------------------------
    st.subheader("6. The Evaluation Rubric")
    st.markdown(
        "Each research pattern maps to a scored dimension. This is the same rubric "
        "the LLM judge and the live tool use."
    )
    st.dataframe(
        pd.DataFrame(
            {
                "Dimension": RUBRIC_DIMENSIONS,
                "What it measures": ["_describe_" for _ in RUBRIC_DIMENSIONS],
                "Why it matters (from research)": ["_link to §4/§5_" for _ in RUBRIC_DIMENSIONS],
            }
        ),
        hide_index=True,
        use_container_width=True,
    )
    # TODO: fill in from your criterion-2 rubric.

    st.divider()

    # --- 7. How the system works ------------------------------------------
    st.subheader("7. How the Evaluation System Works")
    st.markdown(
        "_The methodology: CV metrics for the objective layer + LLM-as-a-Judge "
        "for the rubric layer, combined into one report. Note that the dashboard "
        "and batch script both call the same evaluate_video() function._"
    )
    # TODO: add a simple diagram or bullet flow: download → sample frames →
    # CV metrics + LLM judge → combined report.

    st.divider()

    # --- 8. Results / validation ------------------------------------------
    st.subheader("8. Results & Validation")
    st.markdown(
        "_How well the system works: human-vs-LLM agreement on the rubric, "
        "example evaluations, any regressions caught. Show a before/after or a "
        "sample scored video._"
    )
    # TODO: add results; you can embed a scored example clip here too:
    # render_example_video("examples/scored_example.mp4", kind="file",
    #                       caption="Example the system scored 8.1/10.")

    st.divider()

    # --- 9. Recommendations -----------------------------------------------
    st.subheader("9. Recommendations for Matterport")
    st.markdown(
        "_How the team should use this going forward: setting quality bars per "
        "template, catching regressions, comparing model outputs, and next steps "
        "to move from POC to production._"
    )
    # TODO: expand.


def live_tool_tab():
    st.header("Live Evaluation Tool")
    st.caption("Paste a video URL and get a structured quality report.")

    c1, c2 = st.columns([3, 1])
    with c1:
        video_url = st.text_input(
            "Video URL",
            placeholder="TikTok / YouTube Shorts / Instagram Reel link…",
        )
    with c2:
        template = st.selectbox("Template", TEMPLATES)

    run = st.button("Evaluate", type="primary")

    if run:
        if not video_url:
            st.warning("Paste a video URL first.")
            return
        with st.spinner("Evaluating…"):
            result = evaluate_video(video_url, template)  # <- the one plug-in point
        st.session_state["last_result"] = result

    if "last_result" in st.session_state:
        st.divider()
        render_report(st.session_state["last_result"])


# ---------------------------------------------------------------------------
# App entry
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(
        page_title="Video Quality Evaluation",
        layout="wide",
    )
    st.title("Property Marketing Video — Quality Evaluation")

    findings, tool = st.tabs(["Findings", "Live Tool"])
    with findings:
        findings_tab()
    with tool:
        live_tool_tab()


if __name__ == "__main__":
    main()
