#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${INTELLIGENCE_PYTHON:-}" ]]; then
  PYTHON_BIN="$INTELLIGENCE_PYTHON"
elif [[ -x "$ROOT/intelligence/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/intelligence/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

exec "$PYTHON_BIN" - "$ROOT" "${PY_BUNDLE_MAX_MB:-500}" <<'PY'
from __future__ import annotations

import importlib.metadata as metadata
import math
import sys
from pathlib import Path

try:
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
except ImportError as exc:
    raise SystemExit("Python bundle check requires packaging in the installed runtime; run uv sync first") from exc

root = Path(sys.argv[1])
try:
    limit_mb = float(sys.argv[2])
except ValueError:
    raise SystemExit("PY_BUNDLE_MAX_MB must be a positive number") from None
if not math.isfinite(limit_mb) or limit_mb <= 0 or limit_mb > 500:
    raise SystemExit("PY_BUNDLE_MAX_MB must be finite, greater than zero, and at most 500 MB")

installed = {canonicalize_name(dist.metadata.get("Name", "")): dist for dist in metadata.distributions()}
requirements_file = root / "requirements.txt"
if not requirements_file.is_file():
    raise SystemExit(f"Python bundle check cannot find {requirements_file}")

roots = []
for raw in requirements_file.read_text().splitlines():
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        continue
    try:
        req = Requirement(raw)
    except Exception as exc:
        raise SystemExit(f"cannot parse runtime requirement {raw!r}: {exc}") from None
    if req.marker is None or req.marker.evaluate():
        roots.append((canonicalize_name(req.name), tuple(req.extras)))

closure: set[str] = set()
pending = list(roots)
seen_extras: dict[str, set[str]] = {}
while pending:
    name, requested_extras = pending.pop()
    dist = installed.get(name)
    if dist is None:
        raise SystemExit(f"missing runtime distribution {name!r}; install requirements.txt and rerun the bundle check")
    extras = seen_extras.setdefault(name, set())
    new_extras = set(requested_extras) - extras
    if name in closure and not new_extras:
        continue
    extras.update(requested_extras)
    closure.add(name)
    for raw in dist.requires or ():
        try:
            req = Requirement(raw)
        except Exception as exc:
            raise SystemExit(f"cannot parse dependency metadata for {name!r}: {raw!r} ({exc})") from None
        marker_extras = {"", *extras}
        if req.marker is not None and not any(req.marker.evaluate({"extra": extra}) for extra in marker_extras):
            continue
        dep = canonicalize_name(req.name)
        pending.append((dep, tuple(req.extras)))

files: set[Path] = set()
for name in sorted(closure):
    dist = installed[name]
    if dist.files is None:
        raise SystemExit(f"runtime distribution {name!r} has no installed file list; refusing to underestimate size")
    for entry in dist.files:
        path = Path(dist.locate_file(entry)).resolve()
        if not path.exists():
            raise SystemExit(f"runtime file listed by {name!r} is missing: {path}")
        if path.is_file():
            files.add(path)

source = root / "intelligence" / "src"
if not source.is_dir():
    raise SystemExit(f"Python bundle check cannot find service sources at {source}")
for path in source.rglob("*"):
    if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
        files.add(path.resolve())

total = sum(path.stat().st_size for path in files)
size_mb = total / 1_000_000
print(f"Python runtime bundle estimate: {size_mb:.1f} MB ({len(closure)} runtime distributions, {len(files)} files)")
print(f"Configured limit: {limit_mb:.1f} MB")
if size_mb > limit_mb:
    raise SystemExit(f"Python runtime bundle estimate exceeds {limit_mb:.1f} MB")
PY
