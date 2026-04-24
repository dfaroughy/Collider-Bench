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

# Gemini streams assistant messages as successive `delta:true` chunks with no
# explicit end-of-message marker; we track whether the previous render left
# the cursor mid-line so we can flush a newline before any tool_use / result.
_MID_STREAM = False


def _flush_stream_newline() -> None:
    global _MID_STREAM
    if _MID_STREAM:
        print()
        _MID_STREAM = False


def render_line(line: str) -> None:
    """Render one stream-json line to stdout. Quiet on malformed input.

    Dispatches between Claude's content-block shape and Gemini's flat-string
    shape by detecting the event schema.
    """
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

    # Gemini CLI: {"type":"message","role":"assistant","content":"...","delta":true}
    # plus "init" / "tool_use" / "tool_result" / "result" events. No nested
    # content blocks; assistant messages arrive as many small delta chunks.
    global _MID_STREAM
    if msg_type == "message" and "role" in msg:
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                print(content, end="", flush=True)
                _MID_STREAM = True
                if not msg.get("delta"):
                    print()
                    _MID_STREAM = False
        return
    if msg_type == "tool_use":
        _flush_stream_newline()
        tool = msg.get("tool_name") or msg.get("name") or "?"
        args = msg.get("parameters") or msg.get("args") or msg.get("input") or {}
        if tool in ("run_shell_command", "Bash", "shell"):
            cmd = args.get("command", "")
            display = " ".join(ln.strip() for ln in str(cmd).splitlines() if ln.strip())
            print(f"  {GREEN}${RESET} {display[:200]}")
        elif tool in ("read_file", "Read"):
            path = args.get("absolute_path") or args.get("file_path") or args.get("path", "")
            print(f"  {BLUE}[Read]{RESET} {str(path).split('/')[-1]}")
        elif tool in ("write_file", "Write"):
            path = args.get("file_path") or args.get("path", "")
            print(f"  {BLUE}[Write]{RESET} {str(path).split('/')[-1]}")
        elif tool in ("replace", "edit", "Edit"):
            path = args.get("file_path") or args.get("path", "")
            print(f"  {YELLOW}[Edit]{RESET} {str(path).split('/')[-1]}")
        elif tool in ("list_directory", "LS"):
            path = args.get("dir_path") or args.get("path", "")
            print(f"  {DIM}[ls]{RESET} {path}")
        elif tool in ("search_file_content", "Grep", "grep"):
            pattern = args.get("pattern", "")
            print(f"  {DIM}[Grep]{RESET} {pattern}")
        else:
            print(f"  {DIM}[{tool}]{RESET}")
        sys.stdout.flush()
        return
    if msg_type == "tool_result":
        # Gemini delivers tool output to the model through a separate channel;
        # the stream event only carries status. Nothing useful to render.
        return
    if msg_type == "result" and "stats" in msg:
        _flush_stream_newline()
        stats = msg.get("stats") or {}
        duration = stats.get("duration_ms")
        tokens = stats.get("total_tokens")
        tools = stats.get("tool_calls")
        parts = []
        if duration is not None:
            parts.append(f"{duration/1000:.0f}s")
        if tools is not None:
            parts.append(f"{tools} tool calls")
        if tokens is not None:
            parts.append(f"{tokens} tokens")
        if parts:
            print(f"\n{BOLD}Done{RESET} ({', '.join(parts)})")
        return
    if msg_type == "init" and "session_id" in msg:
        # Quietly log the session bootstrap without crowding the screen.
        return

    # Codex CLI (--json):  thread.started / turn.started / item.started /
    # item.completed / turn.completed.  Only render item.completed (it carries
    # the final state + output of each tool/message).
    if msg_type == "item.completed":
        item = msg.get("item") or {}
        itype = item.get("type")
        if itype == "agent_message":
            text = item.get("text") or ""
            if text:
                print(text)
            return
        if itype == "command_execution":
            cmd = item.get("command") or ""
            # Strip the common `/usr/bin/bash -lc '...'` wrapper codex uses
            # to exec shell commands.
            prefix = "/usr/bin/bash -lc "
            if cmd.startswith(prefix):
                tail = cmd[len(prefix) :].strip()
                if (tail.startswith("'") and tail.endswith("'")) or (
                    tail.startswith('"') and tail.endswith('"')
                ):
                    tail = tail[1:-1]
                cmd = tail
            display = " ".join(ln.strip() for ln in cmd.splitlines() if ln.strip())
            exit_code = item.get("exit_code")
            marker = GREEN if exit_code == 0 else YELLOW
            print(f"  {marker}${RESET} {display[:200]}")
            return
        if itype == "file_change":
            for change in item.get("changes", []) or []:
                kind = change.get("kind", "?")
                path = str(change.get("path", "")).split("/")[-1]
                tag = {"add": "Write", "update": "Edit", "delete": "Delete"}.get(kind, kind)
                print(f"  {YELLOW}[{tag}]{RESET} {path}")
            return
        if itype == "reasoning":
            # Codex's hidden chain-of-thought; show dimmed so users can see
            # the model is thinking but it doesn't crowd the real output.
            text = (item.get("text") or "").strip()
            if text:
                print(f"  {DIM}{text.splitlines()[0][:160]}{RESET}")
            return
        return
    if msg_type == "turn.completed":
        usage = msg.get("usage") or {}
        parts = []
        toks = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        if toks:
            parts.append(f"{toks} tokens")
        if parts:
            print(f"{DIM}  ({', '.join(parts)}){RESET}")
        return
    if msg_type in ("thread.started", "turn.started", "item.started"):
        return

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
