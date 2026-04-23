#!/usr/bin/env python3
"""Render Claude CLI stream-json as a readable terminal display.

Usable two ways:
- As a library: `from agent_runtime.stream_display import render_line`
- As a script: pipe stream-json lines to stdin (`claude … | stream_display.py`)
"""

import json
import sys

BLUE = "\033[34m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def render_line(line: str) -> None:
    """Render one stream-json line to stdout. Quiet on malformed input."""
    line = line.strip()
    if not line:
        return
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return
    if not isinstance(msg, dict):
        return

    msg_type = msg.get("type")

    if msg_type == "assistant":
        content = msg.get("message", {}).get("content", [])
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                print(f"{block['text']}")
            elif block.get("type") == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {})
                if name == "Bash":
                    cmd = inp.get("command", "")
                    lines = [ln.strip() for ln in cmd.strip().split("\n") if ln.strip()]
                    display = " && ".join(lines)
                    display = display.replace(
                        "source /opt/cray/pe/lmod/lmod/init/bash && module load conda && conda activate cms_analysis && ",
                        "",
                    )
                    print(f"  {GREEN}${RESET} {display[:200]}")
                elif name in ("Read", "Write"):
                    path = inp.get("file_path", "")
                    short = path.split("/")[-1] if "/" in path else path
                    print(f"  {BLUE}[{name}]{RESET} {short}")
                elif name == "Edit":
                    path = inp.get("file_path", "")
                    short = path.split("/")[-1] if "/" in path else path
                    print(f"  {YELLOW}[Edit]{RESET} {short}")
                elif name == "Grep":
                    pattern = inp.get("pattern", "")
                    print(f"  {DIM}[Grep]{RESET} {pattern}")
                else:
                    print(f"  {DIM}[{name}]{RESET}")
        sys.stdout.flush()

    elif msg_type == "result":
        cost = msg.get("total_cost_usd") or msg.get("cost_usd")
        duration = msg.get("duration_ms")
        turns = msg.get("num_turns")
        parts = []
        if duration is not None:
            parts.append(f"{duration/1000:.0f}s")
        if turns is not None:
            parts.append(f"{turns} turns")
        if cost is not None:
            parts.append(f"${cost:.2f}")
        if parts:
            print(f"\n{BOLD}Done{RESET} ({', '.join(parts)})")


def main():
    for line in sys.stdin:
        render_line(line)


if __name__ == "__main__":
    main()
