# Property Video Evaluator

Streamlit dashboard for the property marketing video evaluation pipeline.
Paste a video URL or upload a file, pick a template, and get back a
structured quality report (Tier-1 technical CV gate; the Gemini rubric
judge is not wired in yet).

## Files

- `video-eval-tool.py` — Streamlit app (entry point)
- `evaluate.py` — pipeline: ingest → CV gate → (future) Gemini judge → score
- `config.py` — all tunable thresholds/settings
- `rubric.md` — evaluation rubric, loaded at runtime
- `requirements.txt` — Python dependencies

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run video-eval-tool.py --server.port=8501 --server.address=0.0.0.0
```

App will be at `http://<server-ip>:8501`.

## Notes for whoever hosts this

- No API keys or secrets are required yet — the Gemini judge stage isn't
  implemented, so the app only runs the CV gate today.
- App listens on port `8501` by default (Streamlit's default).
- No database or external service dependencies beyond pip packages.
