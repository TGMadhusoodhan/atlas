import time
import unittest

from cloud_preview import CloudPreviewBroker


class CloudPreviewBrokerTest(unittest.TestCase):
    def test_payload_is_consumed_once(self):
        broker = CloudPreviewBroker()
        payload = {"messages": [{"role": "user", "content": "hello"}]}
        token = broker.issue("request", payload)

        self.assertIs(payload, broker.consume("request", token))
        self.assertIsNone(broker.consume("request", token))

    def test_expired_payload_is_not_returned(self):
        broker = CloudPreviewBroker(ttl_seconds=0.001)
        token = broker.issue("request", {"messages": []})
        time.sleep(0.003)

        self.assertIsNone(broker.consume("request", token))

    def test_discard_prevents_send(self):
        broker = CloudPreviewBroker()
        token = broker.issue("request", {"messages": []})

        self.assertTrue(broker.discard(token))
        self.assertIsNone(broker.consume("request", token))

    def test_token_is_bound_to_request(self):
        broker = CloudPreviewBroker()
        token = broker.issue("request", {"messages": []})

        self.assertIsNone(broker.consume("different-request", token))


if __name__ == "__main__":
    unittest.main()
