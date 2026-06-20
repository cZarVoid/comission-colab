#!/usr/bin/env python3
"""Tail the seed output file and forward matching result lines to Discord.

Configuration is supplied with environment variables so webhook secrets do not
need to be committed to GitHub.

Required:
  DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

Optional:
  DISCORD_OUTPUT_FILE=output.txt
  DISCORD_MESSAGE_PREFIX="Seed output"
  DISCORD_MIN_SIZE=7000000

When DISCORD_MIN_SIZE is set to a positive integer, only seed-result lines in
this form are forwarded when the final size is at least that value:

  <seed> at <x> <z> with <size>
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, List, Optional

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
# Keep the bridge's request shape close to the working notebook status sender.
# Discord/Cloudflare can reject Python's default urllib User-Agent from Colab.
USER_AGENT = os.environ.get("DISCORD_USER_AGENT", "colab-cuda-output-bridge/1.0")
USERNAME = os.environ.get("DISCORD_USERNAME", "").strip()
MESSAGE_PREFIX = os.environ.get("DISCORD_MESSAGE_PREFIX", "").strip()
POLL_SECONDS = float(os.environ.get("DISCORD_POLL_SECONDS", "1"))
BATCH_LINES = max(1, int(os.environ.get("DISCORD_BATCH_LINES", "10")))
BATCH_SECONDS = max(1.0, float(os.environ.get("DISCORD_BATCH_SECONDS", "5")))
MAX_CONTENT_LEN = 1900  # Keep below Discord's 2000-character message limit.

# The program prints human-readable lines to stdout:
#   <seed> at <x> <z> with <size>
# but writes raw space-separated lines to output.txt:
#   <seed> <x> <z> <size>
# The bridge tails output.txt, so the raw format must be accepted for min-size filtering.
STDOUT_RESULT_LINE_RE = re.compile(
    r"^\s*(?P<seed>-?\d+)\s+at\s+(?P<x>-?\d+)\s+(?P<z>-?\d+)\s+with\s+(?P<size>\d+)\s*$"
)
OUTPUT_FILE_RESULT_LINE_RE = re.compile(
    r"^\s*(?P<seed>-?\d+)\s+(?P<x>-?\d+)\s+(?P<z>-?\d+)\s+(?P<size>\d+)\s*$"
)


def _parse_positive_int_env(name: str, default: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        print(f"WARNING: ignoring invalid {name}={raw!r}; expected an integer.", file=sys.stderr, flush=True)
        return default


MIN_SIZE = _parse_positive_int_env("DISCORD_MIN_SIZE", 0)


def _parse_result_line(line: str) -> Optional[dict]:
    """Parse either stdout-style or output.txt-style seed result lines."""
    for pattern in (STDOUT_RESULT_LINE_RE, OUTPUT_FILE_RESULT_LINE_RE):
        match = pattern.match(line)
        if match:
            return {
                "seed": int(match.group("seed")),
                "x": int(match.group("x")),
                "z": int(match.group("z")),
                "size": int(match.group("size")),
            }
    return None


def _extract_result_size(line: str) -> Optional[int]:
    parsed = _parse_result_line(line)
    if not parsed:
        return None
    return int(parsed["size"])


def _format_result_line(line: str) -> str:
    parsed = _parse_result_line(line)
    if not parsed:
        return line.rstrip("\n\r")
    return f'{parsed["seed"]} at {parsed["x"]} {parsed["z"]} with {parsed["size"]}'


def _should_forward(line: str) -> bool:
    if not line.strip():
        return False
    if MIN_SIZE <= 0:
        return True
    size = _extract_result_size(line)
    return size is not None and size >= MIN_SIZE


def _chunk_body(lines: Iterable[str]) -> Iterable[str]:
    current: List[str] = []
    current_len = 0
    for raw_line in lines:
        line = raw_line.rstrip("\n\r")
        if not line.strip():
            continue
        line_len = len(line) + 1
        if current and current_len + line_len > MAX_CONTENT_LEN:
            yield "\n".join(current)
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        yield "\n".join(current)


def _format_message(body: str) -> str:
    if MESSAGE_PREFIX:
        candidate = f"{MESSAGE_PREFIX}\n```text\n{body}\n```"
    else:
        candidate = f"```text\n{body}\n```"
    if len(candidate) <= 2000:
        return candidate
    # Fallback for unusually long single lines.
    if MESSAGE_PREFIX:
        return f"{MESSAGE_PREFIX}\n{body[:1900]}"
    return body[:1990]


def _make_request(payload: dict) -> urllib.request.Request:
    data = json.dumps(payload).encode("utf-8")
    return urllib.request.Request(
        WEBHOOK_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        body = ""
    return body


def send_lines(lines: Iterable[str]) -> None:
    if not WEBHOOK_URL:
        return

    for body in _chunk_body(lines):
        payload = {"content": _format_message(body)}
        if USERNAME:
            payload["username"] = USERNAME

        # Small retry loop. Discord can rate-limit webhooks.
        for attempt in range(4):
            try:
                with urllib.request.urlopen(_make_request(payload), timeout=15) as response:
                    response.read()
                break
            except urllib.error.HTTPError as exc:
                error_body = _read_error_body(exc)
                retry_after = exc.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else min(2 ** attempt, 10)
                detail = f"Discord webhook HTTP {exc.code}"
                if error_body:
                    detail += f": {error_body}"
                print(f"{detail}; retrying in {wait:.1f}s", file=sys.stderr, flush=True)
                time.sleep(wait)
            except Exception as exc:  # Keep the search running even if Discord fails.
                wait = min(2 ** attempt, 10)
                print(f"Discord webhook error: {exc}; retrying in {wait:.1f}s", file=sys.stderr, flush=True)
                time.sleep(wait)


def follow_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, os.SEEK_END)  # Only send lines produced after this bridge starts.
        batch: List[str] = []
        last_send = time.monotonic()

        print(f"Discord bridge watching {path}", flush=True)
        if MIN_SIZE > 0:
            print(f"Discord bridge filtering seed results with size >= {MIN_SIZE}", flush=True)
            print("Discord bridge accepts both 'seed x z size' and 'seed at x z with size' formats", flush=True)
        else:
            print("Discord bridge minimum size filter disabled", flush=True)

        while True:
            line = handle.readline()
            now = time.monotonic()

            if line:
                clean_line = line.rstrip("\n\r")
                if _should_forward(clean_line):
                    batch.append(_format_result_line(clean_line))
                if len(batch) >= BATCH_LINES:
                    send_lines(batch)
                    batch.clear()
                    last_send = now
                continue

            if batch and now - last_send >= BATCH_SECONDS:
                send_lines(batch)
                batch.clear()
                last_send = now

            time.sleep(POLL_SECONDS)


def main() -> int:
    if not WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL is not set; Discord bridge disabled.", file=sys.stderr, flush=True)
        return 0

    output_path = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DISCORD_OUTPUT_FILE", "output.txt"))
    follow_file(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
