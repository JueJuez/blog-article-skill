"""Project finding: turn an input (article/video/direct text) into repo candidates.

Combines content_source.resolve (input routing) with extractor (URL extraction +
three-layer dedup). Produces Candidate objects tagged with where the repo link was
found (description / subtitle / body / direct) so callers can prioritize.
"""
from assets.extractor import (
    extract_repo_urls,
    normalize_url,
    owner_repo_key,
    batch_deduplicate,
    filter_imported,
    filter_pending,
)
from assets.content_source import resolve


class Candidate:
    def __init__(self, url: str, owner_repo: str, source_kind: str, platform: str = None):
        self.url = url
        self.owner_repo = owner_repo
        self.source_kind = source_kind   # description | subtitle | body | direct
        self.platform = platform

    def __repr__(self):
        return f"<Candidate {self.owner_repo} via {self.source_kind}>"


def find(input_spec: str, dedup: bool = True):
    """Extract repo candidates from an input.

    Returns (candidates, skipped) where:
      - candidates: list[Candidate] (new, not yet imported/pending)
      - skipped:    list[str] of URLs skipped by dedup (imported.txt / pending_results.json)
    """
    sources, _platform = resolve(input_spec)

    # 1) collect (url, source_kind) pairs from every source chunk
    pairs = []  # (url, source_kind)
    for s in sources:
        for u in extract_repo_urls(s.text):
            pairs.append((normalize_url(u), s.kind))

    # 2) dedup within input by owner/repo key (keep first occurrence / its source)
    seen = set()
    unique = []
    for url, kind in pairs:
        key = owner_repo_key(url)
        if key in seen:
            continue
        seen.add(key)
        unique.append((url, kind))

    urls = [u for u, _ in unique]
    kind_map = {u: k for u, k in unique}

    # 3) three-layer dedup vs imported.txt / pending_results.json
    skipped = []
    if dedup and urls:
        urls, imported_skipped = filter_imported(urls)
        urls, pending_skipped = filter_pending(urls)
        skipped = imported_skipped + pending_skipped

    candidates = [
        Candidate(u, owner_repo_key(u), kind_map[u], _platform) for u in urls
    ]
    return candidates, skipped
