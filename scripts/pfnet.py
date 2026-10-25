"""Literature source adapters and merge/rank logic. Standard library only.

Every network call is best-effort: a throttled or unreachable source is
reported as a warning and the remaining sources still produce a usable set.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from difflib import SequenceMatcher

UA = "paper-forge/0.1 (mailto:research@example.org)"
TIMEOUT = 30
STOP = {
    "a", "an", "the", "of", "for", "and", "on", "in", "with", "via", "using",
    "towards", "toward", "to", "from", "by", "at", "as", "is", "are", "we",
}


class SourceError(RuntimeError):
    pass


def _get(url: str, headers: dict | None = None, retries: int = 1,
         timeout: int = TIMEOUT) -> str:
    h = {"User-Agent": UA, "Accept": "application/json, application/atom+xml, */*"}
    if headers:
        h.update(headers)
    last = "unknown error"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code in (429, 500, 502, 503):
                time.sleep(1.2 * (attempt + 1))
                continue
            break
        except Exception as exc:  # timeout, DNS, TLS
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(0.8 * (attempt + 1))
    raise SourceError(last)


def norm_title(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = text.lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def tokens(text: str) -> set[str]:
    return {w for w in norm_title(text).split() if w not in STOP and len(w) > 1}


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, norm_title(a), norm_title(b)).ratio()


def strip_markup(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return " ".join(text.split())


def _openalex_abstract(inv) -> str:
    if not inv:
        return ""
    slots = []
    for word, positions in inv.items():
        for pos in positions:
            slots.append((pos, word))
    slots.sort()
    return " ".join(w for _, w in slots)


def _record(source, title, year=None, authors=None, venue="", doi="", abstract="",
            citations=0, url="", arxiv_id=""):
    title = strip_markup(title)
    return {
        "source": source,
        "title": title,
        "year": int(year) if str(year).isdigit() else None,
        "authors": [a for a in (authors or []) if a][:12],
        "venue": strip_markup(venue),
        "doi": (doi or "").replace("https://doi.org/", "").strip().lower(),
        "abstract": strip_markup(abstract),
        "citations": int(citations or 0),
        "url": url or "",
        "arxiv_id": arxiv_id,
    }


def search_openalex(query, limit=25, year_from=None, mailto=""):
    params = {"search": query, "per-page": str(max(1, min(limit, 50)))}
    if year_from:
        params["filter"] = f"from_publication_date:{year_from}-01-01"
    if mailto:
        params["mailto"] = mailto
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    data = json.loads(_get(url))
    out = []
    for w in data.get("results", []):
        loc = (w.get("primary_location") or {}).get("source") or {}
        out.append(_record(
            "openalex",
            title=w.get("title") or w.get("display_name") or "",
            year=w.get("publication_year"),
            authors=[(a.get("author") or {}).get("display_name")
                     for a in w.get("authorships", [])],
            venue=loc.get("display_name") or "",
            doi=w.get("doi") or "",
            abstract=_openalex_abstract(w.get("abstract_inverted_index")),
            citations=w.get("cited_by_count") or 0,
            url=w.get("id") or "",
        ))
    return out


def search_crossref(query, limit=10, year_from=None, mailto=""):
    params = {"query.bibliographic": query, "rows": str(max(1, min(limit, 20)))}
    if year_from:
        params["filter"] = f"from-pub-date:{year_from}-01-01"
    if mailto:
        params["mailto"] = mailto
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    data = json.loads(_get(url))
    out = []
    for it in (data.get("message") or {}).get("items", []):
        authors = []
        for a in it.get("author", []) or []:
            name = " ".join(x for x in [a.get("given"), a.get("family")] if x)
            if name:
                authors.append(name)
        parts = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
        out.append(_record(
            "crossref",
            title=(it.get("title") or [""])[0],
            year=parts[0] if parts else None,
            authors=authors,
            venue=(it.get("container-title") or [""])[0],
            doi=it.get("DOI") or "",
            abstract=it.get("abstract") or "",
            citations=it.get("is-referenced-by-count") or 0,
            url=it.get("URL") or "",
        ))
    return out


def search_semanticscholar(query, limit=20):
    key = os.environ.get("S2_API_KEY", "")
    params = {
        "query": query,
        "limit": str(max(1, min(limit, 50))),
        "fields": "title,abstract,year,authors,venue,externalIds,citationCount,url",
    }
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)
    data = json.loads(_get(url, headers={"x-api-key": key} if key else None))
    out = []
    for p in data.get("data", []) or []:
        ids = p.get("externalIds") or {}
        out.append(_record(
            "semanticscholar",
            title=p.get("title") or "",
            year=p.get("year"),
            authors=[a.get("name") for a in p.get("authors", []) or []],
            venue=p.get("venue") or "",
            doi=ids.get("DOI") or "",
            abstract=p.get("abstract") or "",
            citations=p.get("citationCount") or 0,
            url=p.get("url") or "",
            arxiv_id=ids.get("ArXiv") or "",
        ))
    return out


def search_arxiv(query, limit=20):
    q = f'all:"{query}"'
    url = ("https://export.arxiv.org/api/query?" +
           urllib.parse.urlencode({"search_query": q, "max_results": str(max(1, min(limit, 30)))}))
    xml = _get(url)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml)
    out = []
    for e in root.findall("a:entry", ns):
        aid = (e.findtext("a:id", "", ns) or "").rsplit("/", 1)[-1]
        out.append(_record(
            "arxiv",
            title=e.findtext("a:title", "", ns),
            year=(e.findtext("a:published", "") or "")[:4],
            authors=[n.findtext("a:name", "", ns) for n in e.findall("a:author", ns)],
            venue="arXiv",
            abstract=e.findtext("a:summary", "", ns),
            url=f"https://arxiv.org/abs/{aid}" if aid else "",
            arxiv_id=aid,
        ))
    return out


SOURCES = {
    "openalex": search_openalex,
    "crossref": search_crossref,
    "semanticscholar": search_semanticscholar,
    "arxiv": search_arxiv,
}


def _recency(year):
    if not year:
        return 0.3
    age = max(0, date.today().year - year)
    return max(0.15, 1.0 - age / 12.0)


def _overlap(title, queries):
    t = tokens(title)
    if not t:
        return 0.0
    return max((len(t & tokens(q)) / len(t | tokens(q) or {1})) for q in queries)


def collect(queries, sources=("openalex", "crossref"), limit=40, year_from=None,
            per_source=25, mailto=""):
    """Query every requested source, merge duplicates, rank, and truncate."""
    warnings, records = [], []
    for name in sources:
        fn = SOURCES.get(name)
        if not fn:
            warnings.append(f"{name}: unknown source, skipped")
            continue
        got, errors = 0, 0
        for q in queries:
            try:
                kwargs = {"limit": per_source}
                if name in ("openalex", "crossref"):
                    kwargs["year_from"] = year_from
                if name == "openalex":
                    kwargs["mailto"] = mailto
                records.extend(fn(q, **kwargs))
                got += 1
            except SourceError as exc:
                errors += 1
                if errors == 1:
                    hint = ""
                    if name == "semanticscholar" and not os.environ.get("S2_API_KEY"):
                        hint = " (no S2_API_KEY, free tier is heavily throttled)"
                    warnings.append(f"{name}: {exc}{hint}")
            except Exception as exc:
                errors += 1
                if errors == 1:
                    warnings.append(f"{name}: {type(exc).__name__}: {exc}")
        if got == 0:
            warnings.append(f"{name}: all {len(queries)} queries failed, source dropped")

    merged = {}
    for r in records:
        if not r["title"] or len(r["title"]) < 8:
            continue
        key = r["doi"] or norm_title(r["title"])
        cur = merged.get(key)
        if not cur:
            r = dict(r)
            r["found_by"] = [r["source"]]
            merged[key] = r
            continue
        cur["found_by"] = sorted(set(cur["found_by"] + [r["source"]]))
        cur["citations"] = max(cur["citations"], r["citations"])
        if len(r["abstract"]) > len(cur["abstract"]):
            cur["abstract"] = r["abstract"]
        if not cur["doi"] and r["doi"]:
            cur["doi"] = r["doi"]
        if not cur["year"] and r["year"]:
            cur["year"] = r["year"]
        if not cur["authors"] and r["authors"]:
            cur["authors"] = r["authors"]

    items = list(merged.values())
    items = _dedupe_titles(items)
    for r in items:
        r["score"] = round(
            0.55 * _overlap(r["title"], queries)
            + 0.30 * min(1.0, math.log10(1 + r["citations"]) / 3.0)
            + 0.15 * _recency(r["year"]), 4)
    items.sort(key=lambda r: (-r["score"], -r["citations"]))
    items = items[:limit]
    assign_keys(items)
    for i, r in enumerate(items, 1):
        r["rank"] = i
    return items, warnings


def _dedupe_titles(items):
    """Collapse works that share a normalized title and the same year."""
    groups = {}
    for r in items:
        groups.setdefault((norm_title(r["title"]), r["year"]), []).append(r)
    out = []
    for group in groups.values():
        best = max(group, key=lambda x: (len(x["abstract"]), x["citations"]))
        best["found_by"] = sorted({s for g in group for s in g.get("found_by", [])})
        best["citations"] = max(g["citations"] for g in group)
        out.append(best)
    return out


def assign_keys(items):
    """Stable, human-readable cite keys: firstauthor + year + title words."""
    used = {}
    for r in items:
        first = ""
        for a in r.get("authors") or []:
            family = a.split()[-1] if a.split() else ""
            cand = re.sub(r"[^a-z]", "", family.lower())
            if cand:
                first = cand
                break
        year = str(r.get("year") or "nd")
        words = [w for w in norm_title(r["title"]).split() if w not in STOP][:2]
        base = f"{first or 'anon'}{year}{''.join(words)}"
        base = re.sub(r"[^a-z0-9]", "", base)[:28] or "ref"
        n = used.get(base, 0) + 1
        used[base] = n
        r["cite_key"] = base + ("" if n == 1 else chr(ord("a") + n - 1))


def build_record(r) -> dict:
    """Public-facing subset stored in sources.json (saves context)."""
    return {
        "id": r["doi"] or (f"arxiv:{r['arxiv_id']}" if r.get("arxiv_id")
                           else "t:" + "-".join(norm_title(r["title"]).split()[:8])),
        "cite_key": r["cite_key"],
        "title": r["title"],
        "authors": r["authors"],
        "year": r["year"],
        "venue": r["venue"],
        "doi": r["doi"],
        "url": r["url"],
        "citations": r["citations"],
        "abstract": r["abstract"],
        "found_by": r.get("found_by", []),
        "score": r["score"],
        "rank": r["rank"],
    }
