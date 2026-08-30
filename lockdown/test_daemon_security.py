import unittest

from daemon import LockdownDaemon, create_browser_app, create_control_app


class LockdownTransportSecurityTest(unittest.TestCase):
    def test_tcp_browser_app_exposes_only_websocket(self):
        resources = {resource.canonical for resource in create_browser_app(LockdownDaemon()).router.resources()}
        self.assertEqual({"/ws"}, resources)

    def test_control_routes_are_not_added_to_browser_app(self):
        resources = {resource.canonical for resource in create_control_app(LockdownDaemon()).router.resources()}
        self.assertEqual({"/start", "/end", "/exception", "/status", "/monitors"}, resources)


if __name__ == "__main__":
    unittest.main()
