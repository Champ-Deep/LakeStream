import pytest


@pytest.fixture
def sample_wordpress_html() -> str:
    return """
    <html>
    <head><title>Test Blog - WordPress</title></head>
    <body>
    <div class="wp-content">
        <article class="post">
            <h2 class="entry-title"><a href="/blog/test-post-1" rel="bookmark">Test Post 1</a></h2>
            <time class="entry-date" datetime="2024-01-15">January 15, 2024</time>
            <span class="author">John Doe</span>
        </article>
        <article class="post">
            <h2 class="entry-title"><a href="/blog/test-post-2" rel="bookmark">Test Post 2</a></h2>
            <time class="entry-date" datetime="2024-01-10">January 10, 2024</time>
            <span class="author">Jane Smith</span>
        </article>
    </div>
    <nav class="pagination">
        <a class="page-numbers" href="/blog/page/2">2</a>
        <a class="next page-numbers" href="/blog/page/2">Next</a>
    </nav>
    </body>
    </html>
    """


@pytest.fixture
def sample_team_page_html() -> str:
    return """
    <html>
    <head><title>Our Team</title></head>
    <body>
    <div class="team-section">
        <div class="team-member">
            <h3 class="name">Alice Johnson</h3>
            <p class="title">VP of Engineering</p>
            <a href="https://linkedin.com/in/alicejohnson">LinkedIn</a>
        </div>
        <div class="team-member">
            <h3 class="name">Bob Williams</h3>
            <p class="title">Director of Marketing</p>
            <a href="mailto:bob@example.com">Email</a>
        </div>
    </div>
    </body>
    </html>
    """
