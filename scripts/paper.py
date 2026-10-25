"""paper-forge CLI. Run: python scripts/paper.py <command> --help"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pfexport
import pfllm
import pfnet
import pfpipeline
import pfstate
import pfverify

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def ok(msg):
    print(f"  ok  {msg}")


def warn(msg):
    print(f" warn {msg}")


def bad(msg):
    print(f" fail {msg}")


# ------------------------------------------------------------------ commands
def cmd_init(args):
    spec = pfstate.create_run(
        title=args.title, idea=args.idea, field=args.field,
        paper_type=args.paper_type, venue=args.venue, language=args.language,
        keywords=[k.strip() for k in (args.keywords or "").split(",") if k.strip()])
    d = pfstate.run_dir(spec["paper_id"])
    print(f"run: {spec['paper_id']}")
    print(f"dir: {d}")
    (d / "sections").mkdir(exist_ok=True)
    print("\nnext: search --run latest --query ...")


def cmd_search(args):
    d = pfstate.find_run(args.run)
    queries = args.query or []
    if not queries:
        raise SystemExit("at least one --query is required")
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    sources = ["semanticscholar" if s == "s2" else s for s in sources]
    print(f"queries: {len(queries)}  sources: {', '.join(sources)}  limit: {args.limit}")
    items, warnings = pfnet.collect(
        queries, sources=sources, limit=args.limit, year_from=args.year_from,
        per_source=args.per_source, mailto=args.mailto)
    records = [pfnet.build_record(r) for r in items]
    with_abs = sum(1 for r in records if len(r["abstract"]) > 200)
    payload = {
        "paper_id": d.name,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "queries": queries,
        "warnings": warnings,
        "stats": {"kept": len(records), "with_abstract": with_abs},
        "sources": records,
    }
    pfstate.write_json(d / "sources.json", payload)
    for w in warnings:
        warn(w)
    print(f"\n{'#':>3} {'key':<24} {'yr':>4} {'cit':>6}  title")
    for r in records[:args.show]:
        print(f"{r['rank']:>3} {r['cite_key']:<24} {str(r['year'] or '-'):>4} "
              f"{r['citations']:>6}  {r['title'][:74]}")
    ok(f"{len(records)} sources kept, {with_abs} with usable abstracts -> sources.json")
    if with_abs < 10:
        warn("few abstracts; evidence extraction will be thin. add more queries or --year-from")


def _load_evidence(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return data["rows"] if isinstance(data, dict) and "rows" in data else data


def cmd_evidence(args):
    d = pfstate.find_run(args.run)
    sources = pfstate.read_json(d / "sources.json", {}) or {}
    valid_ids = {s["id"] for s in sources.get("sources", [])}
    rows = _load_evidence(args.file)
    good, rejects = pfpipeline.validate_evidence(rows, valid_ids)
    covered = {r["source_id"] for r in good}
    payload = {
        "paper_id": d.name,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rows": good,
        "coverage": {"sources": len(valid_ids), "with_evidence": len(covered)},
    }
    pfstate.write_json(d / "evidence.json", payload)
    for r in rejects:
        bad(r)
    ok(f"{len(good)} rows accepted, {len(rejects)} rejected, "
       f"{len(covered)}/{len(valid_ids)} sources covered")
    if rejects:
        raise SystemExit(1)


def cmd_outline(args):
    d = pfstate.find_run(args.run)
    text = Path(args.file).read_text(encoding="utf-8-sig")
    heads = re.findall(r"^##\s+(.+)$", text, re.M)
    if len(heads) < 3:
        warn(f"only {len(heads)} section heading(s) found; expected 5+")
    if not re.search(r"^Contribution:", text, re.M):
        warn("outline.md should start with a `Contribution:` line recording the user's choice")
    (d / "outline.md").write_text(text, encoding="utf-8")
    ok(f"saved outline.md with {len(heads)} sections: {', '.join(heads[:6])}")


def cmd_verify(args):
    d = pfstate.find_run(args.run)
    result = pfverify.audit(d, draft_path=args.file, bib_path=args.bib,
                            check_doi=not args.no_network)
    pfstate.write_json(d / "audit.json", result)
    pfverify.write_report(d, result)
    c, n = result["citations"], result["numbers"]
    print(f"citations : {c['cited_keys']} cited, {c['resolved']} DOI resolved, "
          f"{len(c['unresolved'])} unresolved, {len(c['unverified'])} unverifiable")
    print(f"numbers   : {n['checked']} checked, {len(n['blocking'])} unsourced (blocking), "
          f"{len(n['warnings'])} warnings")
    print(f"verbatim  : {len(result['verbatim'])} passage(s)")
    if result["failures"]:
        for f in result["failures"]:
            bad(f)
        print("\nreport: audit_report.md")
        raise SystemExit(1)
    ok("PASS — audit_report.md written")


def cmd_export(args):
    d = pfstate.find_run(args.run)
    audit = pfstate.read_json(d / "audit.json", {}) or {}
    if audit.get("verdict") == "FAIL" and not args.force:
        raise SystemExit("audit is FAIL; fix the draft or pass --force")
    out, meta = pfexport.export(d, template=args.template, template_file=args.template_file,
                                out_dir=args.out)
    ok(f"{meta['words']} words, {len(meta['citations_used'])} citations -> {out}")
    if meta["missing_keys"]:
        warn("keys with no source (bib entry skipped): " + ", ".join(meta["missing_keys"][:8]))


def _llm(args):
    pfllm.load_env()
    return pfllm.LLM(model=getattr(args, "model", None),
                     base_url=getattr(args, "base_url", None))


def _spec_from_args(args):
    if not args.title or not args.idea:
        raise SystemExit("need --title and --idea (or --run to continue an existing run)")
    return pfstate.create_run(
        title=args.title, idea=args.idea, field=args.field,
        paper_type=args.paper_type, venue=args.venue, language=args.language,
        keywords=[k.strip() for k in (args.keywords or "").split(",") if k.strip()])


def _print_candidates(run):
    names = pfpipeline.list_candidates(run / "gaps.md")
    print("\ncandidates:")
    for i, name in enumerate(names, 1):
        print(f"  {i}. {name}")
    print(f"full text: {run / 'gaps.md'}")


def cmd_plan(args):
    llm = _llm(args)
    if args.run:
        d = pfstate.find_run(args.run)
        spec = pfstate.load_spec(d)
    else:
        spec = _spec_from_args(args)
    print(f"run: {spec['paper_id']}")
    run, records, rows, gaps = pfpipeline.plan(
        llm, spec, queries=args.query, limit=args.limit,
        max_sources=args.max_sources)
    _print_candidates(run)
    print(f"\nnext: python scripts/paper.py write --run {run.name} --contribution 1")


def cmd_write(args):
    d = pfstate.find_run(args.run)
    llm = _llm(args)
    contribution = (pfpipeline.candidate_text(d / "gaps.md", args.contribution)
                    if args.contribution else None)
    result = pfpipeline.write(d, llm, contribution=contribution,
                              templates=args.template, repair=args.repair)
    print(f"\nrun: {d}")
    if result["verdict"] != "PASS":
        raise SystemExit(1)


def cmd_autopilot(args):
    llm = _llm(args)
    spec = _spec_from_args(args)
    print(f"run: {spec['paper_id']}\n")
    run, records, rows, gaps = pfpipeline.plan(
        llm, spec, queries=args.query, limit=args.limit,
        max_sources=args.max_sources)
    _print_candidates(run)
    print("\nautopilot: taking candidate 1\n")
    result = pfpipeline.write(run, llm,
                              contribution=pfpipeline.candidate_text(run / "gaps.md", 1),
                              templates=args.template, repair=args.repair)
    print(f"\nrun: {run}")
    if result["verdict"] != "PASS":
        raise SystemExit(1)


def cmd_doctor(args):
    import os
    pfllm.load_env()
    print(f"python   : {sys.version.split()[0]}")
    print(f"base_url : {os.environ.get('PAPER_FORGE_BASE_URL') or os.environ.get('OPENAI_BASE_URL') or 'https://api.openai.com/v1'}")
    print(f"model    : {os.environ.get('PAPER_FORGE_MODEL') or 'gpt-4o-mini'}")
    print(f"api key  : {'set' if (os.environ.get('PAPER_FORGE_API_KEY') or os.environ.get('OPENAI_API_KEY')) else 'MISSING'}")
    try:
        reply = pfllm.LLM(model=args.model, base_url=args.base_url).chat(
            "You are a health check.", "Reply with the single word OK.", max_tokens=8, temperature=0)
        ok(f"model replied: {reply.strip()[:40]}")
    except Exception as exc:
        bad(f"model: {exc}")
        print('  set: $env:PAPER_FORGE_API_KEY="sk-..."; $env:PAPER_FORGE_MODEL="deepseek-chat";'
              ' $env:PAPER_FORGE_BASE_URL="https://api.deepseek.com/v1"')
    try:
        pfnet._get("https://api.openalex.org/works?per-page=1&mailto=test@example.org")
        ok("OpenAlex reachable")
    except Exception as exc:
        bad(f"OpenAlex: {exc}")


def cmd_status(args):
    d = pfstate.find_run(args.run)
    spec = pfstate.load_spec(d)
    draft = d / "draft.md"
    todos = 0
    if draft.exists():
        todos = len(re.findall(r"\\todo\{", draft.read_text(encoding="utf-8-sig")))
    s = pfstate.run_summary(d)
    print(f"paper : {s['title']}")
    print(f"id    : {s['paper_id']}")
    print(f"type  : {spec.get('paper_type')}  venue: {spec.get('venue') or '-'}")
    print(f"stage : {s['stage']}")
    print(f"files : sources={s['sources']} evidence={s['evidence_rows']} "
          f"audit={s['audit']} todos={todos}")
    print(f"path  : {s['path']}")


def cmd_list(args):
    runs = pfstate.all_runs()
    if not runs:
        print("no runs yet")
        return
    for d in runs:
        s = pfstate.run_summary(d)
        print(f"{s['stage']:<12} {s['paper_id']:<48} {s['title'][:40]}")


# ---------------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(prog="paper-forge", description="Literature-grounded paper drafting")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("init", help="create a run")
    a.add_argument("--title", required=True)
    a.add_argument("--idea", required=True)
    a.add_argument("--field", default="")
    a.add_argument("--paper-type", default="method",
                   choices=["method", "survey", "empirical", "theory", "generic"])
    a.add_argument("--venue", default="")
    a.add_argument("--language", default="en", choices=["en", "zh"])
    a.add_argument("--keywords", default="")
    a.set_defaults(func=cmd_init)

    b = sub.add_parser("search", help="search OpenAlex/Crossref/S2/arXiv")
    b.add_argument("--run", default="latest")
    b.add_argument("--query", action="append", help="repeatable query variant")
    b.add_argument("--year-from", type=int, default=None)
    b.add_argument("--limit", type=int, default=40)
    b.add_argument("--per-source", type=int, default=25)
    b.add_argument("--sources", default="openalex,crossref")
    b.add_argument("--mailto", default="research@example.org")
    b.add_argument("--show", type=int, default=15)
    b.set_defaults(func=cmd_search)

    c = sub.add_parser("evidence", help="validate evidence.json")
    c.add_argument("--run", default="latest")
    c.add_argument("--file", required=True)
    c.set_defaults(func=cmd_evidence)

    e = sub.add_parser("outline", help="save the chosen outline")
    e.add_argument("--run", default="latest")
    e.add_argument("--file", required=True)
    e.set_defaults(func=cmd_outline)

    f = sub.add_parser("verify", help="audit citations, numbers, verbatim reuse")
    f.add_argument("--run", default="latest")
    f.add_argument("--file", default=None, help="draft path (default: draft.tex then draft.md)")
    f.add_argument("--bib", default=None)
    f.add_argument("--no-network", action="store_true", help="skip Crossref DOI resolution")
    f.set_defaults(func=cmd_verify)

    g = sub.add_parser("export", help="build LaTeX + BibTeX")
    g.add_argument("--run", default="latest")
    g.add_argument("--template", default="generic")
    g.add_argument("--template-file", default=None)
    g.add_argument("--out", default=None)
    g.add_argument("--force", action="store_true")
    g.set_defaults(func=cmd_export)

    h = sub.add_parser("status", help="show run state")
    h.add_argument("--run", default="latest")
    h.set_defaults(func=cmd_status)

    def llm_opts(parser):
        parser.add_argument("--model", default=None, help="override PAPER_FORGE_MODEL")
        parser.add_argument("--base-url", default=None, help="override PAPER_FORGE_BASE_URL")

    def paper_opts(parser):
        parser.add_argument("--title", default=None)
        parser.add_argument("--idea", default=None)
        parser.add_argument("--field", default="")
        parser.add_argument("--paper-type", default="method",
                            choices=["method", "survey", "empirical", "theory", "generic"])
        parser.add_argument("--venue", default="")
        parser.add_argument("--language", default="en", choices=["en", "zh"])
        parser.add_argument("--keywords", default="")

    k = sub.add_parser("plan", help="standalone: queries -> search -> evidence -> gap analysis")
    k.add_argument("--run", default=None, help="continue an existing run instead of creating one")
    paper_opts(k)
    k.add_argument("--query", action="append", help="skip LLM query expansion with explicit queries")
    k.add_argument("--limit", type=int, default=40)
    k.add_argument("--max-sources", type=int, default=20)
    llm_opts(k)
    k.set_defaults(func=cmd_plan)

    w = sub.add_parser("write", help="standalone: outline -> draft -> verify -> export")
    w.add_argument("--run", default="latest")
    w.add_argument("--contribution", type=int, default=None, help="candidate index from gaps.md")
    w.add_argument("--template", default="generic")
    w.add_argument("--repair", type=int, default=1, help="LLM repair rounds on audit failure")
    llm_opts(w)
    w.set_defaults(func=cmd_write)

    au = sub.add_parser("autopilot", help="standalone: plan + write in one shot")
    paper_opts(au)
    au.add_argument("--query", action="append")
    au.add_argument("--limit", type=int, default=40)
    au.add_argument("--max-sources", type=int, default=20)
    au.add_argument("--template", default="generic")
    au.add_argument("--repair", type=int, default=1)
    llm_opts(au)
    au.set_defaults(func=cmd_autopilot)

    d_ = sub.add_parser("doctor", help="check API key, model endpoint, and database reachability")
    llm_opts(d_)
    d_.set_defaults(func=cmd_doctor)

    i = sub.add_parser("list", help="list runs")
    i.set_defaults(func=cmd_list)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
