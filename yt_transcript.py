#!/usr/bin/env python3
import sys
import re
import argparse
import threading
import itertools
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pyperclip
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled


class Spinner:
    _frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str):
        self._message = message
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        for frame in itertools.cycle(self._frames):
            if self._stop.is_set():
                break
            print(f"\r{frame}  {self._message}", end="", flush=True)
            time.sleep(0.08)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()
        print("\r\033[K", end="", flush=True)


def extract_video_id(url: str) -> str:
    patterns = [
        r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from URL: {url}")


def get_video_title(url: str) -> str:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info.get("title", "transcript")


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = name.strip(". ")
    return name or "transcript"


def unique_path(directory: Path, stem: str, suffix: str = ".txt") -> Path:
    candidate = directory / (stem + suffix)
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def fetch_transcript(video_id: str, lang: str = "en", proxy_config=None, http_client=None) -> list[dict]:
    api = YouTubeTranscriptApi(proxy_config, http_client=http_client)
    transcript_list = api.list(video_id)

    def preferred_order(langs: list[str]) -> list[str]:
        return [lang] + [l for l in langs if l != lang]

    manual_langs = list(transcript_list._manually_created_transcripts.keys())
    generated_langs = list(transcript_list._generated_transcripts.keys())

    try:
        transcript = transcript_list.find_manually_created_transcript(
            preferred_order(manual_langs) or [lang]
        )
    except NoTranscriptFound:
        transcript = transcript_list.find_generated_transcript(
            preferred_order(generated_langs) or [lang]
        )

    return transcript.fetch()


def format_transcript(snippets, timestamps: bool = False) -> str:
    lines = []
    for snippet in snippets:
        text = snippet.text.strip()
        if not text:
            continue
        if timestamps:
            total = int(snippet.start)
            h, rem = divmod(total, 3600)
            m, s = divmod(rem, 60)
            lines.append(f"[{h}:{m:02d}:{s:02d}] {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Download YouTube transcript to a text file.")
    parser.add_argument("url", nargs="+", help="YouTube video URL")
    parser.add_argument("-t", "--timestamps", action="store_true", help="Prefix each line with [H:MM:SS]")
    parser.add_argument("-l", "--lang", default="en", metavar="LANG", help="Preferred transcript language (default: en)")
    args = parser.parse_args()

    url = " ".join(args.url)

    try:
        video_id = extract_video_id(url)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    with Spinner("Fetching title & transcript…"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_title = pool.submit(get_video_title, url)
            fut_transcript = pool.submit(fetch_transcript, video_id, args.lang)
            for fut in as_completed([fut_title, fut_transcript]):
                exc = fut.exception()
                if exc is not None:
                    pool.shutdown(wait=False, cancel_futures=True)
                    if isinstance(exc, TranscriptsDisabled):
                        print("Error: Transcripts are disabled for this video.", file=sys.stderr)
                    elif isinstance(exc, NoTranscriptFound):
                        print("Error: No transcript found for this video.", file=sys.stderr)
                    else:
                        print(f"Error: {exc}", file=sys.stderr)
                    sys.exit(1)
    title = fut_title.result()
    snippets = fut_transcript.result()

    text = format_transcript(snippets, timestamps=args.timestamps)
    output_dir = Path(__file__).parent / "transcripts"
    output_dir.mkdir(exist_ok=True)
    output_path = unique_path(output_dir, sanitize_filename(title))

    system_prompt_path = Path(__file__).parent / "system_prompt_transcribator.txt"
    try:
        system_prompt = system_prompt_path.read_text(encoding="utf-8")
        file_text = system_prompt + "\n\n" + text
    except FileNotFoundError:
        file_text = text

    with Spinner("Saving transcript…"):
        output_path.write_text(file_text, encoding="utf-8")

    pyperclip.copy(output_path.read_text(encoding="utf-8"))
    print(f"Saved: {output_path}")
    webbrowser.open("https://claude.ai/new")
    print("Transcript copied to clipboard — paste into Claude")


if __name__ == "__main__":
    main()
