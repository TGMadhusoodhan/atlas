import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import desktop_tools
from desktop_context import (
    DesktopContextStore, detect_git_project, normalize_media,
    normalize_window, normalize_workspace,
)


ACTIVE = {
    "class": "code", "title": "desktop_context.py — atlas", "pid": 12345,
    "workspace": {"id": 4, "name": "4"}, "monitor": 0,
    "address": "0xabc",
}
MONITORS = [{"id": 0, "name": "DP-1", "focused": True,
             "activeWorkspace": {"id": 4, "name": "4"}}]


class FakeRunner:
    def __init__(self, values):
        self.values = values

    def __call__(self, argv):
        key = tuple(argv)
        value = self.values.get(key, ("missing", True))
        return value() if callable(value) else value


class NormalizationTests(unittest.TestCase):
    def test_active_window_normalization(self):
        window = normalize_window(ACTIVE)
        self.assertEqual(window["class"], "code")
        self.assertEqual(window["pid"], 12345)
        self.assertEqual(window["workspace"]["id"], 4)
        self.assertEqual(window["address"], "0xabc")

    def test_workspace_and_malformed_window_normalization(self):
        self.assertEqual(normalize_workspace({"id": 3, "name": "dev"}),
                         {"id": 3, "name": "dev"})
        self.assertIsNone(normalize_window("bad")["class"])

    def test_media_normalization(self):
        media = normalize_media("spotify", "Playing", "Track", "Artist")
        self.assertTrue(media["available"])
        self.assertEqual(media["title"], "Track")


class GitContextTests(unittest.TestCase):
    def test_git_project_dirty_and_clean_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            common = {
                ("git", "-C", str(root), "rev-parse", "--show-toplevel"): (str(root), False),
                ("git", "-C", str(root), "branch", "--show-current"): ("main", False),
                ("git", "-C", str(root), "rev-parse", "--short", "HEAD"): ("abc123", False),
            }
            dirty = detect_git_project(root, FakeRunner({
                **common, ("git", "-C", str(root), "status", "--porcelain"): (" M x.py", False),
            }))
            clean = detect_git_project(root, FakeRunner({
                **common, ("git", "-C", str(root), "status", "--porcelain"): ("", False),
            }))
        self.assertTrue(dirty["dirty"])
        self.assertFalse(clean["dirty"])
        self.assertEqual(dirty["branch"], "main")

    def test_missing_git_repository_has_low_confidence(self):
        project = detect_git_project(Path("/tmp"), FakeRunner({}))
        self.assertIsNone(project["project"])
        self.assertEqual(project["confidence"], "low")


class SnapshotTests(unittest.TestCase):
    def runner(self, *, player=True):
        values = {
            ("hyprctl", "activewindow", "-j"): (json.dumps(ACTIVE), False),
            ("hyprctl", "activeworkspace", "-j"): (json.dumps({"id": 4, "name": "4"}), False),
            ("hyprctl", "clients", "-j"): (json.dumps([ACTIVE]), False),
            ("hyprctl", "monitors", "-j"): (json.dumps(MONITORS), False),
            ("playerctl", "-l"): (("spotify", False) if player else ("not found", True)),
            ("playerctl", "-p", "spotify", "status"): ("Playing", False),
            ("playerctl", "-p", "spotify", "metadata", "--format", "{{title}}"): ("Track", False),
            ("playerctl", "-p", "spotify", "metadata", "--format", "{{artist}}"): ("Artist", False),
        }
        return FakeRunner(values)

    @patch.object(DesktopContextStore, "_process_cwds", return_value=[])
    def test_snapshot_schema_and_open_windows(self, cwd):
        snapshot = DesktopContextStore(self.runner()).get_snapshot(refresh=True)
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["workspace"]["id"], 4)
        self.assertEqual(snapshot["monitor"]["name"], "DP-1")
        self.assertEqual(snapshot["active_window"]["monitor"], "DP-1")
        self.assertEqual(len(snapshot["windows"]), 1)
        self.assertEqual(snapshot["media"]["player"], "spotify")

    @patch.object(DesktopContextStore, "_process_cwds", return_value=[])
    def test_unavailable_playerctl_fails_soft(self, cwd):
        snapshot = DesktopContextStore(self.runner(player=False)).get_snapshot(refresh=True)
        self.assertFalse(snapshot["media"]["available"])
        self.assertEqual(snapshot["media"]["state"], "Unavailable")

    def test_unavailable_hyprland_returns_stale_schema(self):
        snapshot = DesktopContextStore(FakeRunner({})).get_snapshot(refresh=True)
        self.assertTrue(snapshot["stale"])
        self.assertFalse(snapshot["hyprland"]["available"])

    @patch.object(DesktopContextStore, "_process_cwds", return_value=[])
    def test_event_invalidates_snapshot(self, cwd):
        store = DesktopContextStore(self.runner(), ttl_seconds=100)
        store.get_snapshot(refresh=True)
        self.assertTrue(store.apply_event("workspace>>3"))
        self.assertTrue(store._dirty)
        self.assertFalse(store.apply_event("bell>>irrelevant"))

    @patch.object(DesktopContextStore, "_process_cwds", return_value=[])
    def test_compact_prompt_omits_window_list_and_clipboard(self, cwd):
        store = DesktopContextStore(self.runner())
        prompt = store.compact_prompt(store.get_snapshot(refresh=True))
        self.assertIn("Active app: code", prompt)
        self.assertIn("Workspace: 4", prompt)
        self.assertIn("Media: spotify", prompt)
        self.assertNotIn("0xabc", prompt)
        self.assertNotIn("clipboard", prompt.lower())

    @patch("desktop_tools.desktop_context.get_snapshot")
    def test_contextual_active_window_resolution_and_ambiguity(self, snapshot):
        snapshot.return_value = {
            "active_window": {"address": "0xabc"}, "windows": [],
            "developer": {"confidence": "low"},
        }
        self.assertEqual(desktop_tools._selector({}), "address:0xabc")
        snapshot.return_value = {
            "active_window": {"address": None}, "windows": [],
            "developer": {"confidence": "low"},
        }
        with self.assertRaisesRegex(ValueError, "No active window"):
            desktop_tools._selector({})

    @patch("desktop_tools.desktop_context.get_snapshot")
    def test_get_desktop_context_tool_schema(self, snapshot):
        snapshot.return_value = {"schema_version": 1, "windows": []}
        output, failed = desktop_tools.execute("get_desktop_context", {"refresh": True})
        self.assertFalse(failed, output)
        envelope = json.loads(output)
        self.assertEqual(envelope["state"], "VERIFIED")
        self.assertEqual(json.loads(envelope["output"])["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
