from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "update_wanderer_upstream.py"
SPEC = importlib.util.spec_from_file_location("update_wanderer_upstream", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class UpdateWandererUpstreamTest(unittest.TestCase):
    def make_fixture(self) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        (root / "charts/wanderer").mkdir(parents=True)
        (root / "charts/wanderer/Chart.yaml").write_text(
            "\n".join(
                [
                    "apiVersion: v2",
                    "name: wanderer",
                    'version: 0.2.0',
                    'appVersion: "0.20.0"',
                    "icon: https://raw.githubusercontent.com/open-wanderer/wanderer/v0.20.0/logo.svg",
                    "annotations:",
                    "  artifacthub.io/images: |",
                    "    - name: wanderer-web",
                    "      image: flomp/wanderer-web:v0.20.0",
                    "    - name: wanderer-db",
                    "      image: flomp/wanderer-db:v0.20.0",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (root / "charts/wanderer/values.yaml").write_text(
            "\n".join(
                [
                    "web:",
                    "  image:",
                    "    repository: flomp/wanderer-web",
                    "    tag: v0.20.0",
                    "database:",
                    "  image:",
                    "    repository: flomp/wanderer-db",
                    "    tag: v0.20.0",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (root / "README.md").write_text(
            "This chart deploys the three components used by Wanderer `v0.20.0`.\n"
            "flomp/wanderer-web:v0.20.0\nflomp/wanderer-db:v0.20.0\n"
            "https://github.com/open-wanderer/wanderer/blob/v0.20.0/docker-compose.yml\n",
            encoding="utf-8",
        )
        return root

    def test_updates_chart_images_and_documentation(self) -> None:
        root = self.make_fixture()
        changed = MODULE.update(root, "v0.21.0")

        self.assertEqual(
            {path.relative_to(root) for path in changed},
            {
                Path("charts/wanderer/Chart.yaml"),
                Path("charts/wanderer/values.yaml"),
                Path("README.md"),
            },
        )
        self.assertIn('appVersion: "0.21.0"', (root / "charts/wanderer/Chart.yaml").read_text())
        self.assertIn("tag: v0.21.0", (root / "charts/wanderer/values.yaml").read_text())
        self.assertNotIn("v0.20.0", (root / "README.md").read_text())

    def test_same_version_is_idempotent(self) -> None:
        root = self.make_fixture()
        self.assertEqual(MODULE.update(root, "0.20.0"), [])

    def test_real_repository_cli_preserves_history_and_release_versions(self) -> None:
        root = self.make_fixture()
        for relative in ("README.md", "charts/wanderer/Chart.yaml", "charts/wanderer/values.yaml"):
            shutil.copyfile(SCRIPT.parents[1] / relative, root / relative)
        readme_path = root / "README.md"
        history = "\nHistorical release v0.20.0; unrelated image example:v0.20.0.\n"
        readme_path.write_text(readme_path.read_text() + history)
        before_chart = (root / "charts/wanderer/Chart.yaml").read_text()
        before_values = (root / "charts/wanderer/values.yaml").read_text()
        current = MODULE.APP_VERSION_RE.search(before_chart).group("version")
        major, minor, _ = MODULE.version_tuple(current)
        first = f"v{major}.{minor + 1}.0"
        final = f"v{major}.{minor + 2}.0"
        for version in (first, first, final):
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(root), "--version", version],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            readme = readme_path.read_text()
            self.assertIn(f"used by Wanderer `{version}`", readme)
            self.assertIn(f"flomp/wanderer-web:{version}", readme)
            self.assertIn(f"flomp/wanderer-db:{version}", readme)
            self.assertIn(f"/blob/{version}/docker-compose.yml", readme)
            self.assertIn("Wanderer `v0.20.0` accepts FIT files directly.", readme)
            self.assertTrue(readme.endswith(history))
        self.assertEqual(
            (root / "charts/wanderer/Chart.yaml").read_text(),
            before_chart.replace(f'appVersion: "{current}"', f'appVersion: "{final[1:]}"')
            .replace(f'/v{current}/', f'/{final}/')
            .replace(f'flomp/wanderer-web:v{current}', f'flomp/wanderer-web:{final}')
            .replace(f'flomp/wanderer-db:v{current}', f'flomp/wanderer-db:{final}'),
        )
        self.assertEqual(
            (root / "charts/wanderer/values.yaml").read_text(),
            before_values.replace(f'tag: v{current}', f'tag: {final}'),
        )

    def test_missing_current_reference_fails_without_writes(self) -> None:
        root = self.make_fixture()
        path = root / "README.md"
        path.write_text(path.read_text().replace("flomp/wanderer-db:v0.20.0", "flomp/wanderer-db:v0.19.0"))
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        with self.assertRaisesRegex(RuntimeError, "flomp/wanderer-db"):
            MODULE.update(root, "v0.21.0")
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_downgrade_is_rejected(self) -> None:
        root = self.make_fixture()
        with self.assertRaisesRegex(RuntimeError, "move backwards"):
            MODULE.update(root, "v0.19.9")


if __name__ == "__main__":
    unittest.main()
