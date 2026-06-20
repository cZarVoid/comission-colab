#!/usr/bin/env python3
"""Tail the seed output file and forward new result lines to a Discord webhook.

The webhook URL must be supplied with the DISCORD_WEBHOOK_URL environment
variable. This file intentionally does not contain any webhook secrets.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, List

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
USERNAME = os.environ.get("DISCORD_USERNAME", "Colab seed runner")
MESSAGE_PREFIX = os.environ.get("DISCORD_MESSAGE_PREFIX", "").strip()
POLL_SECONDS = float(os.environ.get("DISCORD_POLL_SECONDS", "1"))
BATCH_LINES = max(1, int(os.environ.get("DISCORD_BATCH_LINES", "10")))
BATCH_SECONDS = max(1.0, float(os.environ.get("DISCORD_BATCH_SECONDS", "5")))
MAX_CONTENT_LEN = 1900  # Keep below Discord's 2000-character message limit.


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


def send_lines(lines: Iterable[str]) -> None:
    if not WEBHOOK_URL:
        return

    for body in _chunk_body(lines):
        payload = {
            "content": _format_message(body),
            "username": USERNAME,
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            WEBHOOK_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        # Small retry loop. Discord can rate-limit webhooks.
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    response.read()
                break
            except urllib.error.HTTPError as exc:
                retry_after = exc.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else min(2 ** attempt, 10)
                print(f"Discord webhook HTTP {exc.code}; retrying in {wait:.1f}s", file=sys.stderr, flush=True)
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
        while True:
            line = handle.readline()
            now = time.monotonic()

            if line:
                if line.strip():
                    batch.append(line.rstrip("\n\r"))
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
