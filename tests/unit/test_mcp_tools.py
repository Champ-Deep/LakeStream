"""Tests for MCP tool functions (extract_youtube_transcript, extract_blog_content)."""

import json
from unittest.mock import AsyncMock, patch

import pytest


class TestExtractYoutubeTranscript:
    @pytest.mark.asyncio
    async def test_invalid_url_returns_error(self):
        from src.mcp_server import extract_youtube_transcript

        result = json.loads(await extract_youtube_transcript(url="https://example.com/not-youtube"))
        assert "error" in result
        assert "Invalid YouTube URL" in result["error"]

    @pytest.mark.asyncio
    async def test_valid_url_returns_transcript(self):
        from src.mcp_server import extract_youtube_transcript

        mock_metadata = {
            "title": "Test Video",
            "channel": "Test Channel",
            "channel_url": "",
            "thumbnail_url": "",
        }
        mock_transcript = {
            "transcript_text": "Hello world",
            "segments": [{"text": "Hello world", "start": 0.0, "duration": 2.0}],
            "segment_count": 1,
            "language": "English",
            "language_code": "en",
            "is_generated": False,
            "duration_seconds": 2.0,
        }

        with (
            patch(
                "src.services.youtube.fetch_video_metadata",
                new_callable=AsyncMock,
                return_value=mock_metadata,
            ),
            patch("src.services.youtube.fetch_transcript", return_value=mock_transcript),
        ):
            result = json.loads(
                await extract_youtube_transcript(url="https://youtube.com/watch?v=abc12345678")
            )
            assert result["transcript_text"] == "Hello world"
            assert result["metadata"]["title"] == "Test Video"
            assert result["video_id"] == "abc12345678"
            assert result["segment_count"] == 1
            assert "segments" in result  # timestamps included by default

    @pytest.mark.asyncio
    async def test_without_timestamps(self):
        from src.mcp_server import extract_youtube_transcript

        mock_metadata = {"title": "", "channel": "", "channel_url": "", "thumbnail_url": ""}
        mock_transcript = {
            "transcript_text": "Hello",
            "segments": [{"text": "Hello", "start": 0.0, "duration": 1.0}],
            "segment_count": 1,
            "language": "English",
            "language_code": "en",
            "is_generated": True,
            "duration_seconds": 1.0,
        }

        with (
            patch(
                "src.services.youtube.fetch_video_metadata",
                new_callable=AsyncMock,
                return_value=mock_metadata,
            ),
            patch("src.services.youtube.fetch_transcript", return_value=mock_transcript),
        ):
            result = json.loads(
                await extract_youtube_transcript(
                    url="https://youtu.be/abc12345678",
                    include_timestamps=False,
                )
            )
            assert "segments" not in result
            assert result["transcript_text"] == "Hello"

    @pytest.mark.asyncio
    async def test_transcript_disabled_returns_error(self):
        from src.mcp_server import extract_youtube_transcript
        from src.services.youtube import TranscriptsDisabled

        mock_metadata = {"title": "No Caps", "channel": "", "channel_url": "", "thumbnail_url": ""}

        with (
            patch(
                "src.services.youtube.fetch_video_metadata",
                new_callable=AsyncMock,
                return_value=mock_metadata,
            ),
            patch(
                "src.services.youtube.fetch_transcript",
                side_effect=TranscriptsDisabled("abc12345678"),
            ),
        ):
            result = json.loads(
                await extract_youtube_transcript(url="https://youtube.com/watch?v=abc12345678")
            )
            assert "error" in result
            assert "No transcript available" in result["error"]
            assert result["metadata"]["title"] == "No Caps"

    @pytest.mark.asyncio
    async def test_metadata_failure_still_returns_transcript(self):
        from src.mcp_server import extract_youtube_transcript

        mock_transcript = {
            "transcript_text": "Still works",
            "segments": [],
            "segment_count": 0,
            "language": "English",
            "language_code": "en",
            "is_generated": True,
            "duration_seconds": 0.0,
        }

        with (
            patch(
                "src.services.youtube.fetch_video_metadata",
                new_callable=AsyncMock,
                side_effect=Exception("oEmbed down"),
            ),
            patch("src.services.youtube.fetch_transcript", return_value=mock_transcript),
        ):
            result = json.loads(
                await extract_youtube_transcript(url="https://youtube.com/watch?v=abc12345678")
            )
            assert result["transcript_text"] == "Still works"
            assert result["metadata"]["title"] == ""  # fallback empty


class TestExtractBlogContent:
    @pytest.mark.asyncio
    async def test_successful_extraction(self):
        from src.mcp_server import extract_blog_content

        mock_scrape_result = {
            "markdown": "# Hello World\n\nThis is a test blog post with some content here.",
            "metadata": {
                "url": "https://example.com/blog/post",
                "title": "Hello World",
                "description": "A test post",
                "og_title": "",
                "og_description": "OG description here",
                "og_image": "https://example.com/img.png",
                "canonical": "https://example.com/blog/post",
                "author": "Test Author",
            },
            "success": True,
            "tier_used": "playwright",
            "status_code": 200,
        }

        with patch(
            "src.services.scraper.ScraperService.scrape",
            new_callable=AsyncMock,
            return_value=mock_scrape_result,
        ):
            result = json.loads(await extract_blog_content(url="https://example.com/blog/post"))
            assert result["success"] is True
            assert result["word_count"] > 0
            assert result["reading_time_minutes"] > 0
            assert result["title"] == "Hello World"  # falls back to title since og_title empty
            assert result["description"] == "OG description here"  # prefers og_description
            assert result["author"] == "Test Author"
            assert "markdown" in result

    @pytest.mark.asyncio
    async def test_failed_extraction_returns_error(self):
        from src.mcp_server import extract_blog_content

        with patch(
            "src.services.scraper.ScraperService.scrape",
            new_callable=AsyncMock,
            side_effect=Exception("Connection failed"),
        ):
            result = json.loads(await extract_blog_content(url="https://example.com/blocked"))
            assert result["success"] is False
            assert "error" in result
            assert "Connection failed" in result["error"]

    @pytest.mark.asyncio
    async def test_scrape_returns_not_success(self):
        from src.mcp_server import extract_blog_content

        mock_scrape_result = {
            "markdown": "",
            "metadata": {},
            "success": False,
            "error": "No content found",
        }

        with patch(
            "src.services.scraper.ScraperService.scrape",
            new_callable=AsyncMock,
            return_value=mock_scrape_result,
        ):
            result = json.loads(await extract_blog_content(url="https://example.com/empty"))
            assert result["success"] is False
            assert result["error"] == "No content found"
