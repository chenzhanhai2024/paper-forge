"""Export: Markdown draft to LaTeX + BibTeX built from verified sources."""
from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

import pfverify

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "latex"

PLACEHOLDER = "\x00PF{}\x00"


def _protect(text):
    """Hide math and LaTeX commands from escaping."""
    stash = []

    def keep(m):
        stash.append(m.group(0))
        return PLACEHOLDER.format(len(stash) - 1)

    pattern = re.compile(
        r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
        r"|\$\$.*?\$\$|\$[^$\n]+\$", re.S)
    return pattern.sub(keep, text), stash


def _restore(text, stash):
    for i, val in enumerate(stash):
        text = text.replace(PLACEHOLDER.format(i), val)
    return text


def escape_latex(text):
    text, stash = _protect(text)
    for a, b in (("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"),
                 ("$", r"\$"), ("#", r"\#"), ("_", r"\_"), ("{", r"\{"),
                 ("}", r"\}"), ("~", r"\textasciitilde{}"),
                 ("^", r"\textasciicircum{}")):
        text = text.replace(a, b)
    return _restore(text, stash)


def inline(text):
    text = escape_latex(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\\emph{\1}", text)
    text = re.sub(r"`([^`]+)`", r"\\texttt{\1}", text)
    return text


def _table(rows):
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    cells = [r for r in cells if not all(re.fullmatch(r":?-{2,}:?", c or "-") for c in r)]
    if not cells:
        return ""
    width = max(len(r) for r in cells)
    cells = [r + [""] * (width - len(r)) for r in cells]
    spec = "l" * width
    lines = [r"{\small", rf"\begin{{tabular}}{{{spec}}}", r"\toprule",
             " & ".join(inline(c) for c in cells[0]) + r" \\", r"\midrule"]
    for r in cells[1:]:
        lines.append(" & ".join(inline(c) for c in r) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}}", ""]
    return "\n".join(lines)


def md_to_latex_body(md, figure_dir, out_figures):
    lines = md.splitlines()
    out, buf = [], []
    in_abstract = False
    i = 0

    def flush():
        if buf:
            out.append(" ".join(buf).strip() + "\n")
            buf.clear()

    def close_abstract():
        nonlocal in_abstract
        if in_abstract:
            out.append(r"\end{abstract}" + "\n")
            in_abstract = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush()
            i += 1
            continue

        h = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if h:
            flush()
            close_abstract()
            level, title = len(h.group(1)), h.group(2).strip()
            low = title.lower().strip(" 0123456789.")
            if level == 1:
                i += 1
                continue
            if low in ("abstract", "摘要"):
                out.append(r"\begin{abstract}" + "\n")
                in_abstract = True
                i += 1
                continue
            cmd = {2: "section", 3: "subsection", 4: "subsubsection"}[min(level, 4)]
            title = re.sub(r"^\d+(\.\d+)*\.?\s*", "", title)
            out.append(f"\\{cmd}{{{inline(title)}}}\n")
            i += 1
            continue

        img = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", stripped)
        if img:
            flush()
            caption, src = img.group(1), img.group(2)
            src_path = (figure_dir / src).resolve()
            fig_name = src_path.name
            if src_path.exists():
                out_figures.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, out_figures / fig_name)
            out.append(
                "\\begin{figure}[htbp]\n\\centering\n"
                f"\\includegraphics[width=0.9\\linewidth]{{figures/{fig_name}}}\n"
                f"\\caption{{{inline(caption)}}}\n\\end{{figure}}\n")
            i += 1
            continue

        if stripped.startswith("|"):
            flush()
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(_table(block))
            continue

        if re.match(r"^[-*+]\s+", stripped):
            flush()
            items = []
            while i < len(lines) and re.match(r"^[-*+]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*+]\s+", "", lines[i].strip()))
                i += 1
            out.append("\\begin{itemize}\n" + "".join(
                f"  \\item {inline(x)}\n" for x in items) + "\\end{itemize}\n")
            continue

        if re.match(r"^\d+[.)]\s+", stripped):
            flush()
            items = []
            while i < len(lines) and re.match(r"^\d+[.)]\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+[.)]\s+", "", lines[i].strip()))
                i += 1
            out.append("\\begin{enumerate}\n" + "".join(
                f"  \\item {inline(x)}\n" for x in items) + "\\end{enumerate}\n")
            continue

        buf.append(inline(stripped))
        i += 1

    flush()
    close_abstract()
    body = "\n".join(out)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip() + "\n"


