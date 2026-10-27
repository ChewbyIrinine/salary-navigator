"""Offline checks: no browser or tunnel is started."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "preflight", Path(__file__).parent / "skills/collect-avito/scripts/preflight.py")
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def config(rules=None, protocol="freedom"):
    return {"outbounds": [{"tag": "direct", "protocol": protocol}],
            "routing": {"rules": rules if rules is not None else [
                {"type": "field", "domain": ["domain:avito.ru", "domain:avito.st"],
                 "outboundTag": "direct"}]}}


class RoutingTests(unittest.TestCase):
    def test_both_domains_and_udp_only_block(self):
        data = config()
        data["routing"]["rules"].insert(0, {
            "type": "field", "network": "udp", "port": "443", "outboundTag": "block"})
        self.assertEqual(preflight.check_xray(data), [])

    def test_shadowing_proxy_is_rejected(self):
        data = config()
        data["routing"]["rules"].insert(0, {"type": "field", "network": "tcp", "outboundTag": "proxy"})
        self.assertEqual(len(preflight.check_xray(data)), 2)

    def test_direct_tag_cannot_hide_proxy_protocol(self):
        self.assertEqual(len(preflight.check_xray(config(protocol="vless"))), 2)

    def test_static_domain_is_required(self):
        data = config()
        data["routing"]["rules"][0]["domain"] = ["domain:avito.ru"]
        errors = preflight.check_xray(data)
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("avito.st:"))

    def test_unknown_geosite_rule_needs_review(self):
        data = config()
        data["routing"]["rules"].insert(0, {
            "type": "field", "domain": ["geosite:ru"], "outboundTag": "proxy"})
        self.assertEqual(len(preflight.check_xray(data)), 2)

    def test_extra_conditions_do_not_prove_whole_domain_route(self):
        data = config()
        data["routing"]["rules"][0]["inboundTag"] = ["one-inbound"]
        self.assertEqual(len(preflight.check_xray(data)), 2)

    def test_unrelated_domains_do_not_shadow(self):
        data = config()
        data["routing"]["rules"].insert(0, {
            "type": "field", "domain": ["domain:example.org"], "outboundTag": "proxy"})
        self.assertEqual(preflight.check_xray(data), [])

    def test_bad_schema_is_reported(self):
        for data in ([], {"routing": {"rules": "invalid"}}):
            self.assertTrue(preflight.check_xray(data))

    def test_missing_outbound_tag_cannot_be_treated_as_direct(self):
        data = config()
        del data["outbounds"][0]["tag"]
        del data["routing"]["rules"][0]["outboundTag"]
        self.assertTrue(preflight.check_xray(data))

    def test_unknown_network_is_not_tcp_confirmation(self):
        data = config()
        data["routing"]["rules"][0]["network"] = "unknown"
        self.assertTrue(preflight.check_xray(data))


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        # These checks must not depend on a developer's installed browser/skills.
        self.skill_patch = patch.object(preflight, "find_browse_skill", return_value=Path("SKILL.md"))
        self.cli_patch = patch.object(preflight.shutil, "which", return_value="aside")
        self.skill_patch.start()
        self.cli_patch.start()
        self.addCleanup(self.skill_patch.stop)
        self.addCleanup(self.cli_patch.stop)

    def test_missing_config_is_warning_without_starting_browser(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(preflight, "aside_ready") as probe:
            result = preflight.run_checks(Path(tmp) / "missing.json")
        self.assertEqual(result["status"], "READY_WITH_WARNINGS")
        self.assertFalse(result["can_collect"])  # REPL not probed in this call.
        self.assertTrue(any(c.get("next_step") for c in result["checks"]))
        probe.assert_not_called()

    def test_ready_aside_with_missing_config_can_collect(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(preflight, "aside_ready", return_value=True), \
                patch.object(preflight.shutil, "which", return_value="aside"):
            result = preflight.run_checks(Path(tmp) / "missing.json", probe_aside=True)
        self.assertEqual(result["status"], "READY_WITH_WARNINGS")
        self.assertTrue(result["can_collect"])
        self.assertFalse(result["live_route_verified"])

    def test_failed_aside_remains_blocking_even_with_routing_warning(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(preflight, "aside_ready", return_value=False), \
                patch.object(preflight.shutil, "which", return_value="aside"):
            result = preflight.run_checks(Path(tmp) / "missing.json", probe_aside=True)
        self.assertEqual(result["status"], "NEEDS_SETUP")
        self.assertFalse(result["can_collect"])

    def test_profile_error_is_actionable_without_private_cli_output(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(preflight, 'aside_command', side_effect=RuntimeError('Multiple connected Aside profiles; select one with TL_ASIDE_PROFILE')):
            result = preflight.run_checks(Path(tmp)/'missing.json', probe_aside=True)
        session = next(c for c in result['checks'] if c['check'] == 'aside_session')
        self.assertFalse(result['can_collect'])
        self.assertIn('Multiple connected Aside profiles', session['detail'])
        self.assertIn('TL_ASIDE_PROFILE', session['detail'])

    def test_strict_route_audit_requires_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = preflight.run_checks(Path(tmp) / "missing.json", strict_routing=True)
        self.assertEqual(result["status"], "NEEDS_SETUP")

    def test_unreadable_xray_file_is_not_aside_failure(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(preflight, "aside_ready", return_value=True), \
                patch.object(preflight.shutil, "which", return_value="aside"):
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"tunnel_mode": "xray", "xray_config_path": str(Path(tmp) / "absent.json")}))
            result = preflight.run_checks(path, probe_aside=True)
        self.assertEqual(result["status"], "READY_WITH_WARNINGS")
        self.assertTrue(result["can_collect"])
        self.assertFalse(result["live_route_verified"])

    def test_repeated_check_reads_changed_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local.json"
            path.write_text(json.dumps({"tunnel_mode": "disabled"}))
            first = preflight.run_checks(path)
            path.write_text(json.dumps({"tunnel_mode": "unknown"}))
            second = preflight.run_checks(path)
        self.assertTrue(next(c for c in first["checks"] if c["check"] == "tunnel_mode")["ok"])
        self.assertFalse(next(c for c in second["checks"] if c["check"] == "tunnel_mode")["ok"])
        self.assertFalse(first["live_route_verified"])

    def test_config_secrets_are_not_in_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp) / "private.json"
            private.write_text(json.dumps({**config(protocol="vless"), "secret": "DO_NOT_PRINT"}))
            settings = Path(tmp) / "local.json"
            settings.write_text(json.dumps({"tunnel_mode": "xray", "xray_config_path": str(private)}))
            result = preflight.run_checks(settings)
        rendered = json.dumps(result)
        self.assertNotIn("DO_NOT_PRINT", rendered)
        self.assertNotIn(str(private), rendered)
        self.assertFalse(next(c for c in result["checks"] if c["check"] == "tunnel_config")["ok"])

    def test_readiness_needs_marker_and_no_errors(self):
        for stdout, stderr, expected in (("TNL_ASIDE_READY\n", "", True),
                                         ("", "", False),
                                         ("TNL_ASIDE_READY\n", "[error] private info", False)):
            with patch.object(preflight, "aside_command", return_value=["aside", "--profile", "connected"]), \
                    patch.object(preflight.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, stdout, stderr)) as run:
                self.assertEqual(preflight.aside_ready("aside"), expected)
                command = run.call_args.args[0]
                self.assertEqual(command, ["aside", "--profile", "connected", "repl", 'console.log("TNL_ASIDE_READY")'])
        with patch.object(preflight.subprocess, "run", side_effect=subprocess.TimeoutExpired("aside", 20)):
            self.assertFalse(preflight.aside_ready("aside"))


class AsideProfileTests(unittest.TestCase):
    def select(self, profiles, env=None):
        result = subprocess.CompletedProcess([], 0, json.dumps({"profiles": profiles}), "")
        with patch.dict(preflight.os.environ, env or {}, clear=True), \
                patch.object(preflight.subprocess, "run", side_effect=lambda command, **kw:
                    subprocess.CompletedProcess([], 0, '* u0  private-account signed in\n', '')
                    if command[1:3] == ['account', 'status'] else result) as call:
            command = preflight.aside_command("aside")
        return command, call.call_args.args[0]

    def test_disconnected_default_uses_connected_profile(self):
        command, listing = self.select([
            {"profileId": "default", "connected": False},
            {"profileId": "working", "connected": True}])
        self.assertEqual(command, ["aside", "--host", "local", "--account", "u0", "--profile", "working"])
        self.assertEqual(listing, ["aside", "--host", "local", "--account", "u0", "host", "profiles", "--json"])

    def test_explicit_profile_host_and_account_are_preserved(self):
        command, listing = self.select([
            {"profileId": "first", "connected": True},
            {"profileId": "chosen", "connected": True}],
            {"TL_ASIDE_HOST": "my-host", "TL_ASIDE_ACCOUNT": "u1", "TL_ASIDE_PROFILE": "chosen"})
        self.assertEqual(command, ["aside", "--host", "my-host", "--account", "u1", "--profile", "chosen"])
        self.assertEqual(listing, command[:5] + ["host", "profiles", "--json"])

    def test_no_connected_profile_or_ambiguous_selection_stops(self):
        for profiles, env in [([], {}),
                ([{"profileId": "a", "connected": True}, {"profileId": "b", "connected": True}], {}),
                ([{"profileId": "a", "connected": True}], {"TL_ASIDE_PROFILE": "missing"})]:
            with self.assertRaises(RuntimeError):
                self.select(profiles, env)

    def test_invalid_private_diagnostics_are_not_exposed(self):
        for stdout, code in [("private SECRET", 0), ("private SECRET", 1), ('{"profiles":null}', 0)]:
            with patch.dict(preflight.os.environ, {"TL_ASIDE_ACCOUNT": "u0"}, clear=True), \
                    patch.object(preflight.subprocess, "run", return_value=subprocess.CompletedProcess([], code, stdout, "SECRET")):
                with self.assertRaises(RuntimeError) as error:
                    preflight.aside_command("aside")
                self.assertNotIn("SECRET", str(error.exception))


if __name__ == "__main__":
    unittest.main()
