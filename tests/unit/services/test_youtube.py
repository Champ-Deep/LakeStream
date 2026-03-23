"""Tests for YouTube transcript and metadata extraction."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.youtube import extract_video_id, fetch_transcript, fetch_video_metadata


class TestExtractVideoId:
    def test_standard_watch_url(self):
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_short_url(self):
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_embed_url(self):
        assert extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_url_with_extra_params(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=120&list=PLxyz"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_url_without_www(self):
        assert extract_video_id("https://youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_http_url(self):
        assert extract_video_id("http://youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_invalid_url_returns_none(self):
        assert extract_video_id("https://example.com/page") is None

    def test_empty_string_returns_none(self):
        assert extract_video_id("") is None

    def test_non_youtube_domain_returns_none(self):
        assert extract_video_id("https://vimeo.com/12345") is None

    def test_id_with_hyphens_and_underscores(self):
        assert extract_video_id("https://youtu.be/a-B_c1D2e3f") == "a-B_c1D2e3f"


class TestFetchTranscript:
    @patch("src.services.youtube.YouTubeTranscriptApi")
    def test_returns_text_and_segments(self, mock_api_class):
        snippet1 = MagicMock(text="Hello world", start=0.0, duration=2.5)
        snippet2 = MagicMock(text="How are you", start=2.5, duration=3.0)

        mock_transcript = MagicMock()
        mock_transcript.__iter__ = MagicMock(return_value=iter([snippet1, snippet2]))
        mock_transcript.language = "English"
        mock_transcript.language_code = "en"
        mock_transcript.is_generated = True

        mock_api = MagicMock()
        mock_api.fetch.return_value = mock_transcript
        mock_api_class.return_value = mock_api

        result = fetch_transcript("dQw4w9WgXcQ")

        assert result["transcript_text"] == "Hello world How are you"
        assert len(result["segments"]) == 2
        assert result["segments"][0]["text"] == "Hello world"
        assert result["segments"][0]["start"] == 0.0
        assert result["segments"][1]["start"] == 2.5
        assert result["segment_count"] == 2
        assert result["is_generated"] is True
        assert result["language_code"] == "en"

    @patch("src.services.youtube.YouTubeTranscriptApi")
    def test_duration_from_last_segment(self, mock_api_class):
        snippet = MagicMock(text="Only segment", start=120.0, duration=5.0)

        mock_transcript = MagicMock()
        mock_transcript.__iter__ = MagicMock(return_value=iter([snippet]))
        mock_transcript.language = "English"
        mock_transcript.language_code = "en"
        mock_transcript.is_generated = False

        mock_api = MagicMock()
        mock_api.fetch.return_value = mock_transcript
        mock_api_class.return_value = mock_api

        result = fetch_transcript("test123")

        assert result["duration_seconds"] == 125.0

    @patch("src.services.youtube.YouTubeTranscriptApi")
    def test_custom_languages(self, mock_api_class):
        mock_transcript = MagicMock()
        mock_transcript.__iter__ = MagicMock(return_value=iter([]))
        mock_transcript.language = "Spanish"
        mock_transcript.language_code = "es"
        mock_transcript.is_generated = False

        mock_api = MagicMock()
        mock_api.fetch.return_value = mock_transcript
        mock_api_class.return_value = mock_api

        result = fetch_transcript("test123", languages=["es", "es-MX"])

        mock_api.fetch.assert_called_once_with("test123", languages=["es", "es-MX"])
        assert result["language_code"] == "es"


class TestFetchVideoMetadata:
    @pytest.mark.asyncio
    async def test_returns_metadata(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "title": "Test Video",
            "author_name": "Test Channel",
            "author_url": "https://youtube.com/@testchannel",
            "thumbnail_url": "https://i.ytimg.com/vi/test/maxresdefault.jpg",
        }
        mock_response.raise_for_status = MagicMock()

        with patch("src.services.youtube.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await fetch_video_metadata("test123")

            assert result["title"] == "Test Video"
            assert result["channel"] == "Test Channel"
            assert result["channel_url"] == "https://youtube.com/@testchannel"
            assert result["thumbnail_url"] == "https://i.ytimg.com/vi/test/maxresdefault.jpg"
