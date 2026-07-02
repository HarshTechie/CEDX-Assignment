"""Shared deterministic clock factory."""
from __future__ import annotations

from datetime import datetime, time

from .audit_log import RunClock
from .config import Config


def base_clock(cfg: Config) -> RunClock:
    return RunClock(datetime.combine(cfg.pipeline_now, time(0, 0, 0)))
