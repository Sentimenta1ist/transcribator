import asyncio
import json
import logging
import os
import traceback
import urllib.request
from pathlib import Path

import requests as _requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from yt_transcript import extract_video_id, sanitize_filename, unique_path

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Transcribator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_BASE = Path(__file__).parent
_HTML = (_BASE / "index.html").read_text(encoding="utf-8")

try:
    _SYSTEM_PROMPT = (_BASE / "system_prompt_transcribator.txt").read_text(encoding="utf-8")
except FileNotFoundError:
    _SYSTEM_PROMPT = ""


def get_video_title(url: str) -> str:
    oembed = f"https://www.youtube.com/oembed?url={url}&format=json"
    with urllib.request.urlopen(oembed, timeout=10) as resp:
        return json.loads(resp.read()).get("title", "transcript")


def get_transcript(video_id: str) -> str:
    api_key = os.environ.get("SUPADATA_API_KEY", "")
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
async def transcript_endpoint(req: TranscriptRequest):
    try:
        video_id = extract_video_id(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        title, transcript = await asyncio.gather(
            asyncio.to_thread(get_video_title, req.url),
            asyncio.to_thread(get_transcript, video_id),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logging.error("transcript fetch failed: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=502, detail=str(e))

    clipboard_text = (_SYSTEM_PROMPT + "\n\n" + transcript) if _SYSTEM_PROMPT else transcript

    output_dir = _BASE / "transcripts"
    output_dir.mkdir(exist_ok=True)
    output_path = unique_path(output_dir, sanitize_filename(title))
    output_path.write_text(clipboard_text, encoding="utf-8")

    return {"title": title, "transcript": transcript, "clipboard_text": clipboard_text}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
