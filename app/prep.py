"""Shared pipeline preamble: intake -> normalize -> supersede -> batch stats.

Used by both the recorder (app.record) and the live pipeline (app.pipeline) so the
record IDs, versions, drift flags and outlier statistics are computed identically.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .contracts import NormalizedRecord
from .stages.exceptions import outlier_bounds
from .stages.intake import run_intake
from .stages.normalize import Normalizer
from .store import JsonStore


@dataclass
class Prepared:
    active: list[NormalizedRecord]         # latest version of each id
    superseded: list[NormalizedRecord]     # older versions (SUPERSEDED_VERSION)
    med: float
    scale: float


def prepare(cfg: Config) -> Prepared:
    store = JsonStore(cfg.out_dir / "store" / "raw")
    raws = run_intake(cfg.seed_dir, store)

    by_id: dict[str, list] = {}
    for r in raws:
        by_id.setdefault(r.id, []).append(r)

    norm = Normalizer(cfg.schema_dir)
    active: list[NormalizedRecord] = []
    superseded: list[NormalizedRecord] = []
    for rid, group in sorted(by_id.items()):
        group.sort(key=lambda r: r.version)
        active.append(norm.normalize(group[-1]))
        superseded.extend(norm.normalize(g) for g in group[:-1])

    amounts = [n.amount for n in active if n.amount is not None]
    med, scale, _ = outlier_bounds(amounts)
    return Prepared(active=active, superseded=superseded, med=med, scale=scale)
