"""Internal ATLAS role orchestration; the user always talks to one ATLAS."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Callable


class Intent(str, Enum):
    CONVERSATION = "conversation"
    ACTION = "action"
    RESEARCH = "research"
    MEMORY = "memory"
    COMPOSITE = "composite"


@dataclass(frozen=True)
class Route:
    intent: Intent
    needs_action: bool
    needs_research: bool
    needs_web_research: bool
    needs_memory: bool


@dataclass(frozen=True)
class PlanStep:
    owner: str
    objective: str
    success: str


@dataclass(frozen=True)
class VerificationAssessment:
    terminal: bool
    outcome: str
    feedback: str


class IntentRouter:
    _ACTION = re.compile(
        r"\b(open|close|focus|move|rename|switch|set|play|pause|launch|run|commit|"
        r"lock|shutdown|restart|reboot|start|stop|delete|remove|copy|send|notify|"
        r"notification|remember|forget)\b", re.I)
    _RESEARCH = re.compile(
        r"\b(research|investigate|look up|search|latest|current|today|news|sources?|"
        r"documentation|docs|compare|fact.?check)\b", re.I)
    _LOCAL_RESEARCH = re.compile(
        r"\b(my files?|codebase|repository|repo|project|document|notes?|config)\b", re.I)
    _MEMORY = re.compile(
        r"\b(remember|forget|recall|memory|what do you know about me)\b", re.I)

    def route(self, objective: str) -> Route:
        text = objective.strip()
        action = bool(self._ACTION.search(text))
        research = bool(self._RESEARCH.search(text) or self._LOCAL_RESEARCH.search(text))
        memory = bool(self._MEMORY.search(text))
        web = bool(self._RESEARCH.search(text)) and not bool(self._LOCAL_RESEARCH.search(text))
        active = sum((action, research, memory))
        intent = (Intent.COMPOSITE if active > 1 else Intent.ACTION if action else
                  Intent.RESEARCH if research else Intent.MEMORY if memory else
                  Intent.CONVERSATION)
        return Route(intent, action, research, web, memory)


class Planner:
    def plan(self, objective: str, route: Route) -> list[PlanStep]:
        steps = [PlanStep("Planner", f"Resolve the objective: {objective}",
                          "A bounded executable/evidence plan exists")]
        if route.needs_research:
            source = "web sources" if route.needs_web_research else "documents and files"
            steps.append(PlanStep("Researcher", f"Gather evidence from {source}",
                                  "Relevant evidence is available to the executor"))
        if route.needs_memory:
            steps.append(PlanStep("Memory Manager", "Retrieve or propose durable memory changes",
                                  "Memory is relevant and writes remain approval-gated"))
        if route.needs_action:
            steps.append(PlanStep("Executor", "Use the narrowest typed tools needed",
                                  "Each requested state change has a tool result"))
        elif not route.needs_web_research:
            steps.append(PlanStep("Executor", "Produce an evidence-grounded answer",
                                  "The response addresses the objective"))
        steps.append(PlanStep("Verifier", "Evaluate evidence against the objective",
                              "Outcome is VERIFIED, FAILED, or explicitly DISPATCHED"))
        return steps


class MemoryManager:
    def recall(self, objective: str, search: Callable[..., list[dict]]) -> list[dict]:
        if not objective:
            return []
        try:
            return search(objective, k=4)
        except Exception:
            return []


class Verifier:
    def __init__(self, route: Route):
        self.route = route
        self.results: list[dict] = []

    def record(self, *, name: str, state: str, mutating: bool) -> None:
        self.results.append({"name": name, "state": state, "mutating": mutating})

    def assess(self) -> VerificationAssessment:
        if self.route.needs_action:
            actions = [result for result in self.results if result["mutating"]]
            if not actions:
                return VerificationAssessment(
                    False, "FAILED",
                    "No state-changing tool evidence exists. Execute the requested action; do not claim success.",
                )
            latest_by_action = {result["name"]: result for result in actions}
            failures = [result for result in latest_by_action.values()
                        if result["state"] == "FAILED"]
            if failures:
                return VerificationAssessment(
                    False, "FAILED",
                    "Action verification failed for "
                    + ", ".join(result["name"] for result in failures)
                    + ". Inspect the evidence and try a bounded alternative.",
                )
            dispatched = [result for result in latest_by_action.values()
                          if result["state"] == "DISPATCHED"]
            if dispatched:
                return VerificationAssessment(
                    True, "DISPATCHED",
                    "One or more requests were dispatched but their final external state was not observable; report that limitation.",
                )
            return VerificationAssessment(True, "VERIFIED", "Every executed action has postcondition evidence.")

        if self.route.needs_research and not self.route.needs_web_research:
            evidence = [result for result in self.results
                        if not result["mutating"] and result["state"] != "FAILED"]
            if not evidence:
                return VerificationAssessment(
                    False, "FAILED",
                    "No research tool evidence exists. Use the file/knowledge tools before answering.",
                )
        return VerificationAssessment(True, "VERIFIED", "The response satisfies the routed objective.")


class AtlasOrchestrator:
    def __init__(self):
        self.router = IntentRouter()
        self.planner = Planner()
        self.memory = MemoryManager()

    def prepare(self, objective: str) -> tuple[Route, list[PlanStep]]:
        route = self.router.route(objective)
        return route, self.planner.plan(objective, route)

    @staticmethod
    def event(route: Route, plan: list[PlanStep]) -> dict:
        return {
            "intent": route.intent.value,
            "agents": list(dict.fromkeys(step.owner for step in plan)),
            "plan": [asdict(step) for step in plan],
        }

    @staticmethod
    def prompt_context(route: Route, plan: list[PlanStep]) -> str:
        lines = ["\n\n# Internal orchestration", f"Intent: {route.intent.value}"]
        lines.extend(
            f"- {index}. {step.owner}: {step.objective} (done when: {step.success})"
            for index, step in enumerate(plan, 1)
        )
        lines.append("Follow this internal plan silently. The user speaks only to ATLAS, not to role names.")
        return "\n".join(lines)
