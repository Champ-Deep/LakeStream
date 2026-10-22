"""Unit tests for the sitemap graph transformation."""

from src.services.sitemap_graph import build_sitemap_graph


def _node_ids(graph: dict) -> set[str]:
    return {n["id"] for n in graph["nodes"]}


def _node_by_id(graph: dict, node_id: str) -> dict:
    return next(n for n in graph["nodes"] if n["id"] == node_id)


def _edge_pairs(graph: dict) -> set[tuple[str, str]]:
    return {(e["from"], e["to"]) for e in graph["edges"]}


def test_single_root_url_produces_root_only():
    graph = build_sitemap_graph(["https://example.com/"])

    assert _node_ids(graph) == {"example.com"}
    root = _node_by_id(graph, "example.com")
    assert root["depth"] == 0
    assert root["is_root"] is True
    assert root["label"] == "example.com"
    assert graph["edges"] == []


def test_flat_urls_all_depth_one():
    urls = [
        "https://example.com/about",
        "https://example.com/blog",
        "https://example.com/contact",
    ]
    graph = build_sitemap_graph(urls)

    assert _node_ids(graph) == {
        "example.com",
        "example.com/about",
        "example.com/blog",
        "example.com/contact",
    }
    for leaf in ("example.com/about", "example.com/blog", "example.com/contact"):
        node = _node_by_id(graph, leaf)
        assert node["depth"] == 1
        assert node["is_root"] is False
        assert node["section"] == leaf.split("/", 1)[1]

    # Every leaf is linked directly to the root.
    assert _edge_pairs(graph) == {
        ("example.com", "example.com/about"),
        ("example.com", "example.com/blog"),
        ("example.com", "example.com/contact"),
    }


def test_deep_url_creates_intermediate_nodes_and_chain_of_edges():
    graph = build_sitemap_graph(
        ["https://example.com/docs/api/v2/guide/quickstart"]
    )

    expected_ids = {
        "example.com",
        "example.com/docs",
        "example.com/docs/api",
        "example.com/docs/api/v2",
        "example.com/docs/api/v2/guide",
        "example.com/docs/api/v2/guide/quickstart",
    }
    assert _node_ids(graph) == expected_ids

    # Depth is derived from the path segment count.
    assert _node_by_id(graph, "example.com/docs/api/v2/guide/quickstart")["depth"] == 5
    # Section is the top-level segment for every descendant.
    for nid in expected_ids - {"example.com"}:
        assert _node_by_id(graph, nid)["section"] == "docs"

    # Edges form a single chain from root to leaf.
    assert _edge_pairs(graph) == {
        ("example.com", "example.com/docs"),
        ("example.com/docs", "example.com/docs/api"),
        ("example.com/docs/api", "example.com/docs/api/v2"),
        ("example.com/docs/api/v2", "example.com/docs/api/v2/guide"),
        ("example.com/docs/api/v2/guide", "example.com/docs/api/v2/guide/quickstart"),
    }


def test_duplicate_urls_are_deduplicated():
    urls = [
        "https://example.com/blog/post-1",
        "https://example.com/blog/post-1",
        "https://www.example.com/blog/post-1",  # www. is stripped, same node
        "https://example.com/blog/post-1/",     # trailing slash, same node
    ]
    graph = build_sitemap_graph(urls)

    assert _node_ids(graph) == {
        "example.com",
        "example.com/blog",
        "example.com/blog/post-1",
    }
    assert _edge_pairs(graph) == {
        ("example.com", "example.com/blog"),
        ("example.com/blog", "example.com/blog/post-1"),
    }


def test_empty_and_invalid_inputs_are_ignored():
    graph = build_sitemap_graph([])
    assert graph == {"nodes": [], "edges": []}

    graph = build_sitemap_graph(["", "   ", "not a url", None])  # type: ignore[list-item]
    # "not a url" has no netloc after urlparse, so it yields no nodes.
    assert graph["nodes"] == []
    assert graph["edges"] == []


def test_multiple_hosts_produce_separate_roots():
    urls = [
        "https://example.com/about",
        "https://other.org/docs/intro",
    ]
    graph = build_sitemap_graph(urls)

    assert {"example.com", "other.org"} <= _node_ids(graph)
    # Each host has its own hierarchy, no cross edges.
    edges = _edge_pairs(graph)
    assert ("example.com", "example.com/about") in edges
    assert ("other.org", "other.org/docs") in edges
    assert ("other.org/docs", "other.org/docs/intro") in edges
    for src, dst in edges:
        # Edges never cross hosts.
        assert src.split("/", 1)[0] == dst.split("/", 1)[0]
