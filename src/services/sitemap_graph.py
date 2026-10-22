"""Sitemap graph transformation.

Pure functions that turn a flat list of discovered URLs into a
GraphData payload (nodes + edges) that the frontend can hand to
vis-network without further processing.

The output shape is intentionally forward compatible so that Phase 2
(real hyperlink edges via an outbound_links[] payload) and Phase 3
(graphify semantic enrichment) can inject fields without rewriting
the visualization layer.

    Phase 1  : hierarchy edges inferred from URL paths.
    Phase 2+ : additional edges or node attributes are merged into
               the same dict structure.

Contract:

    build_sitemap_graph(urls: list[str]) -> dict
        {
          "nodes": [
            {
              "id":      str,   # unique node id (host or host+path prefix)
              "label":   str,   # last path segment, or host for the root
              "depth":   int,   # 0 for root, 1 for top-level section, ...
              "section": str,   # top-level path segment, e.g. "blog"
              "url":     str,   # full URL (https://host/prefix) for tooltip
              "is_root": bool,  # true for the domain node
            },
            ...
          ],
          "edges": [
            {"from": str, "to": str},
            ...
          ],
        }
"""

from __future__ import annotations

from urllib.parse import urlparse


def _normalize_host(host: str) -> str:
    """Lowercase and strip a leading www. for stable node ids."""
    host = (host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _split_path(path: str) -> list[str]:
    """Split a URL path into non-empty segments."""
    if not path:
        return []
    return [seg for seg in path.split("/") if seg]


def build_sitemap_graph(urls: list[str]) -> dict:
    """Transform a list of URLs into a GraphData payload.

    - Duplicate URLs are deduplicated.
    - Every path prefix becomes its own node, not just leaf URLs.
    - Parent to child edges are inferred from the URL path.
    - If multiple hosts appear, each is rendered as its own root.
    """
    nodes: dict[str, dict] = {}
    edges: set[tuple[str, str]] = set()

    for raw in urls or []:
        if not raw or not isinstance(raw, str):
            continue

        parsed = urlparse(raw.strip())
        host = _normalize_host(parsed.netloc)
        if not host:
            continue

        # Root node for this host.
        root_id = host
        if root_id not in nodes:
            nodes[root_id] = {
                "id": root_id,
                "label": host,
                "depth": 0,
                "section": "",
                "url": f"{parsed.scheme or 'https'}://{host}/",
                "is_root": True,
            }

        segments = _split_path(parsed.path)
        parent_id = root_id

        for idx, segment in enumerate(segments, start=1):
            node_id = f"{host}/{'/'.join(segments[:idx])}"

            if node_id not in nodes:
                nodes[node_id] = {
                    "id": node_id,
                    "label": segment,
                    "depth": idx,
                    "section": segments[0],
                    "url": f"{parsed.scheme or 'https'}://{host}/{'/'.join(segments[:idx])}",
                    "is_root": False,
                }

            edges.add((parent_id, node_id))
            parent_id = node_id

    return {
        "nodes": list(nodes.values()),
        "edges": [{"from": src, "to": dst} for src, dst in sorted(edges)],
    }
