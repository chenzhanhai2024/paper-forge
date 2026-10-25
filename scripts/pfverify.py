"""Verification layer: citation truth, numeric sourcing, verbatim reuse."""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime

import pfnet

NGRAM = 10
CJK_NGRAM = 12


# ---------------------------------------------------------------- bib parsing
def _read_braced(text, i):
    """Return (content, index after the closing brace) for a {...} group."""
    depth, start = 0, i
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
        i += 1
    return text[start + 1:], i


def parse_bib(text):
    entries, i = {}, 0
    while True:
        m = re.search(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text[i:])
        if not m:
            break
        key = m.group(2).strip()
        j = i + m.end()
        fields = {}
        while j < len(text):
            fm = re.match(r"\s*(\w+)\s*=\s*", text[j:])
            if not fm:
                if text[j:j + 1] == "}":
                    j += 1
                    break
                j += 1
                continue
            name = fm.group(1).lower()
            j += fm.end()
            if j < len(text) and text[j] == "{":
                val, j = _read_braced(text, j)
            elif j < len(text) and text[j] == '"':
                end = text.find('"', j + 1)
                end = len(text) if end < 0 else end
                val, j = text[j + 1:end], end + 1
            else:
                end = text.find(",", j)
                end = len(text) if end < 0 else end
                val, j = text[j:end], end
            fields[name] = " ".join(val.split())
            while j < len(text) and text[j] in " \t\r\n,":
                if text[j] == ",":
                    j += 1
                    break
                j += 1
        entries[key] = fields
        i = j
    return entries


