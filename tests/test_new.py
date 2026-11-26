"""Exercise czdev new and how czdev publish reads its generated metadata."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from czdev import publish


@unittest.skipUnless(shutil.which("git"), "czdev new requires git")
class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.template = self.root / "template"
        self.template.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.template)], check=True)
        (self.template / "CMakeLists.txt").write_text(
            'project(template_app)\nset(APP_DISPLAY_NAME "TemplateApp")\n', encoding="utf-8")
        images = self.template / "assets" / "images"
        images.mkdir(parents=True)
        (images / "template.png").write_bytes(b"test fixture")

    def scaffold(self, manifest, *args):
        if manifest is not None:
            (self.template / "app-builder.json").write_text(
                json.dumps(manifest), encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.template, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                        "commit", "-qm", "Fixture"], cwd=self.template, check=True)
        target = self.root / "generated"
        subprocess.run([str(ROOT / "czdev"), "new", "test-app", "--no-git",
                        "--template", self.template.as_uri(), "--dir", str(target), *args],
                       check=True, capture_output=True, text=True)
        return target

    def fixture(self):
        return {
            "app_name": "Template",
            "package_name": "template_app",
            "version": "0.2.1",
            "store": {
                "summary": "TODO: a summary",
                "screenshots": ["screenshot/test.png"],
                "icon": "assets/images/template.png",
                "author": {"display_name": "Keep this name"},
            },
        }

    def test_explicit_name_reaches_store_metadata_without_renaming_package(self):
        fixture = self.fixture()
        target = self.scaffold(fixture, "--display-name", "Alternate Title")
        raw, project = publish.load_app_builder(target)
        # Match the publish metadata path without accessing the Store or GitHub.
        with patch.object(publish, "fetch_url", return_value=None):
            metadata = publish.build_merged_meta(
                publish.store_meta_from(raw), {"package": "test-app"},
                SimpleNamespace(login="test-user"))
        self.assertEqual(project, str(target))
        self.assertEqual(metadata["title"], "Alternate Title")
        self.assertEqual(raw["package_name"], "test-app")
        self.assertEqual(raw["version"], fixture["version"])
        store_expected = dict(fixture["store"], icon="assets/images/test-app.png")
        self.assertEqual(raw["store"], store_expected)
        self.assertIn('APP_DISPLAY_NAME "Alternate Title"', (target / "CMakeLists.txt").read_text())
        self.assertTrue((target / raw["store"]["icon"]).exists())

    def test_derived_name_when_display_name_is_omitted(self):
        fixture = self.fixture()
        del fixture["app_name"]
        raw, _ = publish.load_app_builder(self.scaffold(fixture))
        self.assertEqual(publish.store_meta_from(raw)["title"], "Test App")
        self.assertEqual(raw["package_name"], "test-app")

    def test_manifest_escapes_display_name_even_in_existing_placeholder_values(self):
        fixture = self.fixture()
        fixture["store"]["summary"] = "TemplateApp summary"
        title = 'Café "Clock" \\ Time'
        raw, _ = publish.load_app_builder(self.scaffold(fixture, "--display-name", title))
        self.assertEqual(publish.store_meta_from(raw)["title"], title)
        self.assertEqual(raw["store"]["summary"], title + " summary")

    def test_template_without_manifest_is_not_given_one(self):
        target = self.scaffold(None)
        self.assertFalse((target / "app-builder.json").exists())
        self.assertIn('APP_DISPLAY_NAME "Test App"', (target / "CMakeLists.txt").read_text())


if __name__ == "__main__":
    unittest.main()
