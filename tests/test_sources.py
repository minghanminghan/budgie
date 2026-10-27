from newsletter.ingest.sources import preview, seed_from_yaml, subscribe, unsubscribe
from tests.fakes import FakeAdapter


def test_preview_returns_config():
    cfg = preview("https://fake.example.com/", None, [FakeAdapter(known=["https://fake.example.com/"])])
    assert cfg.title == "Fake Blog"
    assert preview("https://nope.example.com/", None, [FakeAdapter()]) is None


def test_subscribe_idempotent_and_reactivates(session, now):
    url = "https://fake.example.com/"
    adapters = [FakeAdapter(known=[url])]
    s1 = subscribe(session, url, None, adapters, now)
    assert s1.status == "active" and s1.config_key == f"fake:{url}"
    assert unsubscribe(session, s1.id, now) is True
    assert s1.status == "unsubscribed" and s1.unsubscribed_at == now
    s2 = subscribe(session, url, None, adapters, now)
    assert s2.id == s1.id
    assert s2.status == "active"
    assert s2.unsubscribed_at is None


def test_subscribe_no_feed_returns_none(session, now):
    assert subscribe(session, "https://nope.example.com/", None, [FakeAdapter()], now) is None


def test_subscribe_rejects_non_http(session, now):
    url = "file:///etc/passwd"
    assert subscribe(session, url, None, [FakeAdapter(known=[url])], now) is None


def test_unsubscribe_unknown_id(session, now):
    assert unsubscribe(session, 999, now) is False


def test_seed_from_yaml(session, now, tmp_path):
    url1, url2 = "https://fake.example.com/a", "https://nope.example.com/b"
    path = tmp_path / "sources.yaml"
    path.write_text(f"sources:\n  - {url1}\n  - {url2}\n")
    result = seed_from_yaml(session, path, None, [FakeAdapter(known=[url1])], now)
    assert [u for u, _ in result] == [url1, url2]
    assert result[0][1].config_key == f"fake:{url1}"
    assert result[1][1] is None
