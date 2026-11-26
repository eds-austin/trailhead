#!/usr/bin/env python3
"""Generate python3-deps.json for the Flatpak build from the uv lock file.

Flatpak builds run offline, so every dependency is listed as a pinned wheel (URL + sha256) from PyPI.
PySide6 / shiboken6 are provided by the io.qt.PySide.BaseApp base and are skipped.

Usage (from the repo root):  uv run python packaging/flatpak/gen_python_deps.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

PY = (3, 13)  # org.kde.Platform 6.11 / freedesktop 25.08 ships Python 3.13
ARCHES = {"x86_64": "x86_64", "aarch64": "aarch64"}
SKIP = {"pyside6", "pyside6-essentials", "pyside6-addons", "pyside6-pdf", "pyside6-webengine", "shiboken6"}
# Needed to build/install the app itself inside the sandbox (hatchling backend).
BUILD_DEPS = ["hatchling==1.27.0", "pathspec==0.12.1", "pluggy==1.6.0", "trove-classifiers==2025.5.9.12",
              "packaging==25.0"]
ENV = {"sys_platform": "linux", "platform_system": "Linux", "implementation_name": "cpython", "os_name": "posix",
       "platform_python_implementation": "CPython", "python_version": f"{PY[0]}.{PY[1]}",
       "python_full_version": f"{PY[0]}.{PY[1]}.0", "platform_machine": "x86_64", "extra": ""}
OUT = Path(__file__).with_name("python3-deps.json")


def tag_priority(arch: str) -> list:
    plats = [f"manylinux_2_{m}_{arch}" for m in range(39, 16, -1)] + [f"manylinux2014_{arch}"]
    return list(cpython_tags(PY, platforms=plats)) + list(compatible_tags(PY, platforms=plats))


def pick(files: list[dict], arch: str | None) -> dict | None:
    order = tag_priority(arch or "x86_64")
    rank = {t: i for i, t in enumerate(order)}
    best, best_rank = None, None
    for f in files:
        if f["packagetype"] != "bdist_wheel" or f.get("yanked"):
            continue
        _, _, _, tags = parse_wheel_filename(f["filename"])
        r = min((rank[t] for t in tags if t in rank), default=None)
        if r is not None and (best_rank is None or r < best_rank):
            best, best_rank = f, r
    return best


def is_pure(f: dict) -> bool:
    _, _, _, tags = parse_wheel_filename(f["filename"])
    return any(t.platform == "any" for t in tags)


def requirements() -> list[Requirement]:
    out = subprocess.run(["uv", "export", "--no-dev", "--no-emit-project", "--no-hashes", "--format",
                          "requirements-txt"], check=True, capture_output=True, text=True).stdout
    reqs = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        if req.marker is not None and not Marker(str(req.marker)).evaluate(ENV):
            continue
        if canonicalize_name(req.name) in SKIP:
            continue
        reqs.append(req)
    have = {canonicalize_name(r.name) for r in reqs}
    for b in BUILD_DEPS:
        r = Requirement(b)
        if canonicalize_name(r.name) not in have:
            reqs.append(r)
    return reqs


def main() -> int:
    sources, names, missing = [], [], []
    for req in requirements():
        version = next(iter(req.specifier)).version
        with urllib.request.urlopen(f"https://pypi.org/pypi/{req.name}/{version}/json", timeout=30) as r:
            files = json.load(r)["urls"]
        names.append(req.name)
        f = pick(files, None)
        if f is not None and is_pure(f):
            sources.append({"type": "file", "url": f["url"], "sha256": f["digests"]["sha256"]})
            continue
        for arch in ARCHES:
            f = pick(files, arch)
            if f is None:
                missing.append(f"{req.name}=={version} ({arch})")
                continue
            sources.append({"type": "file", "url": f["url"], "sha256": f["digests"]["sha256"],
                            "only-arches": [arch]})
    module = {
        "name": "python3-deps",
        "buildsystem": "simple",
        "build-commands": [
            "pip3 install --verbose --exec-prefix=${FLATPAK_DEST} --prefix=${FLATPAK_DEST} --no-index "
            '--find-links="file://${PWD}" --no-build-isolation ' + " ".join(sorted(names))
        ],
        "sources": sources,
    }
    OUT.write_text(json.dumps(module, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} with {len(sources)} wheels for {len(names)} packages")
    if missing:
        print("No compatible wheel for:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
