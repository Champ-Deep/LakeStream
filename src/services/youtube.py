"""YouTube transcript and metadata extraction service."""

import re
from typing import Any

import httpx
import structlog
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

log = structlog.get_logger()

# Regex for YouTube video ID extraction (11 alphanumeric + hyphen/underscore chars)
_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?.*v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})"
)

# Re-export exceptions so callers don't need to import from youtube_transcript_api internals
__all__ = [
    "extract_video_id",
    "fetch_video_metadata",
    "fetch_transcript",
    "NoTranscriptFound",
    "TranscriptsDisabled",
    "VideoUnavailable",
]


def extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from various URL formats.

    Supports:
        - https://www.youtube.com/watch?v=VIDEO_ID
        - https://youtu.be/VIDEO_ID
        - https://www.youtube.com/embed/VIDEO_ID
        - URLs with extra query params (&t=, &list=, etc.)
    """
    match = _VIDEO_ID_RE.search(url)
    return match.group(1) if match else None


async def fetch_video_metadata(video_id: str) -> dict[str, Any]:
    """Fetch video metadata via YouTube oEmbed (no API key required).

    Returns title, channel name, channel URL, and thumbnail URL.
    """
    oembed_url = (
        f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(oembed_url)
        response.raise_for_status()
        data = response.json()

    return {
        "title": data.get("title", ""),
        "channel": data.get("author_name", ""),
        "channel_url": data.get("author_url", ""),
        "thumbnail_url": data.get("thumbnail_url", ""),
    }


def fetch_transcript(
    video_id: str,
    languages: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch transcript for a YouTube video.

    Returns full plain text, timestamped segments, language info,
    and estimated duration.

    Raises:
        TranscriptsDisabled: Video has captions disabled.
        NoTranscriptFound: No transcript in requested languages.
        VideoUnavailable: Video does not exist or is private.
    """
    if languages is None:
        languages = ["en", "en-US", "en-GB"]

    ytt_api = YouTubeTranscriptApi()
    transcript = ytt_api.fetch(video_id, languages=languages)

    # Collect snippets in one pass (transcript is an iterator)
    snippets = list(transcript)

    segments = [
        {
            "text": s.text,
            "start": round(s.start, 2),
            "duration": round(s.duration, 2),
        }
        for s in snippets
    ]

    full_text = " ".join(s.text for s in snippets)

    # Estimate duration from last segment
    duration_seconds = 0.0
    if segments:
        last = segments[-1]
        duration_seconds = last["start"] + last["duration"]

    return {
        "transcript_text": full_text,
        "segments": segments,
        "segment_count": len(segments),
        "language": transcript.language,
        "language_code": transcript.language_code,
        "is_generated": transcript.is_generated,
        "duration_seconds": round(duration_seconds, 2),
    }
