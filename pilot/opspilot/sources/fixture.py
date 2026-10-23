"""Snapshots enregistrés (JSON) : rejouer un incident sans cluster."""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Snapshot


def load(path: str | Path) -> Snapshot:
    return Snapshot.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def save(snap: Snapshot, path: str | Path) -> None:
    Path(path).write_text(snap.model_dump_json(indent=2), encoding="utf-8")
