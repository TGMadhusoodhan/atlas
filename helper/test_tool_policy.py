import asyncio
import unittest

from tool_policy import (
    ApprovalBroker, Permission, is_mutating, parse_tool_arguments,
    permission_for, requires_approval,
)
from desktop_tools import DESKTOP_TOOL_NAMES


class ToolPolicyTest(unittest.IsolatedAsyncioTestCase):
    def test_malformed_arguments_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Malformed tool arguments"):
            parse_tool_arguments('{"target":')

    def test_permission_classes_are_distinct(self):
        self.assertFalse(requires_approval("read_file", {"path": "/tmp/x"}))
        self.assertFalse(requires_approval("git_status", {"repo": "~/atlas"}))
        self.assertTrue(requires_approval("lockdown_start", {}))
        self.assertFalse(requires_approval("open_app", {"app": "spotify"}))
        self.assertTrue(requires_approval("run_command", {"argv": ["date"]}))
        self.assertTrue(requires_approval("bash", {"command": "date"}))
        self.assertFalse(requires_approval("memory_forget", {"confirm": False}))
        self.assertTrue(requires_approval("memory_forget", {"confirm": True}))
        self.assertTrue(is_mutating("open_app", {"app": "spotify"}))
        self.assertFalse(is_mutating("read_file", {"path": "/tmp/x"}))
        self.assertEqual(permission_for("open_app", {}), Permission.SAFE)
        self.assertEqual(permission_for("move_window", {}), Permission.REVERSIBLE)
        self.assertEqual(permission_for("shutdown", {}), Permission.SENSITIVE)

    def test_every_desktop_tool_has_a_permission_class(self):
        for name in DESKTOP_TOOL_NAMES:
            permission = permission_for(name, {"confirm": name in {"memory_forget", "forget_fact"}})
            self.assertIsInstance(permission, Permission)

    async def test_approval_is_scoped_and_consumed_once(self):
        broker = ApprovalBroker(timeout_seconds=1)
        waiter = asyncio.create_task(broker.request("request", "call", "remember_fact", {"fact": "x"}))
        await asyncio.sleep(0)

        self.assertFalse(broker.resolve("request", "call", "remember_fact", {"fact": "changed"}, True))
        self.assertTrue(broker.resolve("request", "call", "remember_fact", {"fact": "x"}, True))
        self.assertTrue(await waiter)
        self.assertFalse(broker.resolve("request", "call", "remember_fact", {"fact": "x"}, True))

    async def test_approval_expires(self):
        broker = ApprovalBroker(timeout_seconds=0.001)
        self.assertFalse(await broker.request("request", "call", "remember_fact", {"fact": "x"}))
        self.assertFalse(broker.resolve("request", "call", "remember_fact", {"fact": "x"}, True))

    async def test_pending_is_registered_before_ui_notification(self):
        broker = ApprovalBroker(timeout_seconds=1)

        approved = await broker.request(
            "request", "call", "remember_fact", {"fact": "x"},
            on_pending=lambda: self.assertTrue(
                broker.resolve("request", "call", "remember_fact", {"fact": "x"}, True)
            ),
        )

        self.assertTrue(approved)


if __name__ == "__main__":
    unittest.main()
