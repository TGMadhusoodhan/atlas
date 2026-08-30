import asyncio
import unittest

from tool_policy import ApprovalBroker, parse_tool_arguments, requires_approval


class ToolPolicyTest(unittest.IsolatedAsyncioTestCase):
    def test_malformed_arguments_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Malformed tool arguments"):
            parse_tool_arguments('{"target":')

    def test_read_only_and_mutating_tools_are_separate(self):
        self.assertFalse(requires_approval("read_file", {"path": "/tmp/x"}))
        self.assertTrue(requires_approval("lockdown_start", {}))
        self.assertFalse(requires_approval("memory_forget", {"confirm": False}))
        self.assertTrue(requires_approval("memory_forget", {"confirm": True}))

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
