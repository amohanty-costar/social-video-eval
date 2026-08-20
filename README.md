# Auto Video Evaluator

Streamlit dashboard for the property marketing video evaluation pipeline.
Paste a video URL or upload a file, pick a template, and get back a
structured quality report: Tier-1 technical CV gate, then the Gemini rubric
judge, then a composite score out of 100.

## Files

Every file states its purpose at the top. In short:

| File | Purpose |
|---|---|
| `video-eval-tool.py` | The web app. What users open. Layout only, no logic. |
| `evaluate.py` | The engine. All evaluation logic: ingest → CV gate → judge → score. |
| `config.py` | Every setting you might change, in one place. No logic. |
| `prompt.md` | The instruction sent to Gemini. Edit to change how it judges. |
| `rubric.md` | The evaluation standard. Read at runtime and injected into the prompt. |
| `requirements.txt` | Python packages needed. |
| `.gitignore` | Files git must never track — above all the API key. |

Edit `config.py`, `prompt.md` or `rubric.md` to retune the evaluation —
`evaluate.py` shouldn't need touching.

## Editing the prompt or the rubric

Both are plain files read at runtime, so a reword takes effect on the next
evaluation — no code change, no restart beyond Streamlit's own reload.

`prompt.md` holds two placeholders that are filled in before the prompt is sent:

- `{{TEMPLATE_TYPE}}` — the value from the app's Template dropdown
- `<rubric></rubric>` — where `rubric.md` gets injected

**What the judge is not told**, all handled in `evaluate.py::_rubric_text()`:

- **Tier 1.** The judge only ever runs on a video that already passed the gate,
  so the gate section is stripped out.
- **The Tier-3 deduction points**, and the Final score formula. A model that
  knows a critical rule costs 10 points goes soft on critical rules. It rules on
  what it sees; `score()` applies the cost.

**Behavioural guardrails live in the response schema**, not the prompt — see the
`GUARDRAILS` comment block above `_response_schema()`. Field *order* in that
schema is load-bearing: `evidence` and `rationale` come before `score` so the
reasoning drives the verdict. Reordering those keys changes model behaviour
silently.

**The LLM scores; the code does arithmetic.** Gemini returns the four dimension
scores and every rule verdict. `score()` computes the composite from them and
never overrides a judgment.

## The score spectrum

The composite is shown on a red/amber/green track with a needle at the score, a
thinner marker at the base, and a hatched region between them for what
compliance rules took away.

Two things worth knowing:

- **The bands are not part of `rubric.md`.** The rubric defines how to reach a
  score out of 100 and stops there — it never says 70 is "Strong". The
  cut-points live in `config.SCORE_BANDS` so the choice is explicit and
  calibratable. Defaults map to dimension averages: 67.5 is an average of 3.5,
  47.5 is an average of 2.5.
- **Only 21 scores are reachable.** Four integer 1–5 scores, averaged and ×20,
  can only land on a multiple of 5, and deductions are 5s and 10s. So the bar
  has faint ticks at each multiple of 5 rather than implying a precision the
  score doesn't have, and the band cut-points are offset by 2.5 so no real score
  ever straddles a colour boundary.

A gate-failed video gets no spectrum — it scored 0 without being judged against
the rubric, and a needle pinned in the red zone would imply otherwise.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run video-eval-tool.py --server.port=8501 --server.address=0.0.0.0
```

App will be at `http://<server-ip>:8501`.

## API key

The Gemini judge needs an API key. File a ticket for an API key, you will have to provide IP restrictions since it is a back-end key. 
Refer to previous keys. 
It is resolved in this order:

1. `GEMINI_API_KEY` environment variable
2. `GOOGLE_API_KEY` environment variable
3. `.streamlit/secrets.toml`

For the Streamlit app, create `.streamlit/secrets.toml` (already gitignored):

```toml
GEMINI_API_KEY = "your-key-here"
```

For the CLI, `export GEMINI_API_KEY=...` works too. If no key is configured the
app still runs the CV gate and shows a warning; the judge is disabled.

## CLI

```bash
# full pipeline
python evaluate.py --source /path/to/video.mp4 --template short

# CV gate only, no API key needed — for threshold calibration
python evaluate.py --source /path/to/video.mp4 --template short --no-judge
python evaluate.py --source /path/to/video.mp4 --metrics-only
```

## Notes for whoever hosts this

- Requires a Gemini API key for the judge stage (see above). The CV gate runs
  without one.
- Pinned to `google-genai==1.47.0`, the last release supporting Python 3.9,
  which is what `.venv` is built on. It uses the `generateContent` API — still
  fully supported, though Google now labels it legacy in favour of the
  Interactions API. Moving to Interactions means upgrading to Python 3.10+ and
  a 2.x SDK.
- Judge model is pinned in `config.py` (`GEMINI_MODEL`) to an explicit version
  rather than a `-latest` alias, so scores don't shift under a calibrated rubric
  when Google ships a new model.
- Videos are uploaded to the Gemini Files API and deleted immediately after the
  judge call (`GEMINI_DELETE_UPLOAD_AFTER_USE` in `config.py`).
- A video that fails the Tier-1 gate scores 0 and never reaches the judge, so
  disqualified footage costs no API spend.
- App listens on port `8501` by default (Streamlit's default).
- No database or external service dependencies beyond pip packages.

