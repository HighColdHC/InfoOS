"""Print a pyproject.toml that keeps only what pip needs to install InfoOS dependencies.

The image installs dependencies before copying the source. Feeding that layer
the full pyproject.toml (and the README it names) meant tool settings or
README edits reinstalled the multi-GB Python and CUDA layers. This output only
changes when a real dependency, the package name/version or package discovery
changes, so BuildKit reuses those layers otherwise. The `dev` extra is never
installed in the image and is left out.
"""
from __future__ import annotations

import json
import sys
import tomllib

SKIPPED_EXTRAS = {"dev"}


def _value(value: object) -> str:
    # JSON strings, string arrays and booleans are valid TOML literals.
    return json.dumps(value, ensure_ascii=False)


def render(pyproject: dict) -> str:
    build = pyproject["build-system"]
    project = pyproject["project"]
    lines = [
        "[build-system]",
        f"requires = {_value(build['requires'])}",
        f"build-backend = {_value(build['build-backend'])}",
        "",
        "[project]",
    ]
    for key in ("name", "version", "requires-python"):
        if key in project:
            lines.append(f"{key} = {_value(project[key])}")
    lines.append(f"dependencies = {_value(project.get('dependencies', []))}")
    extras = {name: deps for name, deps in project.get("optional-dependencies", {}).items()
              if name not in SKIPPED_EXTRAS}
    if extras:
        lines += ["", "[project.optional-dependencies]"]
        lines += [f"{_value(name)} = {_value(extras[name])}" for name in sorted(extras)]
    setuptools = pyproject.get("tool", {}).get("setuptools", {})
    if "include-package-data" in setuptools:
        lines += ["", "[tool.setuptools]",
                  f"include-package-data = {_value(setuptools['include-package-data'])}"]
    find = setuptools.get("packages", {}).get("find", {})
    if find:
        lines += ["", "[tool.setuptools.packages.find]"]
        lines += [f"{key} = {_value(find[key])}" for key in sorted(find)]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python-deps-manifest.py <pyproject.toml>", file=sys.stderr)
        return 64
    with open(argv[1], "rb") as handle:
        sys.stdout.write(render(tomllib.load(handle)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
