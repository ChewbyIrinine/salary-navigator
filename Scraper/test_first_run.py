"""First-run file preparation and CSV invariants; no installers or network."""
import base64
import io
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
import run as runner


class SetupTests(unittest.TestCase):
    def test_runner_and_preflight_use_same_aside_outside_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);binary = root / '.local/bin/aside'
            binary.parent.mkdir(parents=True);binary.write_text('#!/bin/sh\n');binary.chmod(0o700)
            with patch.object(preflight.Path, 'home', return_value=root), \
                    patch.object(preflight.shutil, 'which', return_value=None), \
                    patch.object(runner, 'aside_command', side_effect=lambda executable:
                        [executable, '--host', 'local', '--account', 'u0', '--profile', 'connected']) as target, \
                    patch.object(runner.subprocess, 'Popen', side_effect=RuntimeError('test stop')) as command:
                self.assertEqual(preflight.find_aside(), str(binary))
                with self.assertRaisesRegex(RuntimeError, 'test stop'):
                    runner.browser({}, root, root)
                target.assert_called_once_with(str(binary))
                self.assertEqual(command.call_args.args[0], [str(binary), '--host', 'local',
                    '--account', 'u0', '--profile', 'connected', 'repl'])

    def test_runner_passes_explicit_config_to_its_preflight(self):
        with patch.object(runner.subprocess, 'run') as command:
            runner.preflight(Path('/private/custom/local.json'))
        self.assertEqual(command.call_args.args[0][-2:], ['--config', '/private/custom/local.json'])
        with patch.object(runner.subprocess, 'run') as command:
            runner.preflight()
        self.assertNotIn('--config', command.call_args.args[0])

    def test_pipe_reader_preserves_bytes_and_reports_eof_without_select(self):
        stream = io.BufferedReader(io.BytesIO('TL_SESSION=пример\nTL_FINISHED\n'.encode()))
        chunks, thread = runner.pipe_chunks(stream)
        received = bytearray()
        while True:
            chunk = chunks.get(timeout=1)
            if chunk is None:
                break
            received.extend(chunk)
        thread.join(timeout=1)
        self.assertEqual(received.decode(), 'TL_SESSION=пример\nTL_FINISHED\n')

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


if __name__ == "__main__":
    unittest.main()