# ------------------------------------------------------------------ extraction
def extract_cites(text):
    keys = []
    for group in re.findall(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]*)\}", text):
        keys.extend(k.strip() for k in group.split(",") if k.strip())
    for group in re.findall(r"\[@([^\]]+)\]", text):
        keys.extend(re.sub(r"[^A-Za-z0-9_\-]", " ", group).split())
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def strip_tex(text, is_tex=False):
    if is_tex:
        text = re.sub(r"(?<!\\)%.*", "", text)
    text = re.sub(r"^#{1,6}.*$", " ", text, flags=re.M)
    text = re.sub(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{[^}]*\}", " ", text)
    text = re.sub(r"\\ref\{[^}]*\}|\\label\{[^}]*\}|\\eqref\{[^}]*\}", " ", text)
    text = re.sub(r"\\todo\{[^{}]*\}", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\$\$.*?\$\$|\$[^$]*\$", " ", text, flags=re.S)
    return text


YEARS = re.compile(r"(?:19|20)\d{2}")
NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?\s*(?:%|percent|×|x\b|k\b|M\b|B\b)?")


def extract_numbers(text):
    out = []
    for m in NUMBER.finditer(text):
        raw = m.group(0).strip()
        if YEARS.fullmatch(raw):
            continue
        ctx = text[max(0, m.start() - 60):m.end() + 60].replace("\n", " ")
        blocking = ("." in raw) or ("%" in raw) or ("percent" in raw) or ("×" in raw) \
            or ("x" in raw) or ("M" in raw) or ("B" in raw) or ("k" in raw)
        if not blocking:
            try:
                blocking = abs(float(raw)) >= 11
            except ValueError:
                blocking = False
        out.append({"value": raw, "context": " ".join(ctx.split()), "blocking": blocking})
    return out


def _norm_num(s):
    return re.sub(r"[\s,]", "", s.lower())


def number_supported(value, quotes):
    target = _norm_num(value)
    bare = re.sub(r"[^\d.]", "", target)
    if not bare:
        return False
    for q in quotes:
        if target in _norm_num(q) or bare in _norm_num(q):
            return True
    return False


def _ngrams(text, n, cjk):
    if cjk:
        # CJK shingles only: a borrowed Latin term must not count as copied text.
        chars = re.sub(r"[^\u4e00-\u9fff]", "", text)
        return {chars[i:i + n] for i in range(max(0, len(chars) - n + 1))}
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def verbatim_hits(text, abstracts):
    """Return [(key, draft_snippet)] for copied passages.

    Chinese passages are compared as 12-character CJK sequences, Latin passages
    as 10-word sequences. Both run for every pair, so mixed-language drafts are
    checked against both Chinese and English abstracts without false matches on
    a shared technical term.
    """
    hits = []
    for key, abstract in abstracts.items():
        if not abstract or len(abstract) < 80:
            continue
        cjk_pool = _ngrams(abstract, CJK_NGRAM, True)
        word_pool = _ngrams(abstract, NGRAM, False)
        for sentence in re.split(r"(?<=[.!?。！？])\s+|\n{2,}", text):
            cjk_hit = cjk_pool & _ngrams(sentence, CJK_NGRAM, True)
            word_hit = word_pool & _ngrams(sentence, NGRAM, False)
            if cjk_hit or word_hit:
                hits.append({"key": key, "snippet": " ".join(sentence.split())[:180]})
                break
    return hits


# ----------------------------------------------------------------- DOI lookup
def _resolve_crossref(doi):
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    try:
        data = json.loads(pfnet._get(url, retries=1))
        titles = (data.get("message") or {}).get("title") or []
        return True, (titles[0] if titles else ""), ""
    except pfnet.SourceError as exc:
        msg = str(exc)
        if "404" in msg:
            return False, "", "not registered with Crossref"
        return None, "", msg
    except Exception as exc:
        return None, "", f"{type(exc).__name__}: {exc}"


def _resolve_datacite(doi):
    url = "https://api.datacite.org/dois/" + urllib.parse.quote(doi, safe="")
    try:
        data = json.loads(pfnet._get(url, retries=1))
        titles = ((data.get("data") or {}).get("attributes") or {}).get("titles") or []
        title = titles[0].get("title", "") if titles else ""
        return True, title, ""
    except pfnet.SourceError as exc:
        msg = str(exc)
        if "404" in msg:
            return False, "", "not registered with DataCite"
        return None, "", msg
    except Exception as exc:
        return None, "", f"{type(exc).__name__}: {exc}"


def resolve_doi(doi):
    """arXiv/Zenodo DOIs live in DataCite; everything else is checked at Crossref."""
    datacite_first = doi.startswith(("10.48550/", "10.5281/"))
    first, second = ((_resolve_datacite, _resolve_crossref) if datacite_first
                     else (_resolve_crossref, _resolve_datacite))
    ok, title, err = first(doi)
    if ok is True:
        return True, title, ""
    if ok is None:
        return None, "", err
    ok2, title2, err2 = second(doi)
    if ok2 is True:
        return True, title2, ""
    if ok2 is None:
        return None, "", err2
    return False, "", f"{err}; {err2}"


# --------------------------------------------------------------------- audit
def audit(run_dir, draft_path=None, bib_path=None, check_doi=True):
    from pathlib import Path
    run_dir = Path(run_dir)
    sources = (pfstate_read(run_dir / "sources.json") or {}).get("sources", [])
    evidence = (pfstate_read(run_dir / "evidence.json") or {}).get("rows", [])
    draft_path = Path(draft_path) if draft_path else _pick(None, run_dir)
    text = draft_path.read_text(encoding="utf-8-sig")
    bib_file = Path(bib_path) if bib_path else run_dir / "refs.bib"
    bib_text = bib_file.read_text(encoding="utf-8-sig") if bib_file.exists() else ""

    by_key = {s["cite_key"]: s for s in sources}
    bib = parse_bib(bib_text) if bib_text else {}
    cited = extract_cites(text)

    unresolved, unverifiable, mismatch, resolved = [], [], [], 0
    for key in cited:
        src = by_key.get(key)
        entry = bib.get(key)
        if not src and not entry:
            unresolved.append(key)
            continue
        doi = src.get("doi") if src else entry.get("doi", "")
        title = src.get("title") if src else entry.get("title", "")
        if check_doi and doi:
            ok, remote_title, err = resolve_doi(doi)
            if ok is True:
                resolved += 1
                if remote_title and pfnet.title_similarity(title, remote_title) < 0.72:
                    mismatch.append({"key": key, "bib_title": title,
                                     "remote_title": remote_title})
            elif ok is False:
                unverifiable.append({"key": key, "doi": doi, "error": err})
            else:
                unverifiable.append({"key": key, "doi": doi, "error": err, "transient": True})
        elif not doi:
            unverifiable.append({"key": key, "doi": "", "error": "no DOI on record"})
        else:
            resolved += 1

    cited_sources = [by_key[k] for k in cited if k in by_key]
    ids = {s["id"] for s in cited_sources}
    quotes = [r.get("quote", "") for r in evidence
              if r.get("source_id") in ids and r.get("quote")]
    if not ids:
        quotes = [r.get("quote", "") for r in evidence if r.get("quote")]

    is_tex = draft_path.suffix.lower() == ".tex"
    body_text = text
    if is_tex:
        m = re.search(r"\\begin\{document\}(.*?)(?:\\bibliography\{|\\end\{document\})",
                      text, re.S)
        if m:
            body_text = m.group(1)
    body = strip_tex(body_text, is_tex=is_tex)
    nums = extract_numbers(body)
    blocking, warnings = [], []
    for n in nums:
        if n["blocking"]:
            if not number_supported(n["value"], quotes):
                blocking.append(n)
        elif not number_supported(n["value"], quotes):
            warnings.append(n)

    abstracts = {s["cite_key"]: s.get("abstract", "") for s in cited_sources}
    verbatim = verbatim_hits(body, abstracts)
    unused = [k for k in bib if k not in set(cited)]

    failures = []
    if unresolved:
        failures.append(f"{len(unresolved)} citation key(s) not in bib/sources: {', '.join(unresolved[:6])}")
    hard_unver = [u for u in unverifiable if not u.get("transient")]
    if hard_unver:
        failures.append(f"{len(hard_unver)} DOI(s) unverifiable at Crossref")
    if mismatch:
        failures.append(f"{len(mismatch)} DOI(s) resolve to a different title")
    if blocking:
        failures.append(f"{len(blocking)} quantitative number(s) with no evidence quote")
    if verbatim:
        failures.append(f"{len(verbatim)} passage(s) reuse 10+ words from a cited abstract")

    result = {
        "paper_id": run_dir.name,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "draft": str(draft_path),
        "citations": {
            "cited_keys": len(cited), "bib_entries": len(bib),
            "resolved": resolved, "unresolved": unresolved,
            "unverified": unverifiable, "title_mismatch": mismatch,
            "unused_bib_entries": unused,
        },
        "numbers": {"checked": len(nums), "blocking": blocking, "warnings": warnings[:40]},
        "verbatim": verbatim,
        "failures": failures,
        "verdict": "FAIL" if failures else "PASS",
    }
    return result


def pfstate_read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _pick(draft_path, run_dir):
    if draft_path:
        from pathlib import Path
        return Path(draft_path)
    for name in ("draft.tex", "draft.md"):
        if (run_dir / name).exists():
            return run_dir / name
    raise SystemExit("no draft.tex or draft.md in run directory")


def write_report(run_dir, result):
    c, n = result["citations"], result["numbers"]
    lines = [
        f"# Audit report — {result['paper_id']}",
        "",
        f"**Verdict: {result['verdict']}**  ·  {result['generated_at']}",
        "",
        "## Citations",
        "",
        f"- cited keys: {c['cited_keys']}",
        f"- bib entries: {c['bib_entries']}",
        f"- DOI resolved: {c['resolved']}",
        f"- unresolved keys: {len(c['unresolved'])}",
        f"- unverifiable DOIs: {len(c['unverified'])}",
        f"- title mismatches: {len(c['title_mismatch'])}",
    ]
    for u in c["unresolved"]:
        lines.append(f"  - unresolved `{u}`")
    for u in c["unverified"]:
        tag = "timeout" if u.get("transient") else "bad"
        lines.append(f"  - unverifiable `{u['key']}` ({tag}): {u['error']}")
    for m in c["title_mismatch"]:
        lines.append(f"  - mismatch `{m['key']}`: draft says \"{m['bib_title'][:70]}\" / "
                     f"Crossref says \"{m['remote_title'][:70]}\"")
    lines += [
        "",
        "## Numbers",
        "",
        f"- checked: {n['checked']}  ·  blocking: {len(n['blocking'])}  ·  warnings: {len(n['warnings'])}",
    ]
    for b in n["blocking"][:20]:
        lines.append(f"  - `{b['value']}` no evidence quote — {b['context'][:110]}")
    for w in n["warnings"][:10]:
        lines.append(f"  - warn `{w['value']}` — {w['context'][:90]}")
    lines += ["", "## Verbatim reuse", ""]
    if result["verbatim"]:
        for v in result["verbatim"][:10]:
            lines.append(f"- from `{v['key']}`: {v['snippet']}")
    else:
        lines.append("- none detected")
    lines += ["", "## Failures", ""]
    lines += [f"- {f}" for f in result["failures"]] or ["- none"]
    lines.append("")
    (run_dir / "audit_report.md").write_text("\n".join(lines), encoding="utf-8")
