import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import knowledge


class KnowledgeBoundaryTest(unittest.TestCase):
    def test_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "allowed"
            root.mkdir()
            with patch.object(knowledge, "load_config", return_value=([str(root)], [], 10)):
                with self.assertRaises(PermissionError):
                    knowledge.resolve_allowed_path(str(root / ".." / "secret.txt"))

    def test_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "allowed"
            root.mkdir()
            outside = base / "outside.txt"
            outside.write_text("secret")
            link = root / "link.txt"
            link.symlink_to(outside)
            with patch.object(knowledge, "load_config", return_value=([str(root)], [], 10)):
                with self.assertRaises(PermissionError):
                    knowledge.resolve_allowed_path(str(link))

    def test_accepts_descendant_of_configured_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "allowed"
            child = root / "notes" / "one.txt"
            child.parent.mkdir(parents=True)
            child.write_text("ok")
            with patch.object(knowledge, "load_config", return_value=([str(root)], [], 10)):
                self.assertEqual(child.resolve(), knowledge.resolve_allowed_path(str(child)))


if __name__ == "__main__":
    unittest.main()
