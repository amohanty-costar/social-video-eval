"""
video-eval-tool.py
==================
Self-serve dashboard for the property video evaluation pipeline. Paste a
video URL, or upload a file, pick a template, and get a structured quality
report back -- powered directly by evaluate.py's evaluate_video(), so
behavior always matches the CLI exactly.

Run with: streamlit run video-eval-tool.py
"""

import json
import tempfile
from pathlib import Path

import streamlit as st

import evaluate

st.set_page_config(page_title="Property Video Evaluator", layout="wide")
st.title("Property Marketing Video Evaluator")
st.caption(
    "Runs the Tier-1 technical CV gate (and, once enabled, the Gemini rubric "
    "judge) against a property tour video and returns a structured quality report."
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
    submitted = st.form_submit_button("Evaluate", use_container_width=True)


def _resolve_input(source_url, uploaded_file):
    """Return (source, cleanup_path). cleanup_path is a temp file to delete after evaluation, if any."""
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
        result, error = None, None
        try:
            with st.spinner("Analyzing video — the CV gate decodes every frame, this can take a minute or two..."):
                result = evaluate.evaluate_video(source, template)
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
                st.info(result["reason"])
                st.metric("Overall score", result["overall_score"] if result["overall_score"] is not None else "Pending judge")
                if result["dimensions"]:
                    st.subheader("Dimension scores")
                    st.json(result["dimensions"])
                if result["rules"]:
                    st.subheader("Compliance rules")
                    st.dataframe(result["rules"], use_container_width=True)
                if result["summary"]:
                    st.subheader("Summary")
                    st.write(result["summary"])

            with st.expander("Raw JSON"):
                st.json(result)

            st.download_button(
                "Download report as JSON",
                data=json.dumps(result, indent=2),
                file_name="video_eval_report.json",
                mime="application/json",
            )
