import json
import tempfile
import unittest
from pathlib import Path

from conversation_store import ConversationStore


class ConversationStoreTest(unittest.TestCase):
    def test_saved_session_survives_store_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "conversations.sqlite3"
            ConversationStore(db_path).save("one", [{"role": "user", "content": "hello"}])

            restored = ConversationStore(db_path).load_last()

            self.assertEqual("one", restored["id"])
            self.assertEqual("hello", restored["messages"][0]["content"])

    def test_imports_legacy_json_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "sessions"
            legacy.mkdir()
            (legacy / "old.json").write_text(json.dumps({
                "id": "old",
                "created_at": "2025-01-01T00:00:00",
                "messages": [{"role": "user", "content": "legacy"}],
            }))

            store = ConversationStore(root / "conversations.sqlite3", legacy)
            ConversationStore(root / "conversations.sqlite3", legacy)

            self.assertEqual(["old"], [item["id"] for item in store.list_sessions()])

    def test_session_id_cannot_escape_database_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ConversationStore(root / "conversations.sqlite3")
            store.save("../../outside", [])

            self.assertEqual("../../outside", store.load_last()["id"])
            self.assertFalse((root.parent / "outside.json").exists())


if __name__ == "__main__":
    unittest.main()
