from newsletter.urls import canonicalize_url, dedup_key


def test_canonicalize_basic():
    assert canonicalize_url("https://www.Example.com/Post/?utm_source=x&id=3#frag") == "https://example.com/Post?id=3"


def test_canonicalize_medium_rss_param():
    assert canonicalize_url("https://medium.com/pinterest-engineering/a-post-abc123?source=rss----4c5a5f6279b6---4") \
        == "https://medium.com/pinterest-engineering/a-post-abc123"


def test_canonical_link_wins_and_is_resolved():
    assert canonicalize_url("https://x.com/c?utm_medium=1", "/a/b/") == "https://x.com/a/b"


def test_root_url():
    assert canonicalize_url("https://www.x.com/") == "https://x.com"


def test_dedup_key():
    assert dedup_key("Hello,  World!", "Jane Doe") == "hello world|jane doe"
    assert dedup_key("Hello", None) is None
