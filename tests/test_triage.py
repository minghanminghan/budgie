import json
import subprocess

import pytest
import yaml

from newsletter.cli import main
from newsletter.triage import create_ticket, pending_ticket, probe, track_pending
from tests.fakes import FakeAdapter
from tests.fakeweb import FakeWeb

URL = "https://odd.example.com/blog"
YAML = """sources:
  # RSS / Atom
  - https://good.example.com/feed
  - https://odd.example.com/blog
  - url: https://pinned.example.com/eng
    adapter: listing
"""


def odd_page():
    return ('<html><body><nav><a href="/about">About</a></nav>'
            '<a href="/blog/2026/one">One</a><a href="/blog/2026/two">Two</a>'
            '<a href="https://other.example.com/x">Elsewhere</a></body></html>')


class FakeSparrow:
    def __init__(self, task_id="BUDGIE-7"):
        self.task_id = task_id
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, cmd, input, capture_output, text, check):
        self.calls.append((cmd, input))
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"id": self.task_id, "title": "t"}))


def test_probe_reports_each_adapter_and_page_links():
    web = FakeWeb({URL: odd_page()})
    result = probe(URL, web.client(), [FakeAdapter()])
    assert result.found is None
    assert "- fake: nothing found" in result.report
    assert "HTTP 200" in result.report
    assert "/blog/2026/one" in result.report and "/about" in result.report
    assert "other.example.com" not in result.report


def test_probe_found():
    result = probe(URL, FakeWeb({}).client(), [FakeAdapter(known=[URL])])
    assert result.found.title == "Fake Blog"


def test_create_ticket_sends_report_to_project():
    run = FakeSparrow()
    result = probe(URL, FakeWeb({URL: odd_page()}).client(), [FakeAdapter()])
    assert create_ticket(result, project="BUDGIE", sparrow="sparrow", run=run) == "BUDGIE-7"
    cmd, body = run.calls[0]
    assert cmd[:3] == ["sparrow", "create", "--json"]
    assert cmd[cmd.index("--project") + 1] == "BUDGIE"
    assert "--parent" not in cmd
    assert cmd[cmd.index("--title") + 1] == "no feed found for odd.example.com/blog"
    assert body.startswith(result.report)
    assert "include:" in body  # the next steps say how to fix it


def test_create_ticket_with_parent():
    run = FakeSparrow()
    result = probe(URL, FakeWeb({URL: odd_page()}).client(), [FakeAdapter()])
    create_ticket(result, project="BUDGIE", parent="BUDGIE-2", sparrow="sparrow", run=run)
    cmd, _ = run.calls[0]
    assert cmd[cmd.index("--parent") + 1] == "BUDGIE-2"
    assert cmd[cmd.index("--project") + 1] == "BUDGIE"  # sparrow rejects a parent from another project


def test_track_pending_moves_plain_entry_and_keeps_comments(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(YAML)
    track_pending(path, URL, "BUDGIE-7")
    text = path.read_text()
    assert "# RSS / Atom" in text
    data = yaml.safe_load(text)
    assert URL not in data["sources"]
    assert data["pending"] == [{"url": URL, "ticket": "BUDGIE-7"}]
    track_pending(path, "https://new.example.com/", "BUDGIE-8")
    assert yaml.safe_load(path.read_text())["pending"][1] == {"url": "https://new.example.com/", "ticket": "BUDGIE-8"}
    assert pending_ticket(path, URL) == "BUDGIE-7"
    assert pending_ticket(path, "https://good.example.com/feed") is None


def test_track_pending_refuses_when_pending_is_not_last(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text("pending:\n  - url: https://a.example.com/\n    ticket: 1\nsources:\n  - https://b.example.com/\n")
    with pytest.raises(ValueError):
        track_pending(path, URL, "BUDGIE-7")


def test_cli_ticket(tmp_path, monkeypatch, capsys):
    path = tmp_path / "sources.yaml"
    path.write_text(YAML)
    run = FakeSparrow()
    monkeypatch.setattr("newsletter.triage.subprocess.run", run)
    monkeypatch.setattr("newsletter.cli.PoliteClient", lambda: FakeWeb({URL: odd_page()}).client())
    monkeypatch.setattr("newsletter.cli.default_adapters", lambda: [FakeAdapter(known=["https://good.example.com/feed"])])
    # a missing --sparrow binary is the safety net: no test may reach the real board
    args = ["ticket", "--sources", str(path), "--sparrow", str(tmp_path / "no-sparrow")]
    assert main([*args, URL, "https://good.example.com/feed"]) == 0
    assert main([*args, URL]) == 0
    out = capsys.readouterr().out
    assert f"ticket  {URL} -> BUDGIE-7" in out
    assert "found   https://good.example.com/feed -> fake: Fake Blog" in out
    assert f"skip    {URL} (already pending: BUDGIE-7)" in out
    assert len(run.calls) == 1
    cmd = run.calls[0][0]
    assert cmd[0] == str(tmp_path / "no-sparrow")
    assert cmd[cmd.index("--project") + 1] == "BUDGIE"
