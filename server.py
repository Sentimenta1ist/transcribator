import asyncio
import json
import os
import urllib.request
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled

from yt_transcript import (
    extract_video_id,
    fetch_transcript,
    format_transcript,
    sanitize_filename,
    unique_path,
)


def get_video_title(url: str) -> str:
    oembed = f"https://www.youtube.com/oembed?url={url}&format=json"
    with urllib.request.urlopen(oembed, timeout=10) as resp:
        return json.loads(resp.read()).get("title", "transcript")

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
        title, snippets = await asyncio.gather(
            asyncio.to_thread(get_video_title, req.url),
            asyncio.to_thread(fetch_transcript, video_id),
        )
    except TranscriptsDisabled:
        raise HTTPException(status_code=422, detail="Transcripts are disabled for this video.")
    except NoTranscriptFound:
        raise HTTPException(status_code=422, detail="No transcript found for this video.")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    transcript = format_transcript(snippets)
    clipboard_text = (_SYSTEM_PROMPT + "\n\n" + transcript) if _SYSTEM_PROMPT else transcript

    output_dir = _BASE / "transcripts"
    output_dir.mkdir(exist_ok=True)
    output_path = unique_path(output_dir, sanitize_filename(title))
    output_path.write_text(clipboard_text, encoding="utf-8")

    return {"title": title, "transcript": transcript, "clipboard_text": clipboard_text}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
