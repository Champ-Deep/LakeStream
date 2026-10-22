"""Knowledge-graph assembly from the crawl link graph (v2).

Builds a nodes+edges structure for a domain: nodes are the distinct URLs seen
in the link graph, typed via the URL classifier and enriched with the actual
data_type when the page was extracted; edges are the captured page links.
Plain adjacency JSON — no external graph engine.
"""

from urllib.parse import urlparse
from uuid import UUID

import structlog

from src.db.queries.page_links import get_edges_by_domain
from src.scraping.parser.url_classifier import classify_url

log = structlog.get_logger()

# Cap so a huge crawl can't produce an unrenderable graph.
_MAX_NODES = 500
_MAX_EDGES = 2000


async def build_graph(pool, domain: str, user_id: UUID | None = None) -> dict:
    """Return {nodes: [...], edges: [...], truncated: bool} for a domain."""
    edge_rows = await get_edges_by_domain(pool, domain, user_id=user_id, limit=_MAX_EDGES)

    # Actual extracted types override the URL-pattern guess.
    if user_id is None:
        type_rows = await pool.fetch(
            "SELECT DISTINCT url, data_type FROM scraped_data WHERE domain = $1", domain
        )
    else:
        type_rows = await pool.fetch(
            "SELECT DISTINCT url, data_type FROM scraped_data "
            "WHERE domain = $1 AND user_id = $2",
            domain,
            user_id,
        )
    extracted_type: dict[str, str] = {}
    for row in type_rows:
        url = row["url"]
        dt = row["data_type"]
        # Prefer a specific type over the generic 'page'
        if url and (url not in extracted_type or extracted_type[url] == "page"):
            extracted_type[url] = dt

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add_node(url: str) -> None:
        if url in nodes or len(nodes) >= _MAX_NODES:
            return
        dtype = extracted_type.get(url) or classify_url(url)["data_type"]
        nodes[url] = {
            "id": url,
            "label": _short_label(url),
            "type": dtype,
        }

    for row in edge_rows:
        src, tgt = row["source_url"], row["target_url"]
        add_node(src)
        add_node(tgt)
        if src in nodes and tgt in nodes:
            edges.append({"source": src, "target": tgt})
        if len(edges) >= _MAX_EDGES:
            break

    # Include extracted pages that had no captured edges as isolated nodes.
    for url in extracted_type:
        add_node(url)

    truncated = len(nodes) >= _MAX_NODES or len(edges) >= _MAX_EDGES
    return {"nodes": list(nodes.values()), "edges": edges, "truncated": truncated}


def _short_label(url: str) -> str:
    """A compact node label: the path (or host for the root)."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return path if path else parsed.netloc
