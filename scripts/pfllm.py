"""Minimal OpenAI-compatible chat client. Standard library only.

Works with OpenAI, DeepSeek, Moonshot, Qwen, Ollama (/v1), vLLM, and any
other endpoint that speaks the chat/completions protocol.

Environment:
  PAPER_FORGE_API_KEY   or OPENAI_API_KEY
  PAPER_FORGE_BASE_URL  or OPENAI_BASE_URL   (default https://api.openai.com/v1)
  PAPER_FORGE_MODEL                          (default gpt-4o-mini)
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


class LLMError(RuntimeError):
    pass


def load_env(*paths):
    """Tiny .env loader: KEY=VALUE lines, no dependency, no overrides.

    Default lookup order: current directory, the skill root, PAPER_FORGE_HOME.
    """
    if paths:
        candidates = [Path(p) for p in paths]
    else:
        home = Path(os.environ.get("PAPER_FORGE_HOME") or (Path.home() / ".paper-forge"))
        candidates = [Path.cwd() / ".env",
                      Path(__file__).resolve().parent.parent / ".env",
                      home / ".env"]
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def _extract_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.lstrip("json").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                continue
    raise LLMError("model did not return valid JSON: " + text[:200])


class LLM:
    def __init__(self, model=None, base_url=None, api_key=None, timeout=180):
        self.api_key = (api_key or os.environ.get("PAPER_FORGE_API_KEY")
                        or os.environ.get("OPENAI_API_KEY") or "")
        self.base_url = (base_url or os.environ.get("PAPER_FORGE_BASE_URL")
                         or os.environ.get("OPENAI_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")
        self.model = model or os.environ.get("PAPER_FORGE_MODEL") or "gpt-4o-mini"
        self.timeout = timeout
        is_local = any(h in self.base_url for h in ("127.0.0.1", "localhost", "0.0.0.0"))
        if not self.api_key and not is_local:
            raise LLMError(
                "no API key. set PAPER_FORGE_API_KEY (or OPENAI_API_KEY), "
                "and optionally PAPER_FORGE_BASE_URL / PAPER_FORGE_MODEL")

    def describe(self):
        key = "set" if self.api_key else "none (local endpoint)"
        return f"{self.model} @ {self.base_url}  key={key}"

    def chat(self, system, user, json_mode=False, temperature=0.2, max_tokens=4096):
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        msg = self._post(payload, json_mode)
        if msg is None and json_mode:
            payload.pop("response_format", None)
            payload["messages"][0]["content"] += "\nReturn only valid JSON, no prose."
            msg = self._post(payload, False)
        if msg is None:
            raise LLMError("empty response from model")
        return msg

    def _post(self, payload, json_mode):
        body = dict(payload)
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        raw = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = self.base_url + "/chat/completions"
        last = "unknown error"
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=raw, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:300]
                last = f"HTTP {exc.code}: {detail}"
                if exc.code == 400 and json_mode:
                    return None
                if exc.code in (429, 500, 502, 503):
                    time.sleep(2 * (attempt + 1))
                    continue
                break
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
                time.sleep(1.5 * (attempt + 1))
        raise LLMError(f"model call failed ({last})")

    def json(self, system, user, temperature=0.2, max_tokens=4096):
        return _extract_json(self.chat(system, user, json_mode=True,
                                       temperature=temperature, max_tokens=max_tokens))
