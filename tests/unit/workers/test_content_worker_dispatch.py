"""Guards on ContentWorker's extraction dispatch inside _process_url.

tests/unit/workers/test_content_worker.py mocks `_process_url` wholesale, so
nothing checked that the `self._extract_*(...)` calls inside it actually match
the methods they call. Merging the branches produced exactly that mismatch —
`_extract_article_record(url, parser, rich_meta)` against a signature of
`(url, html, parser, rich_meta, template=None)` — which raised TypeError on
every URL and silently reduced completed jobs to zero records, while the whole
unit suite stayed green.

The first test is a static check over every dispatch call, so this class of
mismatch cannot pass again. The second runs the real dispatch end to end with
only the fetch and the DB mocked.
"""

import ast
import inspect
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.scraped_data import DataType
from src.workers.content_worker import ContentWorker

_SOURCE = inspect.getsource(inspect.getmodule(ContentWorker))


def _dispatch_calls():
    """Every `self._extract_*(...)` call in the module, with its arity."""
    out = []
    for node in ast.walk(ast.parse(_SOURCE)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (
            isinstance(fn, ast.Attribute)
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "self"
            and fn.attr.startswith("_extract")
        ):
            continue
        if any(isinstance(a, ast.Starred) for a in node.args):
            continue
        kwargs = [k.arg for k in node.keywords if k.arg is not None]
        out.append((fn.attr, len(node.args), kwargs, node.lineno))
    return out


class TestDispatchSignaturesMatch:
    def test_dispatch_calls_are_present(self):
        """Sanity: the AST scan finds the calls it is meant to police."""
        names = {c[0] for c in _dispatch_calls()}
        assert {
            "_extract_page_record",
            "_extract_article_record",
            "_extract_blog_landing",
            "_extract_contacts",
            "_extract_tech_stack",
        } <= names

    def test_every_dispatch_call_binds_to_its_method(self):
        """Each call site must be callable against the method it names."""
        failures = []
        for name, n_pos, kwargs, lineno in _dispatch_calls():
            method = getattr(ContentWorker, name, None)
            if method is None:
                failures.append(f"line {lineno}: ContentWorker has no {name}")
                continue
            sig = inspect.signature(method)
            args = [None] * (n_pos + 1)  # +1 for self
            try:
                sig.bind(*args, **dict.fromkeys(kwargs))
            except TypeError as e:
                failures.append(f"line {lineno}: {name}({n_pos} positional) — {e}")
        assert not failures, "extraction dispatch does not match signatures:\n  " + \
            "\n  ".join(failures)

    def test_template_aware_extractors_receive_the_template(self):
        """The template-registry wiring is easy to drop silently.

        `template` is an optional parameter, so omitting it still binds — the
        job just quietly falls back to generic parsing. Assert it is passed.
        """
        template_aware = {"_extract_article_record", "_extract_blog_landing"}
        seen = {}
        for name, n_pos, kwargs, lineno in _dispatch_calls():
            if name not in template_aware:
                continue
            sig = inspect.signature(getattr(ContentWorker, name))
            params = list(sig.parameters)  # includes self
            passes_template = (n_pos + 1) >= len(params) or "template" in kwargs
            seen[name] = seen.get(name, False) or passes_template
        for name in template_aware:
            assert seen.get(name), (
                f"{name} is called without the resolved template — the "
                "platform-specific selectors are silently unused"
            )


ARTICLE_HTML = """
<html><head><title>A Real Post</title>
<meta name="generator" content="WordPress 6.4"/>
<meta property="og:title" content="A Real Post"/></head>
<body><nav>menu</nav>
<article><h1>A Real Post</h1>
<p>%s</p>
</article><footer>c</footer></body></html>
""" % ("substantive body text that easily clears the article word floor " * 30)


class TestProcessUrlRunsRealExtraction:
    """The regression test proper: real dispatch, only fetch and DB mocked."""

    @pytest.mark.asyncio
    async def test_page_article_and_tech_records_are_produced(self):
        worker = ContentWorker(domain="example.com", job_id=str(uuid4()))

        fetch_result = MagicMock()
        fetch_result.blocked = False
        fetch_result.html = ARTICLE_HTML
        fetch_result.status_code = 200
        fetch_result.content_type = "text/html"
        fetch_result.headers = {"Server": "nginx/1.24.0"}
        fetch_result.screenshot_bytes = None
        fetch_result.content_bytes = None

        worker.fetch_page = AsyncMock(return_value=fetch_result)
        worker.export_results = AsyncMock()

        with patch("src.config.settings.get_settings") as s:
            s.return_value = MagicMock(
                max_scrape_pages_per_job=10, min_html_bytes=100,
            )
            records = await worker._process_url(
                "https://example.com/a-real-post",
                DataType.PAGE,
                ["page", "article", "tech_stack"],
            )

        kinds = {r.data_type for r in records}
        assert DataType.PAGE in kinds, f"no page record; got {kinds}"
        assert DataType.ARTICLE in kinds, f"no article record; got {kinds}"
        assert DataType.TECH_STACK in kinds, f"no tech_stack record; got {kinds}"

        tech = next(r for r in records if r.data_type == DataType.TECH_STACK)
        detected = {d["name"] for d in tech.metadata.get("detections", [])}
        # meta generator (high) and the Server header (high) both via the Go
        # fetcher's real response shape.
        assert "WordPress" in detected
        assert "nginx" in detected
