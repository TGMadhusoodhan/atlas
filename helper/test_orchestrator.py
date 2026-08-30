import unittest

from orchestrator import AtlasOrchestrator, Intent, IntentRouter, Verifier
from research_helper import build_query_body


class IntentRouterTests(unittest.TestCase):
    def setUp(self):
        self.router = IntentRouter()

    def test_routes_desktop_action(self):
        route = self.router.route("Open VS Code")
        self.assertEqual(route.intent, Intent.ACTION)
        self.assertTrue(route.needs_action)

    def test_routes_current_question_to_web_research(self):
        route = self.router.route("What is the latest Arch Linux news?")
        self.assertTrue(route.needs_research)
        self.assertTrue(route.needs_web_research)

    def test_local_project_search_stays_local(self):
        route = self.router.route("Search my repo for the voice prompt")
        self.assertTrue(route.needs_research)
        self.assertFalse(route.needs_web_research)

    def test_routes_memory_request(self):
        route = self.router.route("Remember that I prefer concise answers")
        self.assertTrue(route.needs_memory)


class OrchestrationTests(unittest.TestCase):
    def test_plan_assigns_internal_roles(self):
        orchestrator = AtlasOrchestrator()
        route, plan = orchestrator.prepare("Research current editors and open the best one")
        owners = [step.owner for step in plan]
        self.assertEqual(owners[0], "Planner")
        self.assertIn("Researcher", owners)
        self.assertIn("Executor", owners)
        self.assertEqual(owners[-1], "Verifier")
        self.assertEqual(route.intent, Intent.COMPOSITE)

    def test_query_preview_is_exact_initial_cloud_body(self):
        body = build_query_body("latest Wayland news", "model-x")
        self.assertEqual(body["model"], "model-x")
        self.assertEqual(body["messages"][-1]["content"], "latest Wayland news")
        self.assertFalse(body["stream"])


class VerifierTests(unittest.TestCase):
    def test_action_requires_mutation_evidence(self):
        verifier = Verifier(IntentRouter().route("Open Spotify"))
        self.assertFalse(verifier.assess().terminal)

    def test_failed_action_requests_retry(self):
        verifier = Verifier(IntentRouter().route("Open Spotify"))
        verifier.record(name="open_app", state="FAILED", mutating=True)
        self.assertFalse(verifier.assess().terminal)

    def test_verified_action_completes(self):
        verifier = Verifier(IntentRouter().route("Open Spotify"))
        verifier.record(name="open_app", state="VERIFIED", mutating=True)
        result = verifier.assess()
        self.assertTrue(result.terminal)
        self.assertEqual(result.outcome, "VERIFIED")

    def test_dispatched_action_reports_limited_observability(self):
        verifier = Verifier(IntentRouter().route("Send a notification"))
        verifier.record(name="send_notification", state="DISPATCHED", mutating=True)
        result = verifier.assess()
        self.assertTrue(result.terminal)
        self.assertEqual(result.outcome, "DISPATCHED")

    def test_local_research_requires_tool_evidence(self):
        verifier = Verifier(IntentRouter().route("Search my repo for config"))
        self.assertFalse(verifier.assess().terminal)
        verifier.record(name="knowledge_search", state="COMPLETED", mutating=False)
        self.assertTrue(verifier.assess().terminal)


if __name__ == "__main__":
    unittest.main()
