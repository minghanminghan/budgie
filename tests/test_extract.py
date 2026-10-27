from newsletter.ingest.extract import extract_page, looks_like_teaser, word_count
from tests.fakeweb import fixture


def test_extract_marks_substack_teaser():
    html = fixture("substack_paywalled.html").decode()
    assert extract_page(html, "https://x.substack.com/p/big").is_teaser is True
    html = fixture("article.html").decode()
    assert extract_page(html, "https://summary.example.com/posts/article").is_teaser is False


def test_extract_article_metadata():
    ex = extract_page(fixture("article.html").decode(), "https://summary.example.com/posts/article")
    assert ex.canonical_link == "https://summary.example.com/posts/how-we-scaled"
    assert ex.author == "Ada Lovelace"
    assert ex.published_at.isoformat() == "2026-10-25T09:30:00+00:00"
    assert word_count(ex.text) > 300


def test_looks_like_teaser_needs_marker_and_short_text():
    assert looks_like_teaser('<div class="paywall">', "few words") is True
    assert looks_like_teaser("<div>", "few words") is False
    assert looks_like_teaser('<div class="paywall">', "word " * 700) is False
