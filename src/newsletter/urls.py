import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

TRACKING_PARAMS: frozenset[str] = frozenset(
    {"ref", "source", "fbclid", "gclid", "mc_cid", "mc_eid"}
)


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k in TRACKING_PARAMS or k.startswith("utm_")


def canonicalize_url(url: str, canonical_link: str | None = None) -> str:
    """Normalize a URL so variants of one article share a key.

    A page's `<link rel="canonical">` wins and is resolved against `url`.
    """
    if canonical_link:
        url = urljoin(url, canonical_link.strip())
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)]
    )
    return urlunsplit((scheme, netloc, parts.path.rstrip("/"), query, ""))


def normalize_title(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.split())


def dedup_key(title: str, author: str | None) -> str | None:
    if not author or not author.strip() or not normalize_title(title):
        return None
    return f"{normalize_title(title)}|{normalize_title(author)}"
