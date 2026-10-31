"""Sources no adapter can read: probe them, file a Sparrow task with the report, and
track them under `pending:` in sources.yaml until a rule is found."""
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import lxml.html
import yaml

from newsletter.http import PoliteClient, RobotsDisallowed
from newsletter.ingest.base import Adapter, SourceConfig, domain_of

SPARROW_PROJECT = "BUDGIE"  # this repo's Sparrow project; task keys look like BUDGIE-7
SAMPLE_LINKS = 40
FEW_LINKS = 5

NEXT_STEPS = """
Next steps:
- Read the links above and find the pattern that only posts match.
- Move the entry from `pending:` to `sources:` in sources.yaml, keeping the ticket:
    - url: {url}
      adapter: listing
      include: ^/blog/   # a regex on the link path
      ticket: <this task's key, e.g. BUDGIE-7>
- Run `newsletter seed sources.yaml` and check the post titles it prints in /sources.
- If no rule works (e.g. the page is built by JavaScript), note it here and leave it pending.
"""


@dataclass
class Probe:
    url: str
    found: SourceConfig | None
    report: str


def _page_summary(url: str, client: PoliteClient) -> list[str]:
    try:
        resp = client.get(url)
    except (httpx.HTTPError, RobotsDisallowed) as exc:
        return [f"Page: {type(exc).__name__}: {exc}"]
    lines = [f"Page: HTTP {resp.status_code}, {len(resp.content)} bytes, final URL {resp.url}"]
    if resp.status_code != 200:
        return [*lines, "The site refused or failed the request; no adapter can read it as it is."]
    try:
        doc = lxml.html.fromstring(resp.text)
    except (ValueError, lxml.etree.ParserError):
        return [*lines, "Page is not HTML."]
    host = domain_of(str(resp.url))
    paths: dict[str, str] = {}
    for a in doc.iter("a"):
        target = urljoin(str(resp.url), (a.get("href") or "").strip())
        if urlsplit(target).scheme in ("http", "https") and domain_of(target) == host:
            paths.setdefault(urlsplit(target).path, " ".join(a.text_content().split())[:60])
    lines.append(f"Same-site links: {len(paths)}")
    if len(paths) < FEW_LINKS:
        lines.append("Few links: the page may be built by JavaScript, which no adapter can read.")
    lines += [f"  {p}  {t}" for p, t in list(paths.items())[:SAMPLE_LINKS]]
    if len(paths) > SAMPLE_LINKS:
        lines.append(f"  … {len(paths) - SAMPLE_LINKS} more")
    return lines


def probe(url: str, client: PoliteClient, adapters: list[Adapter]) -> Probe:
    """Ask every adapter about `url`, and list the page's own links for writing a rule."""
    lines = [f"URL: {url}", "", "Adapters:"]
    found = None
    for adapter in adapters:
        try:
            cfg = adapter.discover(url, client)
        except Exception as exc:  # noqa: BLE001  (a diagnostic: report every failure)
            lines.append(f"- {adapter.name}: error {type(exc).__name__}: {exc}")
            continue
        if cfg is None:
            lines.append(f"- {adapter.name}: nothing found")
        else:
            lines.append(f"- {adapter.name}: found {cfg.title} ({'; '.join(cfg.preview_titles)})")
            found = found or cfg
    return Probe(url, found, "\n".join([*lines, "", *_page_summary(url, client)]))


def create_ticket(result: Probe, project: str, sparrow: str, parent: str | None = None, run=None) -> str:
    """Create a Sparrow task holding the probe report; return its key (e.g. BUDGIE-7).
    A `parent` must be in the same project; Sparrow rejects it otherwise."""
    run = run or subprocess.run
    parts = urlsplit(result.url)
    title = f"no feed found for {domain_of(result.url)}{parts.path.rstrip('/')}"
    body = result.report + "\n" + NEXT_STEPS.format(url=result.url)
    cmd = [sparrow, "create", "--json", "--title", title, "--project", project, "--body", "-"]
    if parent:
        cmd += ["--parent", parent]
    done = run(cmd, input=body, capture_output=True, text=True, check=True)
    return str(json.loads(done.stdout)["id"])


def pending_ticket(path: Path, url: str) -> str | None:
    pending = (yaml.safe_load(Path(path).read_text()) or {}).get("pending") or []
    return next((str(e.get("ticket")) for e in pending if e.get("url") == url), None)


def track_pending(path: Path, url: str, ticket: str) -> None:
    """Append `url` to the `pending:` list (kept last in the file) and drop it from
    `sources:` if it is a plain entry there. Edits text, so comments survive."""
    path = Path(path)
    text = path.read_text()
    text = re.sub(rf"(?m)^[ \t]*-[ \t]+{re.escape(url)}[ \t]*\n", "", text)
    top_keys = re.findall(r"(?m)^([A-Za-z_]+):", text)
    if "pending" not in top_keys:
        text = text.rstrip("\n") + "\n\n# Sources no adapter can read yet. Each has a Sparrow ticket.\npending:\n"
    elif top_keys[-1] != "pending":
        raise ValueError(f"{path}: keep `pending:` as the last section so entries can be appended")
    text = text.rstrip("\n") + f"\n  - url: {url}\n    ticket: {ticket}\n"
    path.write_text(text)
    if pending_ticket(path, url) != ticket:
        raise ValueError(f"{path}: could not add {url} to pending")
