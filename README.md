# Transcribator

A small web tool that turns a YouTube link into a transcript plus a ready-made analysis prompt, so you can decide in 30 seconds whether a video is worth watching.

![Transcribator main page](docs/screenshot.png)

## What it does and why

Long videos and podcasts are expensive to watch and cheap to summarize badly. The workflow here:

1. Paste a YouTube link into the page.
2. The backend fetches the video title (YouTube oEmbed) and the transcript ([Supadata](https://supadata.ai) API).
3. The page shows the transcript and builds a single block of text: a system prompt followed by the transcript.
4. One click copies that block (or copies the raw transcript only), ready to paste into any LLM chat. The "Copy & Open Claude" button copies and opens Claude.

The bundled prompt (`system_prompt_transcribator.txt`, written in Russian) makes the model return a quick verdict first (genre, filler percentage, what is new, watch/skip) and then only the substantive points, with timestamps when available. Swap the file to change the behavior; no code changes needed.

Nothing is stored on the server: the transcript goes straight back to the page.

## Stack

- Python 3.12, FastAPI, Uvicorn
- Supadata API for transcripts, YouTube oEmbed for titles
- Single-file vanilla HTML/CSS/JS frontend (`index.html`, no build step)
- Docker

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export SUPADATA_API_KEY=your_key_here
python server.py              # http://localhost:8000
```

| Variable | Default | Meaning |
|---|---|---|
| `SUPADATA_API_KEY` | — | Supadata key. Without it the page loads, but transcript requests fail with a 502 |
| `PORT` | `8000` | Port |
| `RATE_LIMIT_SECONDS` | `15` | One transcript request per client IP per this many seconds |
| `DAILY_LIMIT` | `200` | Transcript requests per day for the whole service |
| `ALLOWED_ORIGINS` | empty | Comma-separated origins allowed to call the API cross-origin. The bundled page is same-origin and needs none |

- Health check: `GET /health`.

API: `POST /api/transcript` with JSON `{"url": "https://www.youtube.com/watch?v=..."}` returns `{"title", "transcript", "clipboard_text"}`.

## Docker

```bash
docker build -t transcribator .
docker run --rm -p 8000:8000 -e SUPADATA_API_KEY=your_key_here transcribator
```

The container runs as a non-root user and writes nothing to disk.

## Project layout

```
server.py                        FastAPI app (entry point): page, /health, POST /api/transcript
yt_transcript.py                 URL parsing, filename helpers, and a standalone CLI using youtube-transcript-api
api.py                           Older alternative API (GET /api/transcript via youtube-transcript-api); not used by Docker
index.html                       Frontend
system_prompt_transcribator.txt  Prompt prepended to every transcript
tests/test_server.py             API tests (validation, rate limit, CORS, errors)
Dockerfile, requirements.txt, requirements-dev.txt
docs/screenshot.png
```

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

Upstream calls are stubbed, so tests need no network or API key.

## Abuse protection

Every transcript is a paid Supadata call, and there is no login, so the API protects the key itself:

- only YouTube links are accepted (host whitelist), and the URL sent upstream is rebuilt from the video ID;
- one request per client IP every 15 seconds (`Retry-After` on 429), plus a daily cap for the whole service;
- the client IP comes from `CF-Connecting-IP` (Cloudflare) or the last `X-Forwarded-For` hop, so a spoofed header does not reset the limit;
- cross-origin calls are blocked unless the origin is listed in `ALLOWED_ORIGINS`;
- upstream errors return a generic message, details go only to the server log;
- the interactive API docs are disabled.

Limits are kept in memory: they reset on restart and assume a single instance. Also set a usage cap in the Supadata dashboard.
