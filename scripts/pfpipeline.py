"""Standalone pipeline: retrieval -> evidence -> outline -> draft -> verify -> export.

Every write step is LLM-driven, but the gates are deterministic: a section is
only accepted if its citations resolve and its numbers trace to an evidence
quote.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pfexport
import pfnet
import pfstate
import pfverify

QUERY_SYSTEM = """You design literature search queries for academic databases.
Return a JSON array of 6-10 short keyword queries (2-5 words each, no punctuation).
Cover: the core phrase, older synonyms, the method family, the task setting, and a critical angle.
Queries must be in English even if the paper is not."""

EVIDENCE_SYSTEM = """You extract evidence rows from paper abstracts for a literature review.
Return a JSON object {"rows": [...]}. Each row has exactly these keys:
source_id, claim, method, dataset, metric, result, limitation, stance, quote.
Rules:
- source_id must be copied from the input record's id field. Never invent one.
- quote must be copied VERBATIM from that abstract and must contain the reported numbers, if any.
- claim must be your own paraphrase, never the quote.
- stance is one of supports, contradicts, background.
- If an abstract has no usable finding, omit that source entirely."""

GAP_SYSTEM = """You are a skeptical senior reviewer at a top venue.
Given a title, an idea, and an evidence table, propose exactly three candidate contributions.
For each, use this shape:
### C1 <short name>
Gap: what no cited work does
Claim: the single-sentence contribution
Evidence: cite keys that support the premise
Risk: why a reviewer might reject it
Be specific. No generic "we propose a framework" phrasing. Output markdown only."""

OUTLINE_SYSTEM = """You design paper outlines.
Start with a line `Contribution: <the chosen contribution, one sentence>`.
Then output one `## ` heading per section, in order, numbered, using the skeleton you are given.
Include `## Abstract` FIRST but do not write its text.
No other text, no bullet lists."""

SECTION_SYSTEM = """You write one section of an academic paper in LaTeX-flavored Markdown.
Hard rules:
- Cite only keys from the provided AVAILABLE KEYS list, using \\cite{key}.
- Never state a number that is absent from the evidence quotes. Use \\todo{author: measure X} instead.
- Never claim state-of-the-art or an improvement that is not measured in the evidence.
- Do not write the section heading. Do not add a References section.
- 250-450 words, plain declarative prose, no bullet lists unless the section is a setup list."""

ABSTRACT_SYSTEM = """You write conference-paper abstracts.
150-200 words, one paragraph, no citations, no numbers unless quoted from the evidence.
State the problem, the gap, what was done, and what it implies. No hype."""

REPAIR_SYSTEM = """You are a copy editor enforcing citation integrity.
You receive a draft and a list of audit failures.
Fix ONLY those failures: replace unsupported numbers with \\todo{author: ...},
remove or replace unresolved citation keys with keys from the available list,
and rewrite passages flagged as verbatim copies.
Keep everything else as close to the original as possible. Output the full revised markdown."""


# ------------------------------------------------------------------ evidence
def validate_evidence(rows, valid_ids):
    good, rejects = [], []
    for n, row in enumerate(rows, 1):
        sid = str(row.get("source_id") or "").strip()
        if sid not in valid_ids:
            rejects.append(f"row {n}: source_id not in sources.json -> {sid or '(empty)'}")
        elif not str(row.get("quote") or "").strip():
            rejects.append(f"row {n}: empty quote")
        elif not str(row.get("claim") or "").strip():
            rejects.append(f"row {n}: empty claim")
        else:
            good.append(row)
    return good, rejects


def extract_evidence(llm, spec, sources, max_sources=20, batch=5):
    usable = [s for s in sources if len(s.get("abstract") or "") > 200][:max_sources]
    if not usable:
        return [], ["no source has a usable abstract"]
    valid_ids = {s["id"] for s in sources}
    rows, notes = [], []
    for start in range(0, len(usable), batch):
        chunk = usable[start:start + batch]
        payload = [{"id": s["id"], "title": s["title"], "abstract": s["abstract"][:1400]}
                   for s in chunk]
        user = (f"Title: {spec.get('title')}\nIdea: {spec.get('idea')}\n"
                f"Field: {spec.get('field')}\n\nRecords:\n"
                + json.dumps(payload, ensure_ascii=False, indent=1))
        try:
            data = llm.json(EVIDENCE_SYSTEM, user, max_tokens=3000)
            got = data.get("rows") if isinstance(data, dict) else data
        except Exception as exc:
            notes.append(f"batch {start // batch + 1}: {type(exc).__name__}: {exc}")
            continue
        good, rejects = validate_evidence(got or [], valid_ids)
        rows.extend(good)
        notes.extend(rejects)
    return rows, notes


# ------------------------------------------------------------------- writing
def _evidence_lines(rows, by_id):
    out = []
    for r in rows:
        src = by_id.get(r["source_id"], {})
        key = src.get("cite_key", "?")
        parts = [f"[{key}] {r.get('claim', '')}"]
        if r.get("method"):
            parts.append(f"method: {r['method']}")
        if r.get("dataset"):
            parts.append(f"data: {r['dataset']}")
        if r.get("result"):
            parts.append(f"result: {r['result']}")
        if r.get("limitation"):
            parts.append(f"limit: {r['limitation']}")
        parts.append(f'quote: "{r.get("quote", "")}"')
        out.append(" | ".join(parts))
    return "\n".join(out)


def analyze_gaps(llm, spec, rows, sources):
    by_id = {s["id"]: s for s in sources}
    user = (f"Title: {spec.get('title')}\nIdea: {spec.get('idea')}\n"
            f"Field: {spec.get('field')}\nVenue: {spec.get('venue') or 'unspecified'}\n"
            f"Paper type: {spec.get('paper_type')}\n\nEvidence table:\n"
            + _evidence_lines(rows, by_id))
    return llm.chat(GAP_SYSTEM, user, temperature=0.4, max_tokens=1800)


SKELETONS = {
    "method": ["Abstract", "1 Introduction", "2 Related Work", "3 Method",
               "4 Experimental Setup", "5 Results", "6 Limitations", "7 Conclusion"],
    "survey": ["Abstract", "1 Introduction", "2 Scope and Method", "3 Taxonomy",
               "4 Theme A", "5 Theme B", "6 Open Problems", "7 Conclusion"],
    "empirical": ["Abstract", "1 Introduction", "2 Hypotheses", "3 Data",
                  "4 Design", "5 Analysis", "6 Threats to Validity", "7 Conclusion"],
    "theory": ["Abstract", "1 Introduction", "2 Problem Formulation", "3 Model",
               "4 Propositions", "5 Implications", "6 Conclusion"],
    "generic": ["Abstract", "1 Introduction", "2 Related Work", "3 Approach",
                "4 Evaluation", "5 Discussion", "6 Conclusion"],
}

SKELETONS_ZH = {
    "method": ["摘要", "1 引言", "2 相关工作", "3 方法",
               "4 实验设置", "5 结果", "6 局限", "7 结论"],
    "survey": ["摘要", "1 引言", "2 范围与方法", "3 分类体系",
               "4 主题一", "5 主题二", "6 开放问题", "7 结论"],
    "empirical": ["摘要", "1 引言", "2 研究假设", "3 数据",
                  "4 研究设计", "5 分析", "6 效度威胁", "7 结论"],
    "theory": ["摘要", "1 引言", "2 问题形式化", "3 模型",
               "4 命题", "5 含义", "6 结论"],
    "generic": ["摘要", "1 引言", "2 相关工作", "3 方法",
                "4 评估", "5 讨论", "6 结论"],
}


def make_outline(llm, spec, contribution, rows):
    table = SKELETONS_ZH if spec.get("language") == "zh" else SKELETONS
    skeleton = table.get(spec.get("paper_type"), table["generic"])
    user = (f"Title: {spec.get('title')}\nChosen contribution:\n{contribution}\n\n"
            f"Skeleton (use these exact headings):\n" + "\n".join(skeleton))
    return llm.chat(OUTLINE_SYSTEM, user, temperature=0.3, max_tokens=900)


def _sections(outline):
    heads = re.findall(r"^##\s+(.+)$", outline, re.M)
    return [h.strip() for h in heads]


def draft_paper(llm, spec, outline, rows, sources, progress=print):
    by_id = {s["id"]: s for s in sources}
    keys = []
    for r in rows:
        k = by_id.get(r["source_id"], {}).get("cite_key")
        if k and k not in keys:
            keys.append(k)
    evidence = _evidence_lines(rows, by_id)
    contribution = ""
    m = re.search(r"^Contribution:\s*(.+)$", outline, re.M)
    if m:
        contribution = m.group(1).strip()

    heads = _sections(outline)
    body, abstract = [], ""
    for head in heads:
        if head.lower() in ("abstract", "摘要"):
            continue
        progress(f"    drafting: {head}")
        user = (f"Paper title: {spec.get('title')}\n"
                f"Contribution: {contribution}\n"
                f"Section to write: {head}\n"
                f"Language: {'Chinese' if spec.get('language') == 'zh' else 'English'}\n"
                f"AVAILABLE KEYS: {', '.join(keys)}\n\n"
                f"Evidence table:\n{evidence}\n\n"
                f"Draft only this section's body text.")
        body.append((head, llm.chat(SECTION_SYSTEM, user, temperature=0.3,
                                    max_tokens=2200).strip()))
    if any(h.lower() in ("abstract", "摘要") for h in heads):
        progress("    drafting: Abstract")
        summary = "\n".join(f"- {h}: {t[:400]}" for h, t in body)
        user = (f"Title: {spec.get('title')}\nContribution: {contribution}\n\n"
                f"Section drafts:\n{summary}\n\nEvidence table:\n{evidence}")
        abstract = llm.chat(ABSTRACT_SYSTEM, user, temperature=0.3,
                            max_tokens=700).strip()

    lines = [f"# {spec.get('title')}", ""]
    if abstract:
        lines += ["## Abstract", "", abstract, ""]
    for head, text in body:
        lines += [f"## {head}", "", text, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- orchestrate
def plan(llm, spec, queries=None, limit=40, max_sources=20, progress=print):
    run = pfstate.run_dir(spec["paper_id"])
    progress(f"[1/4] query expansion  ({llm.describe()})")
    if not queries:
        data = llm.json(QUERY_SYSTEM,
                        f"Title: {spec['title']}\nIdea: {spec['idea']}\n"
                        f"Field: {spec.get('field')}\nType: {spec.get('paper_type')}")
        queries = [str(q) for q in (data if isinstance(data, list)
                                    else data.get("queries", []))][:10]
        if not queries:
            raise SystemExit("model returned no queries; pass --query manually")
    progress("      " + "; ".join(queries))

    progress(f"[2/4] retrieval  ({len(queries)} queries)")
    items, warnings = pfnet.collect(queries, limit=limit, year_from=None)
    records = [pfnet.build_record(r) for r in items]
    pfstate.write_json(run / "sources.json", {
        "paper_id": spec["paper_id"],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "queries": queries,
        "warnings": warnings,
        "stats": {"kept": len(records),
                  "with_abstract": sum(1 for r in records if len(r["abstract"]) > 200)},
        "sources": records,
    })
    for w in warnings:
        progress(f"      warn {w}")
    if not records:
        raise SystemExit("retrieval returned nothing; loosen the queries or drop --year-from")

    progress(f"[3/4] evidence extraction  ({len(records)} sources)")
    rows, notes = extract_evidence(llm, spec, records, max_sources=max_sources)
    if not rows:
        raise SystemExit("no evidence extracted. Check the model endpoint, or extract manually.")
    pfstate.write_json(run / "evidence.json", {
        "paper_id": spec["paper_id"], "rows": rows,
        "coverage": {"sources": len(records),
                     "with_evidence": len({r['source_id'] for r in rows})},
    })
    for n in notes:
        progress(f"      warn {n}")
    progress(f"      {len(rows)} rows kept, "
             f"{len({r['source_id'] for r in rows})} sources covered")

    progress("[4/4] gap analysis")
    gaps = analyze_gaps(llm, spec, rows, records)
    (run / "gaps.md").write_text(gaps, encoding="utf-8")
    return run, records, rows, gaps


def write(run, llm, contribution=None, templates="generic", repair=1,
          progress=print):
    spec = pfstate.load_spec(run)
    sources = (pfstate.read_json(run / "sources.json", {}) or {}).get("sources", [])
    rows = (pfstate.read_json(run / "evidence.json", {}) or {}).get("rows", [])
    if not rows:
        raise SystemExit("no evidence.json; run the plan step first")

    if not contribution:
        contribution = _pick_contribution(run / "gaps.md")
    progress(f"[1/5] outline  (using: {contribution[:80]})")
    outline = make_outline(llm, spec, contribution, rows)
    (run / "outline.md").write_text(outline, encoding="utf-8")

    progress("[2/5] drafting sections")
    draft = draft_paper(llm, spec, outline, rows, sources, progress=progress)
    (run / "draft.md").write_text(draft, encoding="utf-8")

    progress("[3/5] verification")
    result = pfverify.audit(run, draft_path=run / "draft.md")
    pfstate.write_json(run / "audit.json", result)
    pfverify.write_report(run, result)
    rounds = 0
    while result["verdict"] == "FAIL" and rounds < repair:
        rounds += 1
        progress(f"      repair round {rounds}/{repair}: {len(result['failures'])} failure(s)")
        fixed = llm.chat(REPAIR_SYSTEM,
                         "AVAILABLE KEYS: " + ", ".join(s["cite_key"] for s in sources)
                         + "\n\nFAILURES:\n" + "\n".join(result["failures"])
                         + "\n\nDRAFT:\n" + draft,
                         temperature=0.2, max_tokens=8000)
        draft = fixed.strip()
        (run / "draft.md").write_text(draft, encoding="utf-8")
        result = pfverify.audit(run, draft_path=run / "draft.md")
        pfstate.write_json(run / "audit.json", result)
        pfverify.write_report(run, result)

    progress(f"[4/5] audit: {result['verdict']}  "
             f"({result['citations']['resolved']}/{result['citations']['cited_keys']} DOI verified)")
    if result["verdict"] == "PASS":
        progress("[5/5] export")
        out, meta = pfexport.export(run, template=templates)
        progress(f"      {out}  ({meta['words']} words)")
    else:
        progress("[5/5] export skipped — fix audit_report.md items first")
    return result


def _pick_contribution(path):
    if not path.exists():
        raise SystemExit("no gaps.md; run the plan step first")
    text = path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"^###\s*", text, flags=re.M)[1:]
    if not blocks:
        raise SystemExit("gaps.md has no candidate contributions")
    first = blocks[0]
    lines = first.splitlines()
    name = lines[0].strip() if lines else "C1"
    gap = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("gap:")), "")
    claim = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("claim:")), "")
    return f"{name} — {gap} {claim}".strip()


def list_candidates(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    return [(b.splitlines()[0].strip() if b.splitlines() else f"C{i}")
            for i, b in enumerate(re.split(r"^###\s*", text, flags=re.M)[1:], 1)]


def candidate_text(path, index):
    text = Path(path).read_text(encoding="utf-8-sig")
    blocks = re.split(r"^###\s*", text, flags=re.M)[1:]
    if not blocks:
        raise SystemExit("gaps.md has no candidates")
    if not 1 <= index <= len(blocks):
        raise SystemExit(f"--contribution must be 1..{len(blocks)}")
    return blocks[index - 1].strip()
