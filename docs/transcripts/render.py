"""Render a `claude -p --output-format stream-json --verbose` log as readable Markdown.

Nothing is summarised or rewritten: every assistant text block, every tool call with its
full input, and every tool result appear in order. Only the streaming partial-delta events
(`stream_event`), which duplicate the complete messages, are dropped. Very long tool
results are cut at MAX_RESULT chars with the cut marked explicitly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MAX_RESULT = 6000


def fence(text: str, lang: str = "") -> str:
    ticks = "````" if "```" in text else "```"
    return f"{ticks}{lang}\n{text.rstrip()}\n{ticks}\n"


def result_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if block.get("type") == "text":
            parts.append(block["text"])
        else:
            parts.append(f"[{block.get('type')} block]")
    return "\n".join(parts)


def render(src: Path, prompt: str, header: str) -> str:
    out = [header, "\n## User prompt (verbatim)\n\n", fence(prompt, "text")]
    turn = 0
    call = 0
    for line in src.read_text().splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            out.append(
                f"\n_Session init: model `{ev.get('model')}`, cwd `{ev.get('cwd')}`, "
                f"permission mode `{ev.get('permissionMode')}`, "
                f"tools: {', '.join(ev.get('tools', []))}_\n"
            )
        elif kind == "assistant":
            for block in ev["message"].get("content", []):
                if block["type"] == "text" and block["text"].strip():
                    turn += 1
                    out.append(f"\n### Agent [{turn}]\n\n{block['text'].strip()}\n")
                elif block["type"] == "tool_use":
                    call += 1
                    inp = block["input"]
                    if block["name"] == "Bash":
                        body = fence(inp.get("command", ""), "bash")
                    else:
                        body = fence(json.dumps(inp, indent=2), "json")
                    out.append(f"\n**Tool call #{call} — `{block['name']}`**\n\n{body}")
        elif kind == "user":
            content = ev["message"].get("content", [])
            if isinstance(content, str):
                continue
            for block in content:
                if block.get("type") != "tool_result":
                    continue
                text = result_text(block.get("content"))
                cut = ""
                if len(text) > MAX_RESULT:
                    extra = len(text) - MAX_RESULT
                    cut = f"\n[... {extra} more chars cut; full text in the .jsonl ...]"
                    text = text[:MAX_RESULT]
                label = "Tool result (error)" if block.get("is_error") else "Tool result"
                body = fence(text + cut)
                out.append(f"\n<details><summary>{label}</summary>\n\n{body}\n</details>\n")
        elif kind == "result":
            out.append(
                "\n## Session result\n\n"
                f"- subtype: `{ev.get('subtype')}`\n"
                f"- turns: {ev.get('num_turns')}\n"
                f"- duration: {round((ev.get('duration_ms') or 0) / 1000)} s\n"
                f"- reported cost: ${ev.get('total_cost_usd', 0):.2f}\n"
            )
    return "".join(out)


if __name__ == "__main__":
    # python render.py session-01.jsonl prompt.md session-01.md header.md
    src, prompt_file, dest, header_file = map(Path, sys.argv[1:5])
    dest.write_text(render(src, prompt_file.read_text(), header_file.read_text()))
