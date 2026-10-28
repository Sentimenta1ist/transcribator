import asyncio
import json
import logging
import os
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

import requests as _requests
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from yt_transcript import extract_video_id

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Transcribator", docs_url=None, redoc_url=None, openapi_url=None)

# Each transcript is a paid Supadata call: one request per client IP per interval, plus a daily cap.
_MIN_INTERVAL = float(os.environ.get("RATE_LIMIT_SECONDS", "15"))
_PER_DAY = int(os.environ.get("DAILY_LIMIT", "200"))
# The page calls the API from the same origin, so cross-origin calls are allowed only for listed origins.
_ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}

_last_call: dict[str, float] = {}
_day = {"date": "", "count": 0}

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def client_ip(request: Request) -> str:
    # Cloudflare sets the real client IP; otherwise trust the hop added by our own proxy (last entry).
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[-1]
    return forwarded.strip() or (request.client.host if request.client else "unknown")


def check_rate_limit(ip: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    if _day["date"] != today:
        _day.update(date=today, count=0)
        _last_call.clear()
    if _day["count"] >= _PER_DAY:
        raise HTTPException(status_code=429, detail="Daily limit reached. Try again tomorrow.")
    wait = _MIN_INTERVAL - (now - _last_call.get(ip, 0.0))
    if wait > 0:
        raise HTTPException(status_code=429, detail=f"Too many requests. Try again in {int(wait) + 1} s.",
                            headers={"Retry-After": str(int(wait) + 1)})
    _last_call[ip] = now
    _day["count"] += 1


def canonical_video_url(url: str) -> str:
    if len(url) > 300:
        raise ValueError("URL is too long.")
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or parsed.hostname not in _YOUTUBE_HOSTS:
        raise ValueError("Only YouTube links are supported.")
    return f"https://www.youtube.com/watch?v={extract_video_id(url)}"


_BASE = Path(__file__).parent
_HTML = (_BASE / "index.html").read_text(encoding="utf-8")

try:
    _SYSTEM_PROMPT = (_BASE / "system_prompt_transcribator.txt").read_text(encoding="utf-8")
except FileNotFoundError:
    _SYSTEM_PROMPT = ""


def get_video_title(url: str) -> str:
    oembed = "https://www.youtube.com/oembed?" + urllib.parse.urlencode({"url": url, "format": "json"})
    with urllib.request.urlopen(oembed, timeout=10) as resp:
        return json.loads(resp.read()).get("title", "transcript")


def get_transcript(video_id: str) -> str:
    api_key = os.environ.get("SUPADATA_API_KEY", "")
    if not api_key:
        raise RuntimeError("SUPADATA_API_KEY is not set")
    resp = _requests.get(
        f"https://api.supadata.ai/v1/youtube/transcript?videoId={video_id}",
        headers={"x-api-key": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    segments = resp.json().get("content", [])
    if not segments:
        raise ValueError("No transcript available for this video.")
    return "\n".join(s["text"].strip() for s in segments if s.get("text", "").strip())


class TranscriptRequest(BaseModel):
    url: str


@app.get("/", response_class=HTMLResponse)
async def root():
    return _HTML


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/transcript")
async def transcript_endpoint(req: TranscriptRequest, request: Request):
    try:
        url = canonical_video_url(req.url)
    except ValueError:
        raise HTTPException(status_code=400, detail="Only YouTube video links are supported.")
    video_id = extract_video_id(url)
    check_rate_limit(client_ip(request))

    try:
        title, transcript = await asyncio.gather(
            asyncio.to_thread(get_video_title, url),
            asyncio.to_thread(get_transcript, video_id),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logging.error("transcript fetch failed: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=502, detail="Could not fetch the transcript. Try again later.")

    clipboard_text = (_SYSTEM_PROMPT + "\n\n" + transcript) if _SYSTEM_PROMPT else transcript

    return {"title": title, "transcript": transcript, "clipboard_text": clipboard_text}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
