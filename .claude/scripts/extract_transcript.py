"""Condense a Claude Code session transcript (JSONL) into readable Markdown for the context agents.

Keeps: user prompts (incl. pasted content and earlier-compaction summaries), assistant replies,
one line per tool call, and tool errors. Drops: tool output, thinking, API/metadata records,
sub-agent side-chains, system reminders. Secrets and e-mail addresses are redacted.

Usage: python .claude/scripts/extract_transcript.py [transcript.jsonl] [--out FILE] [--max-chars N]
With no path, uses the newest transcript of the current project (~/.claude/projects/<slug>/).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SECRETS = [
    (re.compile(r"AIza[0-9A-Za-z_-]{35}"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"), "[REDACTED_ANTHROPIC_KEY]"),
    (re.compile(r"sk-[A-Za-z0-9]{32,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}"), "[REDACTED_TELEGRAM_BOT_TOKEN]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"(?i)\b((?:api[_-]?key|secret|token|password|passwd|pseudonym_secret)\s*[=:]\s*)(['\"]?)[^\s'\"]{6,}\2"), r"\1[REDACTED]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
]
NOISE = [re.compile(r"<(system-reminder|task-notification|local-command-caveat)>.*?</\1>", re.S)]


def redact(text: str) -> tuple[str, int]:
    n = 0
    for rx, rep in SECRETS:
        text, k = rx.subn(rep, text)
        n += k
    return text, n


def default_transcript() -> Path:
    slug = re.sub(r"[:\\/]", "-", str(Path.cwd()))
    folder = Path.home() / ".claude" / "projects" / slug
    files = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        sys.exit(f"No transcripts in {folder}")
    return files[0]


def tool_line(block: dict) -> str:
    inp = block.get("input") or {}
    hint = inp.get("description") or inp.get("file_path") or inp.get("pattern") or inp.get("command") or inp.get("prompt") or ""
    return f"- tool `{block.get('name')}`: {' '.join(str(hint).split())[:160]}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--out", default=".claude/handoff/00-transcript.md")
    ap.add_argument("--max-chars", type=int, default=6000, help="cap per message (long pastes are clipped)")
    a = ap.parse_args()
    src = Path(a.path) if a.path else default_transcript()
    out, redactions, turns = [f"# Condensed transcript\n\nSource: `{src.name}` (secrets and e-mails redacted)\n"], 0, 0
    for line in src.open(encoding="utf-8"):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") not in ("user", "assistant") or rec.get("isSidechain"):
            continue
        content = (rec.get("message") or {}).get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        texts, tools = [], []
        for b in blocks:
            if b.get("type") == "text":
                t = b.get("text", "")
                for rx in NOISE:
                    t = rx.sub("", t)
                if t.strip():
                    texts.append(t.strip())
            elif b.get("type") == "tool_use":
                tools.append(tool_line(b))
            elif b.get("type") == "tool_result" and b.get("is_error"):
                err = b.get("content")
                err = err if isinstance(err, str) else " ".join(x.get("text", "") for x in err or [] if isinstance(x, dict))
                tools.append(f"- tool error: {' '.join(err.split())[:240]}")
        if not texts and not tools:
            continue
        body = "\n\n".join(texts)
        if len(body) > a.max_chars:
            body = body[: a.max_chars] + f"\n[... clipped {len(body) - a.max_chars} chars]"
        chunk = (f"\n### {rec['type'].upper()} · {rec.get('timestamp', '')[:19]}\n\n" + body + ("\n" if body else "") + "\n".join(tools)).rstrip() + "\n"
        chunk, n = redact(chunk)
        redactions += n
        turns += 1
        out.append(chunk)
    out.append(f"\n---\n{turns} turns kept · {redactions} redactions\n")
    dest = Path(a.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(out), encoding="utf-8")
    print(f"wrote {dest} · {turns} turns · {redactions} redactions · {dest.stat().st_size // 1024} KB (from {src.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