def bib_escape(text):
    for a, b in (("&", r"\&"), ("%", r"\%"), ("_", r"\_"), ("#", r"\#")):
        text = text.replace(a, b)
    return text


def bib_entry(src):
    authors = src.get("authors") or []
    author = " and ".join(authors) if authors else "Unknown"
    fields = [
        ("author", author),
        ("title", bib_escape(src.get("title", ""))),
        ("year", str(src.get("year") or "n.d.")),
    ]
    venue = src.get("venue") or ""
    kind = "article"
    if venue:
        fields.append(("journal", bib_escape(venue)))
    else:
        kind = "misc"
        fields.append(("howpublished", "Preprint"))
    if src.get("doi"):
        fields.append(("doi", src["doi"]))
        fields.append(("url", "https://doi.org/" + src["doi"]))
    elif src.get("url"):
        fields.append(("url", src["url"]))
    body = ",\n  ".join(f"{k} = {{{v}}}" for k, v in fields)
    return f"@{kind}{{{src['cite_key']},\n  {body}\n}}\n"


def build_bib(sources, cited_keys):
    by_key = {s["cite_key"]: s for s in sources}
    return "\n".join(bib_entry(by_key[k]) for k in cited_keys if k in by_key)


def export(run_dir, template="generic", template_file=None, out_dir=None):
    run_dir = Path(run_dir)
    spec = {}
    try:
        import json
        spec = json.loads((run_dir / "spec.json").read_text(encoding="utf-8-sig"))
    except Exception:
        pass
    sources = []
    try:
        import json
        sources = json.loads((run_dir / "sources.json").read_text(encoding="utf-8-sig")).get("sources", [])
    except Exception:
        pass

    draft_md = run_dir / "draft.md"
    draft_tex = run_dir / "draft.tex"
    if not draft_md.exists() and not draft_tex.exists():
        raise SystemExit("no draft.md or draft.tex to export")

    out = Path(out_dir) if out_dir else run_dir / "export"
    out.mkdir(parents=True, exist_ok=True)
    out_figures = out / "figures"

    if draft_md.exists():
        text = draft_md.read_text(encoding="utf-8-sig")
        body = md_to_latex_body(text, run_dir, out_figures)
    else:
        text = draft_tex.read_text(encoding="utf-8-sig")
        body = text

    cited = [k for k in pfverify.extract_cites(text)]
    missing = [k for k in cited if k not in {s["cite_key"] for s in sources}]
    bib = build_bib(sources, cited)
    if not bib:
        bib = "% no citations found in draft\n"

    title = spec.get("title") or _first_h1(text) or "Untitled"
    template_path = Path(template_file) if template_file else ASSETS / f"{template}.tex"
    if not template_path.exists():
        raise SystemExit(f"template not found: {template_path}")
    tpl = template_path.read_text(encoding="utf-8-sig")

    keywords = spec.get("keywords") or []
    kw_block = (r"\noindent\textbf{Keywords:} " + escape_latex(", ".join(keywords))
                + r"\par\medskip" + "\n") if keywords else ""

    rendered = (tpl.replace("{{TITLE}}", escape_latex(title))
                .replace("{{AUTHORS}}", "Anonymous Authors")
                .replace("{{KEYWORDS_BLOCK}}", kw_block)
                .replace("{{BODY}}", body))

    (out / "main.tex").write_text(rendered, encoding="utf-8")
    (out / "refs.bib").write_text(bib, encoding="utf-8")
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", body))
    latin_words = len(re.findall(r"[A-Za-z0-9]+", body))
    meta = {
        "paper_id": run_dir.name,
        "title": title,
        "template": template,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sources_available": len(sources),
        "citations_used": cited,
        "missing_keys": missing,
        "words": latin_words + cjk_chars,
        "latin_words": latin_words,
        "cjk_chars": cjk_chars,
    }
    (out / "metadata.json").write_text(
        __import__("json").dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return out, meta


def _first_h1(text):
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else ""
