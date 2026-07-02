"""Entry point: `python -m app <command> [ID]`. The Makefile targets are thin wrappers
over these commands, so graders can invoke the same interface regardless of stack.
"""
from __future__ import annotations

import os
import sys

from .config import Config


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "demo"
    cfg = Config.from_env()

    if cmd == "demo":
        from .pipeline import run
        run(cfg)
        return 0
    if cmd == "record":
        from .record import main as rec_main
        rec_main()
        return 0
    if cmd == "trace":
        from .cli import cmd_trace
        return cmd_trace(cfg, os.environ.get("ID") or (argv[1] if len(argv) > 1 else ""))
    if cmd == "replay":
        from .cli import cmd_replay
        return cmd_replay(cfg, os.environ.get("ID") or (argv[1] if len(argv) > 1 else ""))
    if cmd == "eval":
        from .eval import main as eval_main
        return eval_main(cfg)

    probes = {
        "probe-approval": "probe_approval",
        "probe-agent-failure": "probe_agent_failure",
        "probe-budget": "probe_budget",
        "probe-append-only": "probe_append_only",
        "probe-idempotency": "probe_idempotency",
        "probe-crash": "probe_crash",
    }
    if cmd in probes:
        from . import probes as P
        return getattr(P, probes[cmd])(cfg)

    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
