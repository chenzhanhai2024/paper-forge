---
name: paper-forge
description: Draft papers from a title and idea using OpenAlex and Crossref, verified citations, and LaTeX export. Use for paper or survey drafting, reference checks, or standalone runs with your own API key.
---

# Paper Forge

Turn a title plus an idea into a submission-ready draft whose every citation and number is traceable. The scripts handle retrieval, validation, and export; you handle judgment and prose.

## Hard rules

These override any user request for "just generate it":

1. **Cite only what is in `sources.json`.** Never emit a citation key that did not come from this run. Your memory of a paper is not evidence.
2. **Never invent numbers.** Any quantitative result must come from an evidence quote, or be written as `\todo{author: fill measured value}`.
3. **Never fabricate experiments.** Produce a runnable experiment plan and code skeleton. Result tables get placeholder cells.
4. **Paraphrase, do not copy.** Never reuse a 10-word sequence from an abstract.
5. **One human checkpoint.** The user picks the contribution from the gap analysis before you draft. Do not skip it.

If a request requires breaking 1-3, say so and offer the closest compliant version.

## Workflow

Run from the skill directory. `python` must be 3.9+; no third-party packages.

### 1. Initialize

```bash
python scripts/paper.py init --title "..." --idea "..." --field "..." \
  --paper-type method|survey|empirical|theory --venue "..." --language en|zh
```

Writes `~/.paper-forge/runs/<id>/spec.json`. Set `PAPER_FORGE_HOME` to relocate runs.

### 2. Search

Generate 6-12 query variants yourself (see `references/workflow.md`), then:

```bash
python scripts/paper.py search --run latest \
  --query "main phrase" --query "synonym phrase" --query "task + method" \
  --year-from 2020 --limit 40
```

Writes `sources.json`. Read the printed warnings: a skipped source is normal (Semantic Scholar and arXiv rate-limit aggressively); OpenAlex is the primary index and already covers arXiv preprints.

### 3. Evidence table

Read the abstracts in `sources.json`, extract structured rows, and write `evidence.json` in the schema shown in `references/workflow.md`. Then validate:

```bash
python scripts/paper.py evidence --run latest --file evidence.json
```

The script rejects rows whose `source_id` is not in `sources.json` or whose `quote` is empty. Fix all rejects before continuing.

### 4. Gap analysis and checkpoint

Write 3 candidate contributions, each stating the gap, the claim, and which evidence rows support it. **Ask the user to choose.** Save the choice with:

```bash
python scripts/paper.py outline --run latest --file outline.md
```

`outline.md` starts with `Contribution: <the chosen one>` and then one `## ` heading per section.

### 5. Draft

Write `draft.md` in `runs/<id>/`. Structure per paper type is in `references/workflow.md`. Use citation keys from `sources.json` (`\cite{lewis2020rag}` style, keys are precomputed). Draft section by section, then run verify.

### 6. Verify, then export

```bash
python scripts/paper.py verify --run latest
```

Produces `audit_report.md` and `audit.json`. Fix every FAIL item and re-run until `PASS`. Do not export on FAIL.

```bash
python scripts/paper.py export --run latest --template generic
```

Writes `runs/<id>/export/` with `main.tex`, `refs.bib`, `figures/`, `metadata.json`.

## Standalone mode

The same scripts run the pipeline without an agent, using any OpenAI-compatible endpoint.

```powershell
$env:PAPER_FORGE_API_KEY="sk-..."
$env:PAPER_FORGE_BASE_URL="https://api.deepseek.com/v1"   # default https://api.openai.com/v1
$env:PAPER_FORGE_MODEL="deepseek-chat"                    # default gpt-4o-mini
$env:PAPER_FORGE_HOME="D:\papers"                         # default ~/.paper-forge
python scripts/paper.py doctor
```

`doctor` prints the resolved endpoint and does one round trip plus an OpenAlex ping. Fix it before running anything else.

```bash
python scripts/paper.py autopilot --title "..." --idea "..." --field "..." \
  --limit 40 --max-sources 20 --template generic --repair 1
```

`autopilot` takes candidate 1 automatically. To keep the human checkpoint, split it:

```bash
python scripts/paper.py plan --title "..." --idea "..." --limit 40 --max-sources 20
python scripts/paper.py write --run latest --contribution 2
```

`plan` stops after `gaps.md`; `write` outlines, drafts, audits, repairs once, and exports only on PASS. `--model` and `--base-url` override the environment per command. Keys come from `PAPER_FORGE_API_KEY` or `OPENAI_API_KEY`; a `.env` in the current directory is loaded first. Local endpoints (127.0.0.1, localhost) need no key.

## What each check means

| Check | Fails when | Fix |
|---|---|---|
| Citation resolved | `\cite` key missing from bib, or DOI not in Crossref, or title mismatch | Use a real source from `sources.json`, or remove the claim |
| Number sourced | A number in the draft appears in no evidence quote | Add the quote, or replace with `\todo{...}` |
| Verbatim overlap | 10-word sequence also appears in a cited abstract | Rewrite the sentence |
| Unused bib | A bib entry is never cited | Fine to ignore, listed as info |

## Output contract

Always tell the user: run path, source count, evidence row count, audit verdict, and the remaining `\todo` list. Never claim a draft is "ready to submit" — the author still owns experiments, figures, and the AI-use disclosure required by most venues.

Details, section prompts, and venue norms: `references/workflow.md`.
