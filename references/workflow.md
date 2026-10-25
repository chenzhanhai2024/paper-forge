# Paper Forge — stage details

Load this file when you need the exact prompt shapes, schemas, or section norms.
`<run>` is `~/.paper-forge/runs/<paper_id>`.

## 1. Query expansion

Produce 6-12 short keyword queries, not sentences. Cover four axes:

| Axis | Example for "RAG for legal QA" |
|---|---|
| Core phrase | `retrieval augmented generation` |
| Synonym / older term | `open-domain question answering retrieval` |
| Method family | `dense passage retrieval` |
| Task + setting | `legal question answering` |
| Evaluation | `RAG benchmark evaluation` |
| Critical view | `hallucination retrieval augmented generation` |

Rules: 2-5 words per query, no punctuation, English queries even for a Chinese paper (indexes are English-heavy). Pass each as a separate `--query`.

If a source warns 429, it is throttled, not broken. Keep going; OpenAlex alone is usually enough.

## 2. Evidence schema

Read abstracts from `sources.json` and write these rows. 1-4 rows per relevant source; skip irrelevant ones.

```json
[
  {
    "source_id": "<the id field from sources.json>",
    "claim": "one-sentence finding in your own words",
    "method": "technique used",
    "dataset": "dataset or setting, empty if none",
    "metric": "metric name, empty if none",
    "result": "reported outcome, empty if none",
    "limitation": "stated or implied limitation",
    "stance": "supports | contradicts | background",
    "quote": "verbatim sentence from the abstract that contains the result"
  }
]
```

Hard requirements enforced by `paper.py evidence`:

- `source_id` must exist in `sources.json`.
- `quote` must be copied verbatim from the abstract. It is the only thing that later authorizes a number in the draft.
- `claim` must be your paraphrase, never the quote itself.

Never fill a field from memory. If the abstract is missing or uninformative, skip the source.

## 3. Gap analysis and the checkpoint

Write three candidate contributions in this shape:

```
### C1 <name>
Gap: what no cited work does
Claim: the one-sentence contribution
Evidence: <cite_key> rows that support the premise
Risk: why a reviewer might reject it
```

Then ask the user to pick one. Do not proceed on an assumed choice.

`outline.md` format:

```
Contribution: C2 — <text>

## Abstract
## 1 Introduction
## 2 Related Work
...
```

## 4. Section skeletons

| Type | Sections |
|---|---|
| method | Intro, Related Work, Method, Experimental Setup, Results, Discussion, Limitations, Conclusion |
| survey | Intro, Scope and Method, Taxonomy, Theme sections x3, Open Problems, Conclusion |
| empirical | Intro, Hypotheses, Data, Design, Analysis, Threats to Validity, Conclusion |
| theory | Intro, Problem Formulation, Model, Propositions, Implications, Conclusion |

## 5. Drafting rules

- **Related Work**: one paragraph per cluster, 2-5 citations each, and every paragraph ends by stating how your claim differs. No "many works have studied X" filler.
- **Method**: define notation once, then reuse. Every design choice must trace to a cited limitation in the evidence table.
- **Experimental Setup**: datasets, baselines, metrics, and implementation details, all sourced from evidence rows or marked `\todo{author: ...}`.
- **Results**: tables may contain `\todo{author: measured value}` cells. Prose must not state an outcome that has no evidence quote.
- **Limitations**: at least three concrete limitations. Reviewers read this section first.
- **Abstract**: 150-200 words, no citations, no numbers unless sourced, no "state-of-the-art" claims without measurement.
- **Citations**: keys come from `sources.json`; write `\cite{lewis2020rag}` or `\cite{a,b}`.
- **Language**: if `spec.language` is `zh`, write the draft in Chinese but keep cite keys unchanged.

## 6. Revision loop

`verify` fails with five possible verdicts. Map each to an action:

| Failure | Action |
|---|---|
| unresolved key | replace with a key from `sources.json`, or delete the sentence |
| DOI unverifiable | drop the source; its metadata is untrustworthy |
| title mismatch | a fabricated reference; replace it |
| unsourced number | add the quote to `evidence.json`, or convert to `\todo{author: ...}` |
| verbatim passage | rewrite the sentence; do not reorder words |

Re-run `verify` after every fix. The loop ends only at PASS.

## 7. Venue and ethics

- Confirm the page limit and reference style from the venue's own author guide; do not guess. `--template-file` accepts a custom `.tex`.
- Most venues now require an AI-use disclosure. Add this to the draft's Acknowledgements when the venue asks:

  > Generative AI was used to assist with literature retrieval, drafting, and reference checking. All citations were verified against Crossref, and the authors reviewed and edited all content and take full responsibility for it.

- Never describe `\todo{}` cells as results. List them explicitly in the handoff message.

## 8. Chinese output

`--template generic-zh` uses `ctexart`. Compile with:

```bash
xelatex -interaction=nonstopmode main.tex && bibtex main && xelatex main.tex
```

English output compiles with `pdflatex`. If no LaTeX is installed, the `.tex` and `.bib` files are still the deliverable.
