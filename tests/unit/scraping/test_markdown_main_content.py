"""Tests for main-content isolation in src/scraping/parser/markdown.py.

`find_main_html` used to return the first container matching its selector
priority list, without checking that the container actually held the page's
content. Measured against Firecrawl on live pages, that lost most of the text on
two shapes of page, both covered here:

  - a blog *listing* page, where the first <article> is one post teaser
    (djangoproject.com/weblog/ returned 0.33x Firecrawl's word count);
  - a page whose <main> is an empty shell filled in by JS, while the real
    content sits elsewhere in the document (stripe.com/blog returned 0.07x).

After scoring candidates by text volume those became 1.00x and 2.40x, with the
already-passing pages unchanged.
"""

from src.scraping.parser.markdown import (
    MAIN_CONTENT_MIN_TEXT_SHARE,
    find_main_html,
    html_to_markdown,
)

LOREM = "content words here and there " * 40


def test_single_article_page_still_selects_the_article():
    """The common case must not regress: one article holding the page's text."""
    html = (
        "<html><body><nav>menu links</nav>"
        f"<article><h1>The Post</h1><p>{LOREM}</p></article>"
        "<footer>copyright</footer></body></html>"
    )
    main = find_main_html(html)
    assert "The Post" in main
    assert "menu links" not in main
    assert "copyright" not in main


def test_listing_page_does_not_collapse_to_the_first_article():
    """A listing page's first <article> is a teaser, not the main content."""
    posts = "".join(
        f"<article><h2>Post {i}</h2><p>{LOREM}</p></article>" for i in range(6)
    )
    html = f"<html><body><nav>menu</nav>{posts}</body></html>"
    main = find_main_html(html)
    # Every post survives, not just the first.
    for i in range(6):
        assert f"Post {i}" in main, f"lost Post {i} from the listing"


def test_empty_main_shell_falls_back_to_the_real_content():
    """<main> as a JS placeholder must not win over the document's actual text."""
    html = (
        '<html><body><main id="app"></main>'
        f'<div class="posts"><h2>Real Heading</h2><p>{LOREM}</p></div>'
        "</body></html>"
    )
    main = find_main_html(html)
    assert "Real Heading" in main
    assert LOREM.split()[0] in main


def test_richer_candidate_wins_over_higher_priority_thin_one():
    """Selector priority breaks ties; it does not override text volume."""
    html = (
        "<html><body>"
        "<main><p>tiny</p></main>"
        f'<div class="entry-content"><p>{LOREM}</p></div>'
        "</body></html>"
    )
    main = find_main_html(html)
    assert LOREM.split()[0] in main


def test_no_matching_selector_falls_back_to_body():
    html = f"<html><body><section><p>{LOREM}</p></section></body></html>"
    main = find_main_html(html)
    assert LOREM.split()[0] in main


def test_noise_is_stripped_from_the_body_fallback_too():
    """Scoring happens after noise removal, so the fallback is clean as well."""
    posts = "".join(f"<article><p>{LOREM}</p></article>" for _ in range(4))
    html = (
        "<html><body><nav>NAVNOISE</nav><aside>ASIDENOISE</aside>"
        f"{posts}<footer>FOOTERNOISE</footer></body></html>"
    )
    main = find_main_html(html)
    for noise in ("NAVNOISE", "ASIDENOISE", "FOOTERNOISE"):
        assert noise not in main


def test_strip_noise_false_keeps_noise_inside_the_container():
    """The flag controls decomposition, not which container is selected.

    Noise outside the winning container is still absent simply because it is not
    part of that subtree.
    """
    html = (
        "<html><body>"
        f"<main><nav>INNERNAV</nav><p>{LOREM}</p></main>"
        "</body></html>"
    )
    assert "INNERNAV" in find_main_html(html, strip_noise=False)
    assert "INNERNAV" not in find_main_html(html, strip_noise=True)


def test_explicit_selectors_are_honoured():
    html = (
        f'<html><body><main><p>{LOREM}</p></main>'
        f'<div class="custom"><p>{LOREM} CUSTOMMARK</p></div></body></html>'
    )
    assert "CUSTOMMARK" in find_main_html(html, selectors=[".custom"])


def test_threshold_is_a_documented_constant():
    assert 0 < MAIN_CONTENT_MIN_TEXT_SHARE < 1


def test_html_to_markdown_uses_the_improved_selection():
    """End-to-end: the listing-page fix shows up in markdown output."""
    posts = "".join(
        f"<article><h2>Post {i}</h2><p>{LOREM}</p></article>" for i in range(5)
    )
    md = html_to_markdown(f"<html><body><nav>menu</nav>{posts}</body></html>")
    assert md.count("Post ") >= 5


def test_html_to_markdown_full_page_mode_unaffected():
    html = f"<html><body><main><p>{LOREM}</p></main></body></html>"
    assert len(html_to_markdown(html, find_main=False).split()) > 0
