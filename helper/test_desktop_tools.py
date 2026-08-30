import subprocess
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import desktop_tools
from desktop_tools import (
    DESKTOP_TOOLS,
    DESKTOP_TOOL_NAMES,
    MUTATING_DESKTOP_TOOLS,
    READ_ONLY_DESKTOP_TOOLS,
    execute,
)


EXPECTED_TOOLS = {
    "open_app", "close_app", "focus_window", "move_window", "switch_workspace",
    "find_file", "open_file", "move_file", "rename_file", "get_clipboard",
    "set_clipboard", "set_volume", "play_pause", "set_brightness",
    "send_notification", "git_status", "git_commit", "git_diff",
    "launch_terminal", "run_command", "browser_open", "browser_search",
    "browser_current_page", "lock_computer", "shutdown", "restart", "bash",
}


class DesktopToolSchemaTest(unittest.TestCase):
    def test_all_requested_tools_have_unique_typed_schemas(self):
        names = [tool["function"]["name"] for tool in DESKTOP_TOOLS]

        self.assertEqual(set(names), EXPECTED_TOOLS)
        self.assertEqual(len(names), len(set(names)))
        for tool in DESKTOP_TOOLS:
            self.assertFalse(tool["function"]["parameters"]["additionalProperties"])

    def test_every_tool_has_an_explicit_policy_classification(self):
        self.assertEqual(DESKTOP_TOOL_NAMES, EXPECTED_TOOLS)
        self.assertFalse(READ_ONLY_DESKTOP_TOOLS & MUTATING_DESKTOP_TOOLS)
        self.assertEqual(READ_ONLY_DESKTOP_TOOLS | MUTATING_DESKTOP_TOOLS, EXPECTED_TOOLS)


class DesktopToolBoundaryTest(unittest.TestCase):
    def test_paths_cannot_escape_home(self):
        with tempfile.TemporaryDirectory() as temp:
            fake_home = Path(temp).resolve()
            with patch.object(desktop_tools, "HOME", fake_home):
                with self.assertRaisesRegex(ValueError, "within the user's home"):
                    desktop_tools._path("/etc/passwd", must_exist=True)

    def test_rename_stays_in_the_same_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            fake_home = Path(temp).resolve()
            source = fake_home / "old.txt"
            source.write_text("hello")
            with patch.object(desktop_tools, "HOME", fake_home):
                output, failed = execute("rename_file", {
                    "path": str(source), "new_name": "new.txt",
                })

            self.assertFalse(failed, output)
            self.assertEqual(json.loads(output)["state"], "VERIFIED")
            self.assertTrue((fake_home / "new.txt").exists())

    @patch("desktop_tools.subprocess.run")
    def test_run_command_uses_argv_without_a_shell(self, run):
        run.return_value = Mock(stdout="ok\n", stderr="", returncode=0)

        output, failed = execute("run_command", {"argv": ["printf", "%s", "hello"]})

        self.assertFalse(failed, output)
        self.assertEqual(json.loads(output)["state"], "VERIFIED")
        argv, = run.call_args.args
        self.assertEqual(argv, ["printf", "%s", "hello"])
        self.assertNotIn("shell", run.call_args.kwargs)

    @patch("desktop_tools.subprocess.run")
    def test_bash_is_the_only_explicit_shell_entrypoint(self, run):
        run.return_value = Mock(stdout="ok\n", stderr="", returncode=0)

        output, failed = execute("bash", {"command": "printf hello"})

        self.assertFalse(failed, output)
        self.assertEqual(run.call_args.args[0], ["/bin/bash", "-lc", "printf hello"])

    def test_browser_rejects_non_http_urls(self):
        output, failed = execute("browser_open", {"url": "file:///etc/passwd"})

        self.assertTrue(failed)
        self.assertEqual(json.loads(output)["state"], "FAILED")
        self.assertIn("http or https", output)

    @patch("desktop_tools.subprocess.run")
    def test_power_action_is_a_fixed_command(self, run):
        run.return_value = Mock(stdout="", stderr="", returncode=0)

        output, failed = execute("restart", {})

        self.assertFalse(failed, output)
        self.assertEqual(json.loads(output)["state"], "DISPATCHED")
        self.assertEqual(run.call_args.args[0], ["systemctl", "reboot"])

    @patch("desktop_tools.time.sleep", return_value=None)
    @patch("desktop_tools._window_matches")
    @patch("desktop_tools._execute_once", return_value=("launch accepted", False))
    def test_app_launch_is_verified_from_observed_window_state(self, action, windows, sleep):
        windows.side_effect = [[], [{"class": "code"}]]

        output, failed = execute("open_app", {"app": "code"})

        result = json.loads(output)
        self.assertFalse(failed, output)
        self.assertEqual(result["state"], "VERIFIED")
        self.assertIn("matching window", result["verification"])
        self.assertEqual(result["attempts"], 2)

    @patch("desktop_tools._run")
    def test_clipboard_write_requires_exact_readback(self, run):
        run.side_effect = [("Completed with exit code 0", False), ("different", False)]

        output, failed = execute("set_clipboard", {"text": "expected"})

        result = json.loads(output)
        self.assertTrue(failed)
        self.assertEqual(result["state"], "FAILED")
        self.assertIn("did not match", result["verification"])


if __name__ == "__main__":
    unittest.main()
