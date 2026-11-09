"""Curated collections (Coach's Picks and future editor-made shelves).

Each collection is a YAML file in backend/content/collections/<key>.yaml:

    title: Coach's Picks
    subtitle: Hand-picked to get you started   # optional
    items:                                      # exercise slugs, in display order
      - push-up
      - dumbbell-bench-press

The app loads these at startup. Unknown or draft slugs are simply skipped when a
page renders, so a pick never breaks a page; `content_sync.py check` reports them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict

import yaml

COLLECTIONS_DIR = Path(__file__).resolve().parent / "content" / "collections"
KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_KEYS = {"title", "subtitle", "items"}


class CurationError(Exception):
    pass


def load_collections(directory: Path = COLLECTIONS_DIR) -> Dict[str, dict]:
    collections: Dict[str, dict] = {}
    problems = []
    for path in sorted(Path(directory).glob("*.yaml")):
        key = path.stem
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            problems.append(f"{path.name}: invalid YAML ({e})")
            continue
        if not KEY_RE.match(key):
            problems.append(f"{path.name}: file name must be lowercase words joined by hyphens")
            continue
        if not isinstance(raw, dict):
            problems.append(f"{path.name}: must contain title and items")
            continue
        unknown = set(raw) - ALLOWED_KEYS
        if unknown:
            problems.append(f"{path.name}: unknown field(s) {sorted(unknown)}")
        title = raw.get("title")
        items = raw.get("items")
        if not isinstance(title, str) or not title.strip():
            problems.append(f"{path.name}: title must be non-empty text")
        if not isinstance(items, list) or not all(isinstance(i, str) and i for i in items):
            problems.append(f"{path.name}: items must be a list of exercise slugs")
            continue
        collections[key] = {
            "key": key,
            "title": (title or "").strip(),
            "subtitle": (raw.get("subtitle") or "").strip(),
            "items": list(dict.fromkeys(items)),  # de-duplicate, keep order
        }
    if problems:
        raise CurationError("\n".join(problems))
    return collections
