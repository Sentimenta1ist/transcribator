import asyncio
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled

from yt_transcript import extract_video_id, fetch_transcript, format_transcript, get_video_title

app = FastAPI(title="Transcribator")

_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def root():
    return _HTML


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/transcript")
async def transcript_endpoint(
    url: str = Query(..., description="YouTube video URL"),
    lang: str = Query("en", description="Preferred transcript language"),
    timestamps: bool = Query(True, description="Include timestamps"),
):
    try:
        video_id = extract_video_id(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        title, snippets = await asyncio.gather(
            asyncio.to_thread(get_video_title, url),
            asyncio.to_thread(fetch_transcript, video_id, lang),
        )
    except TranscriptsDisabled:
        raise HTTPException(status_code=422, detail="Transcripts are disabled for this video.")
    except NoTranscriptFound:
        raise HTTPException(status_code=422, detail="No transcript found for this video.")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "title": title,
        "transcript": format_transcript(snippets, timestamps=timestamps),
    }
