import pytest

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


class OtherFake(FakeAdapter):
    name = "other"

    def discover(self, url, client):
        cfg = super().discover(url, client)
        if cfg is not None:
            cfg.adapter, cfg.config_key = "other", f"other:{url}"
        return cfg


def test_seed_from_yaml_pins_adapter(session, now, tmp_path):
    url1, url2 = "https://fake.example.com/a", "https://fake.example.com/b"
    path = tmp_path / "sources.yaml"
    path.write_text(f"sources:\n  - {url1}\n  - url: {url2}\n    adapter: other\n")
    adapters = [FakeAdapter(known=[url1, url2]), OtherFake(known=[url1, url2])]
    result = seed_from_yaml(session, path, None, adapters, now)
    assert [u for u, _ in result] == [url1, url2]
    assert result[0][1].adapter == "fake"
    assert result[1][1].adapter == "other"


def test_preview_unknown_pinned_adapter_returns_none():
    url = "https://fake.example.com/"
    assert preview(url, None, [FakeAdapter(known=[url])], adapter="nope") is None


class OptionFake(FakeAdapter):
    name = "opt"

    def discover(self, url, client, include=None):
        cfg = super().discover(url, client)
        if cfg is not None:
            cfg.adapter, cfg.config_key, cfg.config = "opt", f"opt:{url}", {"url": url, "include": include}
        return cfg


def test_seed_passes_options_and_ignores_ticket(session, now, tmp_path):
    url = "https://fake.example.com/a"
    path = tmp_path / "sources.yaml"
    path.write_text(f"sources:\n  - url: {url}\n    adapter: opt\n    include: ^/a/\n    ticket: 32\n"
                    f"pending:\n  - url: https://nope.example.com/\n    ticket: 33\n")
    result = seed_from_yaml(session, path, None, [OptionFake(known=[url])], now)
    assert [u for u, _ in result] == [url]  # pending entries are not seeded
    assert result[0][1].config == {"url": url, "include": "^/a/"}


def test_reseed_updates_config(session, now, tmp_path):
    url = "https://fake.example.com/a"
    path = tmp_path / "sources.yaml"
    path.write_text(f"sources:\n  - url: {url}\n    adapter: opt\n    include: ^/a/\n")
    first = seed_from_yaml(session, path, None, [OptionFake(known=[url])], now)[0][1]
    path.write_text(f"sources:\n  - url: {url}\n    adapter: opt\n    include: ^/b/\n")
    second = seed_from_yaml(session, path, None, [OptionFake(known=[url])], now)[0][1]
    assert second.id == first.id
    assert second.config == {"url": url, "include": "^/b/"}


def test_options_need_a_pinned_adapter():
    url = "https://fake.example.com/"
    with pytest.raises(ValueError):
        preview(url, None, [OptionFake(known=[url])], options={"include": "x"})


def test_seed_without_reactivate_keeps_unsubscribed(session, now, tmp_path):
    url = "https://fake.example.com/a"
    adapters = [FakeAdapter(known=[url])]
    source = subscribe(session, url, None, adapters, now)
    unsubscribe(session, source.id, now)
    path = tmp_path / "sources.yaml"
    path.write_text(f"sources:\n  - {url}\n")
    [(_, got)] = seed_from_yaml(session, path, None, adapters, now, reactivate=False)
    assert got.id == source.id and got.status == "unsubscribed" and got.unsubscribed_at == now
