from __future__ import annotations

import importlib.util
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
        (root / "charts/wanderer").mkdir(parents=True)
        (root / "charts/wanderer/Chart.yaml").write_text(
            "\n".join(
                [
                    "apiVersion: v2",
                    "name: wanderer",
                    'version: 0.2.0',
                    'appVersion: "0.20.0"',
                    "icon: https://raw.githubusercontent.com/open-wanderer/wanderer/v0.20.0/logo.svg",
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
            "Wanderer v0.20.0\nflomp/wanderer-web:v0.20.0\n"
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

    def test_downgrade_is_rejected(self) -> None:
        root = self.make_fixture()
        with self.assertRaisesRegex(RuntimeError, "move backwards"):
            MODULE.update(root, "v0.19.9")


if __name__ == "__main__":
    unittest.main()
