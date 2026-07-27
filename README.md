# video-quality-eval

Automated quality evaluation for property-marketing videos. Combines
traditional computer-vision metrics with an LLM-as-a-Judge scored against a
research-backed rubric, wrapped in a self-serve Streamlit app.

The app has two tabs:
- **Findings** — full project write-up (research → rubric → system → results → recommendations), with embedded example videos.
- **Live Tool** — paste a video URL, get a structured quality report.

---

## Project structure

```
video-quality-eval/
├── app.py              # the Streamlit app (findings + live tool)
├── requirements.txt    # dependencies
├── README.md           # this file
└── examples/           # local .mp4 example clips for the findings page
```

Create the `examples/` folder and drop your hero example clips in it. Embedding
local `.mp4` files keeps the findings page self-contained and reliable (see
"Adding example videos" below).

---

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app opens in your browser. It runs on dummy data until you wire in the real
evaluation logic, so you can click around and see the layout immediately.

---

## Deploy to a shareable link (Streamlit Community Cloud — free)

1. Push this repo to GitHub.
2. Go to https://share.streamlit.io and sign in with GitHub.
3. Click **Create app**, pick this repo, branch, and `app.py`.
4. (Optional) Choose a subdomain — your app lands at
   `https://<your-name>.streamlit.app`.
5. Deploy. Share that URL — no one needs to run a script.

**Free-tier notes:** ~1 GB memory, apps sleep after ~12 hours idle (first
visitor wakes it in ~30s), one private app allowed, no custom domain. Keep
example clips short and compressed so the repo stays light.

**Demo-day tip:** open the link and run one evaluation a few minutes before you
present, so the instance is awake and warm.

---

## Secrets (LLM API key)

Once `evaluate_video()` calls an LLM, the API key must **not** live in the code —
especially if the repo is public. Use Streamlit secrets:

- **Locally:** create `.streamlit/secrets.toml`
  ```toml
  ANTHROPIC_API_KEY = "sk-..."
  ```
- **On Community Cloud:** app → Settings → Secrets → paste the same content.

Read it in code with `st.secrets["ANTHROPIC_API_KEY"]`. Add
`.streamlit/secrets.toml` to `.gitignore` so it never gets committed.

---

## Adding example videos (findings page)

In `app.py`, section 4 of the findings tab has `example_showcase(...)` blocks.
Each takes a `source` and a `kind`:

- `kind="file"` — a local path like `examples/hook_example.mp4` (recommended;
  most reliable, page stays self-contained).
- `kind="youtube"` — a YouTube URL (works out of the box; keeps the repo light).
- `kind="tiktok"` / `kind="instagram"` — official embed; the post must be public
  and can break if the creator deletes it. Use only for non-critical examples.

---

## Wiring in the real evaluation

All evaluation logic lives behind one function: `evaluate_video(url, template)`
in `app.py`. Replace its dummy return with the real pipeline:

1. Download the video (`yt-dlp`).
2. Sample frames + compute CV metrics.
3. Send sampled frames + metadata to the LLM with the rubric prompt.
4. Assemble the result dict (keep the same shape as the dummy version).

The dashboard **and** the batch script call this same function, so they stay
consistent by construction. Keep the return-dict shape stable and the UI needs
no changes.
