#!/usr/bin/env python3

"""Fail-closed outer router for Codex notifications.

This executable belongs directly in Codex's top-level ``notify`` setting so
child turns are rejected before SkyComputerUseClient and every local notifier.
"""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from codex_notify_common import CLASS_ROOT, classify_thread


DEFAULT_SKY_CLIENT = (
    Path.home()
    / ".codex/computer-use/Codex Computer Use.app/Contents/SharedSupport/"
    "SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient"
)


def downstream_command(sky_client: Path, sidebar: Path, payload: str) -> List[str]:
    previous_notify = json.dumps(
        [str(sidebar), "notify", "--classified-root"],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return [
        str(sky_client),
        "turn-ended",
        "--previous-notify",
        previous_notify,
        payload,
    ]


def forward(
    payload: str,
    sky_client: Path,
    sidebar: Path,
    environment: Optional[Mapping[str, str]] = None,
    received_ns: Optional[int] = None,
) -> int:
    env: Dict[str, str] = dict(os.environ if environment is None else environment)
    env["CODEX_NOTIFY_CLASSIFICATION"] = CLASS_ROOT
    env["CODEX_NOTIFY_RECEIVED_NS"] = str(time.time_ns() if received_ns is None else received_ns)
    try:
        result = subprocess.run(
            downstream_command(sky_client, sidebar, payload),
            env=env,
            check=False,
        )
    except OSError:
        return 1
    return result.returncode


def main(argv: Optional[List[str]] = None) -> int:
    received_ns = time.time_ns()
    parser = argparse.ArgumentParser()
    parser.add_argument("--sky-client", type=Path, default=DEFAULT_SKY_CLIENT)
    parser.add_argument(
        "--sidebar",
        type=Path,
        default=Path(__file__).with_name("codex-sidebar-bin.sh"),
    )
    parser.add_argument("--classify", action="store_true")
    parser.add_argument("payload")
    args = parser.parse_args(argv)

    try:
        notification = json.loads(args.payload)
    except (TypeError, ValueError):
        return 0
    if not isinstance(notification, dict):
        return 0

    notification_type = notification.get("type")
    if notification_type == "agent-turn-complete":
        classification = classify_thread(notification)
        if args.classify:
            print(classification)
            return 0
        if classification != CLASS_ROOT:
            return 0
    elif args.classify:
        print("passthrough")
        return 0

    # Non-completion events, including approval-requested, remain untouched.
    return forward(args.payload, args.sky_client, args.sidebar, received_ns=received_ns)


if __name__ == "__main__":
    raise SystemExit(main())
