#!/usr/bin/env python3
"""Update all repository-owned references to an upstream Wanderer release.

This script intentionally updates only the upstream application version. The
Helm chart version and the synchronizer image release are owned by Knope and
are not changed here.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


VERSION_RE = re.compile(r"^v?(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)$")
APP_VERSION_RE = re.compile(
    r'(?m)^(appVersion:\s*["\']?)(?P<version>v?[0-9]+\.[0-9]+\.[0-9]+)(["\']?\s*)$'
)


def version_tuple(version: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(version)
    if not match:
        raise ValueError(f"expected a plain semver release tag, got {version!r}")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def normalized_version(version: str) -> str:
    version_tuple(version)
    return version.removeprefix("v")


def read_required(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot read {path}: {exc}") from exc


def replace_exact(text: str, old: str, new: str, path: Path) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected 1 occurrence of {old!r}, found {count}"
        )
    return text.replace(old, new)


def replace_app_version(text: str, target: str, path: Path) -> tuple[str, str]:
    match = APP_VERSION_RE.search(text)
    if not match:
        raise RuntimeError(f"{path}: appVersion entry was not found")
    current = normalized_version(match.group("version"))
    replacement = f'{match.group(1)}{target}{match.group(3)}'
    return APP_VERSION_RE.sub(replacement, text, count=1), current


def update_repository_tag(text: str, repository: str, current: str, target: str, path: Path) -> str:
    pattern = re.compile(
        rf"(?m)^(?P<prefix>\s+repository:\s*{re.escape(repository)}\s*\n\s+tag:\s*)"
        rf"v?{re.escape(current)}(?P<suffix>\s*)$"
    )
    replacement = rf"\g<prefix>v{target}\g<suffix>"
    updated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected one {repository} tag at v{current}, found {count}"
        )
    return updated


def update(root: Path, requested_version: str) -> list[Path]:
    target = normalized_version(requested_version)
    chart_path = root / "charts/wanderer/Chart.yaml"
    values_path = root / "charts/wanderer/values.yaml"
    readme_path = root / "README.md"

    chart = read_required(chart_path)
    values = read_required(values_path)
    readme = read_required(readme_path)

    chart, current = replace_app_version(chart, target, chart_path)
    current_tuple = version_tuple(current)
    target_tuple = version_tuple(target)
    if target_tuple < current_tuple:
        raise RuntimeError(
            f"upstream version would move backwards from v{current} to v{target}"
        )
    if target_tuple == current_tuple:
        print(f"Wanderer is already at v{target}; no update needed")
        return []

    values = update_repository_tag(values, "flomp/wanderer-web", current, target, values_path)
    values = update_repository_tag(values, "flomp/wanderer-db", current, target, values_path)

    old_tag = f"v{current}"
    new_tag = f"v{target}"
    chart = replace_exact(
        chart,
        f"https://raw.githubusercontent.com/open-wanderer/wanderer/{old_tag}/",
        f"https://raw.githubusercontent.com/open-wanderer/wanderer/{new_tag}/",
        chart_path,
    )
    # Match current-release references, not historical version mentions.
    for reference in (
        "This chart deploys the three components used by Wanderer `{tag}`",
        "https://github.com/open-wanderer/wanderer/blob/{tag}/docker-compose.yml",
    ):
        readme = replace_exact(
            readme, reference.format(tag=old_tag), reference.format(tag=new_tag), readme_path
        )
    for repository in ("flomp/wanderer-web", "flomp/wanderer-db"):
        old_image = f"{repository}:{old_tag}"
        new_image = f"{repository}:{new_tag}"
        chart = replace_exact(chart, old_image, new_image, chart_path)
        readme = replace_exact(readme, old_image, new_image, readme_path)

    changed: list[Path] = []
    for path, content in (
        (chart_path, chart),
        (values_path, values),
        (readme_path, readme),
    ):
        original = read_required(path)
        if content != original:
            path.write_text(content, encoding="utf-8")
            changed.append(path)
    return changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        required=True,
        help="upstream Wanderer release tag, for example v0.21.0",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the parent of scripts/)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        changed = update(args.root.resolve(), args.version)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"update-wanderer-upstream: {exc}", file=sys.stderr)
        return 1
    for path in changed:
        print(f"updated {path.relative_to(args.root.resolve())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
