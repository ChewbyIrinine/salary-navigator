"""First-run file preparation and CSV invariants; no installers or network."""
import base64
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).parent / "skills/collect-avito/scripts"
sys.path.insert(0, str(SCRIPTS))
import setup_workspace as setup
import preflight
import export_captures


class SetupTests(unittest.TestCase):
    def test_happ_files_are_importable_repeatable_and_static_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            settings = directory / "local.json"
            profile_dir = directory / "private"
            first = setup.prepare("happ", settings, profile_dir)
            link = (profile_dir / "avito-routing-link.txt").read_text().strip()
            decoded = json.loads(base64.b64decode(link.removeprefix("happ://routing/add/")))
            self.assertEqual(decoded, json.loads((profile_dir / "avito-routing.json").read_text()))
            self.assertEqual(preflight.check_happ(decoded), [])
            self.assertTrue(first["happ_import_required"])
            self.assertFalse(first["live_route_verified"])
            before = settings.stat().st_mtime_ns
            self.assertEqual(setup.prepare("happ", settings, profile_dir)["created"], [])
            self.assertEqual(settings.stat().st_mtime_ns, before)
            with patch.object(preflight, "find_browse_skill", return_value=SCRIPTS), \
                    patch.object(preflight.shutil, "which", return_value="aside"), \
                    patch.object(preflight, "aside_ready", return_value=True):
                checked = preflight.run_checks(settings, probe_aside=True, strict_routing=True)
            self.assertEqual(checked["status"], "LOCAL_CHECKS_OK")
            self.assertTrue(checked["can_collect"])
            self.assertFalse(checked["live_route_verified"])

    def test_existing_different_settings_are_not_reset_or_partly_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local.json"
            setup.prepare("disabled", path)
            previous = path.read_bytes()
            with self.assertRaises(ValueError):
                setup.prepare("happ", path, Path(tmp) / "private")
            self.assertEqual(path.read_bytes(), previous)
            self.assertFalse((Path(tmp) / "private").exists())

    def test_changed_happ_profile_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); private = directory / "private"
            setup.prepare("happ", directory / "settings.json", private)
            path = private / "avito-routing.json"
            path.write_text('{"Name":"user edited"}')
            with self.assertRaises(ValueError):
                setup.prepare("happ", directory / "settings.json", private)
            self.assertEqual(json.loads(path.read_text()), {"Name": "user edited"})

    def test_no_happ_artifacts_in_checkout(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(setup, "ROOT", Path(tmp)):
            with self.assertRaises(ValueError):
                setup.prepare("happ", Path(tmp) / "local.json", Path(tmp) / "private")

    def test_happ_shadowing_and_missing_domain_fail(self):
        profile = setup.happ_profile()
        profile["RouteOrder"] = "block-proxy-direct"
        self.assertTrue(preflight.check_happ(profile))
        profile = setup.happ_profile();profile["DirectSites"].pop()
        self.assertTrue(preflight.check_happ(profile))

    def test_explicit_browse_path_and_invalid_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "SKILL.md";skill.write_text("name: browse")
            self.assertEqual(preflight.find_browse_skill(explicit=str(skill)), skill)
            self.assertIsNone(preflight.find_browse_skill(explicit=str(skill) + "missing"))


class ExportTests(unittest.TestCase):
    def prepare(self, root):
        raw = {"id": "123", "state": "DETAIL", "title": "Пример",
               "source_url": "https://www.avito.ru/moskva/vakansii/job_123",
               "observed_at": "2026-10-27T12:00:00+00:00",
               "description": 'Первая строка, "кавычки"\nВторая строка',
               "fields": [{"name": "График", "value": "2/2, 3/3"}],
               "location_raw": ["Москва, улица Примерная"],
               "read_status": {"location": "observed"}}
        (root / "123.json").write_text(json.dumps(raw))
        review = root / "review.json"
        review.write_text(json.dumps({"123": {"reviewed": True, "city": "Москва"}}))
        return raw, review

    def test_csv_preserves_location_characteristics_and_full_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);raw, review = self.prepare(root);output = root / "out.csv"
            result = export_captures.export(root, review, output)
            with output.open(encoding="utf-8-sig", newline="") as stream:
                row, = list(csv.DictReader(stream))
            self.assertEqual(row["description"], raw["description"])
            self.assertEqual(row["location_observed"], raw["location_raw"][0])
            self.assertEqual(row["График"], "2/2, 3/3")
            self.assertEqual(result["rows"], 1)
            with self.assertRaises(FileExistsError):
                export_captures.export(root, review, output)

    def test_missing_location_and_unreviewed_capture_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);raw, review = self.prepare(root)
            raw["location_raw"] = []
            (root / "123.json").write_text(json.dumps(raw))
            with self.assertRaises(ValueError):
                export_captures.export(root, review, root / "out.csv")
            self.assertFalse((root / "out.csv").exists())
            self.prepare(root)
            review.write_text(json.dumps({"123": {"city": "Москва"}}))
            with self.assertRaises(ValueError):
                export_captures.export(root, review, root / "out.csv")


if __name__ == "__main__":
    unittest.main()
