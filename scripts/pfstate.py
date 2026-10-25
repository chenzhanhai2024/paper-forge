"""Run state, paths, and small IO helpers. Standard library only."""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("PAPER_FORGE_HOME") or (Path.home() / ".paper-forge"))
RUNS = ROOT / "runs"

STAGES = [
    ("exported", ["export/main.tex"]),
    ("verified", ["audit.json"]),
    ("drafted", ["draft.md", "draft.tex"]),
    ("outlined", ["outline.md"]),
    ("evidenced", ["evidence.json"]),
    ("searched", ["sources.json"]),
]


def slugify(text: str, fallback: str = "paper") -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text or fallback)[:48]


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run_dir(pid: str) -> Path:
    return RUNS / pid


def create_run(title, idea, field="", paper_type="method", venue="",
               language="en", keywords=None):
    RUNS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    pid = f"{stamp}-{slugify(title)}"
    d = RUNS / pid
    d.mkdir(parents=True, exist_ok=False)
    spec = {
        "paper_id": pid,
        "title": title,
        "idea": idea,
        "field": field,
        "paper_type": paper_type,
        "venue": venue,
        "language": language,
        "keywords": keywords or [],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    write_json(d / "spec.json", spec)
    return spec


def all_runs():
    if not RUNS.exists():
        return []
    return sorted([p for p in RUNS.iterdir() if p.is_dir()], reverse=True)


def find_run(value: str) -> Path:
    if not value or value == "latest":
        runs = all_runs()
        if not runs:
            raise SystemExit("no runs found; run `paper.py init` first")
        return runs[0]
    direct = RUNS / value
    if direct.is_dir():
        return direct
    matches = [p for p in all_runs() if p.name.endswith(value) or value in p.name]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"run not found: {value}")
    raise SystemExit("ambiguous run: " + ", ".join(p.name for p in matches[:5]))


def stage_of(d: Path) -> str:
    for name, files in STAGES:
        if all((d / f).exists() for f in files):
            return name
    return "initialized"


def load_spec(d: Path) -> dict:
    return read_json(d / "spec.json", {}) or {}


def run_summary(d: Path) -> dict:
    spec = load_spec(d)
    src = read_json(d / "sources.json", {}) or {}
    ev = read_json(d / "evidence.json", {}) or {}
    au = read_json(d / "audit.json", {}) or {}
    return {
        "paper_id": d.name,
        "title": spec.get("title", ""),
        "stage": stage_of(d),
        "sources": len(src.get("sources", [])),
        "evidence_rows": len(ev.get("rows", [])),
        "audit": au.get("verdict", "-"),
        "path": str(d),
    }
