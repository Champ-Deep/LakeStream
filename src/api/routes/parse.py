"""Parse Bytes — arbitrary document/file → clean markdown.

POST /api/parse accepts, in order of precedence:
  1. multipart/form-data with a `file` field (upload)
  2. JSON {"url": "..."} — fetch the document then parse
  3. raw request body bytes (Content-Type used as the type hint)
"""

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.middleware.auth import get_current_user
from src.config.settings import get_settings
from src.scraping.parser.document_parser import MAX_DOC_BYTES, parse_document

router = APIRouter(prefix="/parse")
logger = structlog.get_logger()


def _result_payload(result, source: str) -> dict:
    return {
        "success": True,
        "source": source,
        "source_type": result.source_type,
        "markdown": result.markdown,
        "text": result.text,
        "tables": result.tables,
        "metadata": result.metadata,
        "word_count": result.word_count,
        "page_count": result.page_count,
        "table_count": len(result.tables),
        "content_hash": result.content_hash,
    }


@router.post("")
async def parse_bytes(request: Request, user: dict = Depends(get_current_user)):
    """Convert a document (PDF, DOCX, HTML, text) to markdown."""
    settings = get_settings()
    if not settings.enable_document_parsing:
        raise HTTPException(status_code=404, detail="Document parsing is disabled")

    content_type = request.headers.get("content-type", "")

    # 1. Multipart upload
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="multipart requests need a 'file' field")
        content = await upload.read()
        try:
            result = parse_document(
                content, filename=getattr(upload, "filename", None),
                content_type=getattr(upload, "content_type", None),
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return _result_payload(result, source=getattr(upload, "filename", "") or "upload")

    # 2. JSON {url}
    if content_type.startswith("application/json"):
        body = await request.json()
        url = (body.get("url") or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="'url' is required in JSON body")
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Fetch failed: {e}")
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Fetch failed: HTTP {resp.status_code}")
        if len(resp.content) > MAX_DOC_BYTES:
            raise HTTPException(status_code=413, detail=f"Document too large (max {MAX_DOC_BYTES} bytes)")
        try:
            result = parse_document(
                resp.content, filename=url.split("?")[0].rsplit("/", 1)[-1],
                content_type=resp.headers.get("content-type"),
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return _result_payload(result, source=url)

    # 3. Raw body bytes
    content = await request.body()
    if not content:
        raise HTTPException(
            status_code=400,
            detail="Send a multipart 'file', a JSON {url}, or raw document bytes",
        )
    if len(content) > MAX_DOC_BYTES:
        raise HTTPException(status_code=413, detail=f"Document too large (max {MAX_DOC_BYTES} bytes)")
    try:
        result = parse_document(content, content_type=content_type or None)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _result_payload(result, source="body")
