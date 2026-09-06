#!/usr/bin/env python3
"""DeepSeek agentic helper for ai-sidebar.

Reads JSON commands from stdin, writes JSON events to stdout.
Commands: {"cmd":"chat","id":"...","messages":[...],"model":"...","thinking":bool}
          {"cmd":"cancel","id":"..."}
          {"cmd":"save","id":"...","messages":[...],"api_messages":[...]}
          {"cmd":"load_last"}
          {"cmd":"list_sessions"}
          {"cmd":"clear_history"}
Events:   {"type":"status","id":"...","state":"thinking|streaming","model":"..."}
          {"type":"token","id":"...","text":"..."}
          {"type":"tool_call","id":"...","call_id":"...","name":"...","inputText":"..."}
          {"type":"tool_result","id":"...","call_id":"...","output":"...","error":bool}
          {"type":"done","id":"...","api_messages":[...]}
          {"type":"error","id":"...","message":"..."}
          {"type":"session_loaded","session_id":"...","messages":[...],"api_messages":[...]}
          {"type":"sessions","list":[...]}
          {"type":"saved","path":"..."}
          {"type":"cleared","count":N}
"""

import asyncio
import json
import os
import sys
import threading
import tomllib
import copy
import time
from pathlib import Path

import httpx

import knowledge
import lockdown_client
import metrics
import research_helper
import desktop_context
from capabilities import system_prompt
from desktop_tools import (
    DESKTOP_TOOLS, DESKTOP_TOOL_NAMES, execute as execute_desktop_tool,
    result_state as desktop_result_state,
)
from conversation_store import ConversationStore
from cloud_preview import CloudPreviewBroker
from orchestrator import AtlasOrchestrator, Verifier
from tool_policy import ApprovalBroker, is_mutating, parse_tool_arguments, requires_approval
import user_profile
import vectordb

ORCHESTRATOR = AtlasOrchestrator()

def _lockdown_call(method: str, path: str, body: dict | None = None) -> tuple[dict, bool]:
    return lockdown_client.request(method, path, body)

CONFIG_PATH = Path.home() / ".config/ai-sidebar/config.toml"
HISTORY_DIR = Path.home() / ".local/share/ai-sidebar/sessions"
CONVERSATION_DB = Path.home() / ".local/share/ai-sidebar/conversations.sqlite3"
_conversation_store: ConversationStore | None = None
DEEPSEEK_BASE = "https://api.deepseek.com"
# Bounded room for action → verify → inspect failure → correct → re-verify workflows.
MAX_TOOL_ITERATIONS = 8
OUTPUT_TRUNCATE = 8000


def voice_may_authorize_sensitive(transcript_assessment: str) -> bool:
    return transcript_assessment != "UNCERTAIN"


def _assistant_history_message(content: str, reasoning_content: str,
                               tool_calls: list[dict] | None = None) -> dict:
    """Preserve DeepSeek thinking history exactly across tool-enabled requests."""
    message = {"role": "assistant", "content": content or ""}
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return message

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file within configured knowledge roots. Use start_line/end_line for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path within a configured knowledge root"},
                    "start_line": {"type": "integer", "description": "First line (1-indexed, optional)"},
                    "end_line": {"type": "integer", "description": "Last line (1-indexed, optional)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List directory contents within configured knowledge roots.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default: home)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lockdown_status",
            "description": "Get current lockdown state and list of available monitors. Call this before lockdown_start to check monitor count and build the monitor question for the user.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lockdown_start",
            "description": "Start a Hyprland focus lockdown session. Enforces the app allowlist and browser domain allowlist until the timer expires or lockdown_end is called. Firefox is auto-added to allowed_apps when primary_url is set.",
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_seconds": {
                        "type": "integer",
                        "description": "Session length in seconds. Parse natural language: '1 hour'=3600, '90 minutes'=5400, '45 min'=2700."
                    },
                    "primary_target": {
                        "type": "string",
                        "description": "Human-readable target name, e.g. 'LeetCode', 'VSCode', 'Writing'."
                    },
                    "primary_url": {
                        "type": "string",
                        "description": "URL to open in Firefox when the session starts (website-based focus). Empty string if app-based."
                    },
                    "primary_app": {
                        "type": "string",
                        "description": "App command/name to launch (app-based focus, e.g. 'code'). Empty string if URL-based."
                    },
                    "allowed_apps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Hyprland window class names allowed during the session, e.g. ['kitty', 'code']. Firefox is added automatically when primary_url is set."
                    },
                    "allowed_domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Browser domains allowed (e.g. ['leetcode.com', 'docs.python.org']). Subdomains are automatically permitted."
                    },
                    "monitor_to_disable": {
                        "type": "string",
                        "description": "Monitor name to disable for the session (from lockdown_status monitors list). Empty string to keep all monitors active."
                    }
                },
                "required": ["duration_seconds", "primary_target", "allowed_apps", "allowed_domains"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lockdown_exception",
            "description": "Add an exception to the active lockdown session, granting access to an additional app or browser domain. Use when the user requests access to something during a session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "App window class name or browser domain to allow (e.g. 'spotify', 'open.spotify.com')."
                    }
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lockdown_end",
            "description": "End the active lockdown session, restore any disabled monitor, and clear all enforcement.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_search",
            "description": "Search the user's own curated files (documents, notes, projects, configs) for text matching a query. This is your memory of the user's system — use it whenever a question might be answered by their files, or to ground answers in what they actually have. Returns matching path:line: snippets; follow up with read_file for full context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Regex/keyword to search for across indexed personal dirs."},
                    "path_filter": {"type": "string", "description": "Optional: limit to a root or subpath (e.g. 'atlas', '~/Documents')."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_list",
            "description": "List the files available in the user's indexed personal dirs (a manifest). Use to discover what exists before searching, or to see the layout of a project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path_filter": {"type": "string", "description": "Optional: limit to a root or subpath (e.g. 'projects')."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": "Save a durable fact you've learned about the USER to their long-term profile (injected into every future chat). Use for stable facts: identity, background, preferences, working style, recurring projects, tools, environment, goals. Do NOT use for one-off task details or transient state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The durable fact, phrased as a concise statement about the user."},
                    "category": {"type": "string", "description": "One of: Identity, Preferences, Projects, Environment, Goals, Other."}
                },
                "required": ["fact"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "Search your long-term memory of PAST CONVERSATIONS with the user (semantic, by meaning). Use it to recall things discussed earlier — decisions, plans, preferences, ongoing topics, things the user told you before — whenever the question refers to the past or continuity would help. This is different from knowledge_search, which searches the user's files. The most relevant recent memories are already auto-injected each turn; call this to dig deeper or recall something specific.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to recall, in natural language."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory_forget",
            "description": "Delete memories of past conversations. SAFETY: always call FIRST with confirm=false to preview which memories match, show them to the user, and get their explicit yes. Only then call again with confirm=true to actually delete. Use when the user asks you to forget/delete something you discussed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to forget, in natural language (matched semantically)."},
                    "confirm": {"type": "boolean", "description": "false = preview matches only (default); true = actually delete the matches."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "forget_fact",
            "description": "Remove a durable fact from the user's long-term PROFILE (the 'what you know about the user' facts). SAFETY: call FIRST with confirm=false to preview which profile facts match, confirm with the user, then call again with confirm=true. Use when the user says a saved fact about them is wrong or should be removed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The fact (or a distinctive part of it) to remove."},
                    "confirm": {"type": "boolean", "description": "false = preview matches only (default); true = actually remove."}
                },
                "required": ["fact"]
            }
        }
    }
]
TOOLS.extend(DESKTOP_TOOLS)


def get_api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "rb") as f:
                cfg = tomllib.load(f)
            return cfg.get("api_key", "").strip()
        except Exception:
            pass
    return ""


def emit(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def _format_input_text(name: str, args: dict) -> str:
    if name in DESKTOP_TOOL_NAMES:
        visible = {key: value for key, value in args.items() if key not in {"text"}}
        if name == "set_clipboard":
            return f"Set clipboard text ({len(str(args.get('text', '')))} characters)"
        return f"{name.replace('_', ' ')}: {json.dumps(visible, ensure_ascii=False)}"
    if name == "read_file":
        path = args.get("path", "")
        sl, el = args.get("start_line"), args.get("end_line")
        return f"{path}:{sl}-{el}" if (sl or el) else path
    if name == "list_dir":
        return args.get("path", "~")
    if name == "lockdown_status":
        return "Check lockdown state"
    if name == "lockdown_start":
        t    = args.get("primary_target", "?")
        mins = args.get("duration_seconds", 0) // 60
        return f"Start lockdown: {t} for {mins}m"
    if name == "lockdown_exception":
        return f"Add exception: {args.get('target', '?')}"
    if name == "lockdown_end":
        return "End lockdown session"
    if name == "knowledge_search":
        pf = args.get("path_filter")
        q = args.get("query", "")
        return f'search {pf}: "{q}"' if pf else f'search: "{q}"'
    if name == "knowledge_list":
        return f"list {args.get('path_filter', 'all indexed dirs')}"
    if name == "remember_fact":
        return f"remember: {args.get('fact', '')}"
    if name == "memory_search":
        return f'recall: "{args.get("query", "")}"'
    if name == "memory_forget":
        verb = "forget" if args.get("confirm") else "preview forget"
        return f'{verb}: "{args.get("query", "")}"'
    if name == "forget_fact":
        verb = "remove fact" if args.get("confirm") else "preview remove"
        return f'{verb}: "{args.get("fact", "")}"'
    return json.dumps(args)


def _verification_output(action: str, response, evidence: str,
                         state: str = "VERIFIED") -> tuple[str, bool]:
    return json.dumps({
        "state": state,
        "action": action,
        "verification": evidence,
        "output": response,
    }, ensure_ascii=False), state == "FAILED"


def execute_tool(name: str, args: dict) -> tuple[str, bool]:
    """Returns (output, is_error). Runs synchronously — call via run_in_executor."""
    try:
        if name in DESKTOP_TOOL_NAMES:
            return execute_desktop_tool(name, args)

        if name == "read_file":
            path = knowledge.resolve_allowed_path(args["path"])
            with path.open("r", errors="replace") as f:
                lines = f.readlines()
            sl = args.get("start_line")
            el = args.get("end_line")
            start = (sl - 1) if sl else 0
            end = el if el else len(lines)
            content = "".join(lines[start:end])
            if len(content) > OUTPUT_TRUNCATE:
                content = content[:OUTPUT_TRUNCATE] + f"\n... (truncated, {len(lines)} lines total)"
            return content or "(empty file)", False

        elif name == "list_dir":
            path = knowledge.resolve_allowed_path(args.get("path"))
            entries = []
            for ename in sorted(os.listdir(path)):
                full = path / ename
                try:
                    is_dir = full.is_dir()
                    size = full.stat().st_size
                    entries.append(f"{'d' if is_dir else 'f'}  {ename}" + (
                        "/" if is_dir else f"  ({size:,}b)"
                    ))
                except OSError:
                    entries.append(f"?  {ename}")
            return "\n".join(entries) if entries else "(empty)", False

        elif name == "lockdown_status":
            status, err1 = _lockdown_call("GET", "/status")
            monitors, err2 = _lockdown_call("GET", "/monitors")
            combined = {"lockdown": status}
            if not err2:
                combined["monitors"] = monitors.get("monitors", [])
                combined["monitor_count"] = monitors.get("count", 0)
            return json.dumps(combined, indent=2), err1  # status failure is authoritative

        elif name == "lockdown_start":
            result, err = _lockdown_call("POST", "/start", args)
            if err:
                return _verification_output(name, result, "Lockdown start request failed", "FAILED")
            status, status_err = _lockdown_call("GET", "/status")
            verified = (not status_err and status.get("state") == "ACTIVE"
                        and status.get("target") == args.get("primary_target"))
            return _verification_output(
                name, result,
                f"Lockdown status is {status.get('state')} for {status.get('target')}",
                "VERIFIED" if verified else "FAILED",
            )

        elif name == "lockdown_exception":
            result, err = _lockdown_call("POST", "/exception", args)
            if err:
                return _verification_output(name, result, "Exception request failed", "FAILED")
            status, status_err = _lockdown_call("GET", "/status")
            target = str(args.get("target", "")).lower()
            present = target in {
                str(item).lower() for item in [
                    *status.get("allowed_apps", []), *status.get("allowed_domains", [])
                ]
            }
            return _verification_output(
                name, result, f"Lockdown allowlists contain target={present}",
                "VERIFIED" if not status_err and present else "FAILED",
            )

        elif name == "lockdown_end":
            result, err = _lockdown_call("POST", "/end", {})
            if err:
                return _verification_output(name, result, "Lockdown end request failed", "FAILED")
            status, status_err = _lockdown_call("GET", "/status")
            verified = not status_err and status.get("state") == "IDLE"
            return _verification_output(
                name, result, f"Lockdown status is {status.get('state')}",
                "VERIFIED" if verified else "FAILED",
            )

        elif name == "knowledge_search":
            out = knowledge.search(args["query"], args.get("path_filter"))
            return out, False

        elif name == "knowledge_list":
            out = knowledge.list_files(args.get("path_filter"))
            return out, False

        elif name == "remember_fact":
            fact = args.get("fact", "")
            added = user_profile.add_fact(
                args.get("category", "Other"), fact
            )
            verified = bool(user_profile.find_facts(fact))
            message = f"Saved to profile: {fact}" if added else "Already known — not duplicated."
            return _verification_output(
                name, message, f"Profile read-back contains the fact={verified}",
                "VERIFIED" if verified else "FAILED",
            )

        elif name == "memory_search":
            hits = vectordb.search(args.get("query", ""), k=6)
            if not hits:
                return "No relevant past conversations found.", False
            lines = []
            for h in hits:
                ts = (h.get("meta") or {}).get("ts", "")[:10]
                lines.append((f"[{ts}] " if ts else "") + h["text"])
            return "\n\n".join(lines), False

        elif name == "memory_forget":
            matches = vectordb.search(args.get("query", ""), k=5, max_distance=0.75)
            if not matches:
                return "No matching memories found — nothing to forget.", False
            if not bool(args.get("confirm", False)):
                preview = "\n".join(f"- {m['text'][:140]}" for m in matches)
                return (f"PREVIEW — {len(matches)} memory item(s) match. Show these to the user "
                        f"and, only if they confirm, call memory_forget again with confirm=true:\n"
                        f"{preview}"), False
            n = vectordb.delete([m["id"] for m in matches])
            remaining = vectordb.existing_ids([m["id"] for m in matches])
            return _verification_output(
                name, f"Deleted {n} memory item(s).",
                f"Memory read-back found {len(remaining)} deleted ID(s)",
                "VERIFIED" if not remaining else "FAILED",
            )

        elif name == "forget_fact":
            matches = user_profile.find_facts(args.get("fact", ""))
            if not matches:
                return "No matching profile fact found — nothing to remove.", False
            if not bool(args.get("confirm", False)):
                preview = "\n".join(f"- {m}" for m in matches)
                return (f"PREVIEW — {len(matches)} profile fact(s) match. Confirm with the user, "
                        f"then call forget_fact again with confirm=true:\n{preview}"), False
            removed = user_profile.remove_fact(args.get("fact", ""))
            remaining = user_profile.find_facts(args.get("fact", ""))
            return _verification_output(
                name, f"Removed {len(removed)} profile fact(s): " + "; ".join(removed),
                f"Profile read-back found {len(remaining)} matching fact(s)",
                "VERIFIED" if removed and not remaining else "FAILED",
            )

        else:
            return f"Unknown tool: {name}", True

    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}", True


def _result_state(name: str, output: str, is_error: bool) -> str:
    return desktop_result_state(output, is_error)


def _inject_context(api_messages: list) -> list:
    """Fold the live user profile + knowledge-index hint into the system message.

    Read fresh each turn so newly-remembered facts take effect immediately.
    """
    profile = user_profile.load_profile().strip()
    roots = knowledge.roots_summary()

    parts = [
        "\n\n# Retrieval & memory",
        "You can search the user's own files with knowledge_search (and browse "
        f"them with knowledge_list). Indexed dirs: {roots}. "
        "Prefer knowledge_search over guessing whenever a question could be "
        "answered from the user's documents, notes, configs, or projects; "
        "read_file to pull full context from a hit.",
        "You have long-term memory of past conversations — memory_search recalls "
        "things discussed before (plans, decisions, preferences, ongoing topics). "
        "Relevant memories are auto-injected below when they exist; call memory_search "
        "to dig deeper or recall something specific.",
        "Save durable facts you learn about the user with remember_fact so you "
        "know them better over time.",
    ]

    # Semantic recall: fold in memories relevant to the current question.
    last_user = next(
        (m.get("content") for m in reversed(api_messages)
         if m.get("role") == "user" and isinstance(m.get("content"), str)),
        "",
    )
    route, plan = ORCHESTRATOR.prepare(last_user)
    mems = ORCHESTRATOR.memory.recall(last_user, vectordb.search)
    if mems:
        parts.append("\n## Relevant memory from past conversations\n"
                     + "\n".join(f"- {m['text']}" for m in mems))

    if profile:
        parts.append("\n## What you already know about the user\n" + profile)

    parts.append(ORCHESTRATOR.prompt_context(route, plan))
    parts.append("\n\n" + desktop_context.compact_prompt())

    preamble = "\n".join(parts)

    if not api_messages or api_messages[0].get("role") != "system":
        api_messages = [system_prompt("sidebar"), *api_messages]

    if api_messages and api_messages[0].get("role") == "system":
        head = dict(api_messages[0])
        head["content"] = (head.get("content") or "") + preamble
        return [head] + api_messages[1:]
    return [{"role": "system", "content": preamble.lstrip()}] + api_messages


def _build_cloud_body(messages: list, model: str, thinking: bool) -> dict:
    body: dict = {
        "model": model,
        "messages": messages,
        "stream": True,
        "tools": TOOLS,
        "stream_options": {"include_usage": True},
    }
    if not thinking:
        body["tool_choice"] = "auto"
    if thinking:
        body["thinking"] = {"type": "enabled", "budget_tokens": 8192}
    return body


async def agent_loop(req_id: str, messages: list, model: str, thinking: bool,
                     cancel_event: asyncio.Event, approvals: ApprovalBroker,
                     prepared_body: dict | None = None) -> None:
    started_at = time.monotonic()
    outcome = "FAILED"
    usage: dict = {}
    tool_call_count = 0
    api_key = get_api_key()
    if not api_key:
        emit({"type": "error", "id": req_id,
              "message": "No API key. Set DEEPSEEK_API_KEY or add api_key to ~/.config/ai-sidebar/config.toml"})
        await asyncio.to_thread(
            metrics.record_request,
            request_id=req_id, model=model, outcome="NO_API_KEY",
            latency_ms=int((time.monotonic() - started_at) * 1000),
            usage=usage, tool_calls=tool_call_count,
        )
        return

    transcript_assessment = "ACCEPT"
    if prepared_body:
        prepared_body = copy.deepcopy(prepared_body)
        transcript_assessment = prepared_body.pop("_atlas_transcript_assessment", "ACCEPT")
        model = prepared_body["model"]
        thinking = "thinking" in prepared_body

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    api_messages = copy.deepcopy(prepared_body["messages"]) if prepared_body else _inject_context(list(messages))
    objective = next(
        (m.get("content", "") for m in reversed(api_messages)
         if m.get("role") == "user" and isinstance(m.get("content"), str)),
        "",
    )
    route, plan = ORCHESTRATOR.prepare(objective)
    verifier = Verifier(route, objective)
    defer_output = route.needs_action or (route.needs_research and not route.needs_web_research)
    emit({"type": "orchestration", "id": req_id, **ORCHESTRATOR.event(route, plan)})
    emit({"type": "status", "id": req_id, "state": "thinking", "model": model})

    try:
        for _iter in range(MAX_TOOL_ITERATIONS):
            if cancel_event.is_set():
                emit({"type": "cancelled", "id": req_id})
                return

            body = copy.deepcopy(prepared_body) if (_iter == 0 and prepared_body) else \
                _build_cloud_body(api_messages, model, thinking)

            text_content = ""
            reasoning_content = ""
            tool_calls_raw: dict[int, dict] = {}
            finish_reason = None
            first_text = True

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
            ) as client:
                async with client.stream(
                    "POST", f"{DEEPSEEK_BASE}/v1/chat/completions",
                    json=body, headers=headers
                ) as resp:
                    if resp.status_code != 200:
                        err = await resp.aread()
                        emit({"type": "error", "id": req_id,
                              "message": f"HTTP {resp.status_code}: {err.decode()[:300]}"})
                        return

                    async for raw in resp.aiter_lines():
                        if cancel_event.is_set():
                            emit({"type": "cancelled", "id": req_id})
                            return
                        if not raw.startswith("data: "):
                            continue
                        payload = raw[6:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if isinstance(chunk.get("usage"), dict):
                                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                                    usage[key] = int(usage.get(key, 0) or 0) + int(
                                        chunk["usage"].get(key, 0) or 0
                                    )
                            choices = chunk.get("choices") or []
                            if not choices:
                                continue
                            choice = choices[0]
                            delta = choice.get("delta", {})

                            reasoning = delta.get("reasoning_content") or ""
                            if reasoning:
                                reasoning_content += reasoning
                                emit({"type": "thinking_token", "id": req_id, "text": reasoning})

                            content = delta.get("content") or ""
                            if content:
                                if first_text:
                                    emit({"type": "status", "id": req_id, "state": "streaming"})
                                    first_text = False
                                text_content += content
                                if not defer_output:
                                    emit({"type": "token", "id": req_id, "text": content})

                            for tc_delta in delta.get("tool_calls", []):
                                idx = tc_delta.get("index", 0)
                                if idx not in tool_calls_raw:
                                    tool_calls_raw[idx] = {
                                        "id": tc_delta.get("id", ""),
                                        "name": (tc_delta.get("function") or {}).get("name", ""),
                                        "arguments": ""
                                    }
                                else:
                                    if tc_delta.get("id"):
                                        tool_calls_raw[idx]["id"] = tc_delta["id"]
                                    func = tc_delta.get("function") or {}
                                    if func.get("name"):
                                        tool_calls_raw[idx]["name"] = func["name"]
                                tool_calls_raw[idx]["arguments"] += (
                                    (tc_delta.get("function") or {}).get("arguments", "")
                                )

                            fr = choice.get("finish_reason")
                            if fr:
                                finish_reason = fr

                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue

            # Finished streaming this iteration
            if finish_reason == "stop" or not tool_calls_raw:
                assessment = verifier.assess()
                emit({"type": "verification", "id": req_id,
                      "outcome": assessment.outcome, "terminal": assessment.terminal,
                      "detail": assessment.feedback})
                if not assessment.terminal and _iter + 1 < MAX_TOOL_ITERATIONS:
                    api_messages.append(_assistant_history_message(
                        text_content, reasoning_content))
                    api_messages.append({
                        "role": "system",
                        "content": "Verifier feedback: " + assessment.feedback,
                    })
                    emit({"type": "status", "id": req_id, "state": "thinking", "model": model})
                    continue
                api_messages.append(_assistant_history_message(
                    text_content, reasoning_content))
                if defer_output and text_content:
                    emit({"type": "status", "id": req_id, "state": "streaming"})
                    emit({"type": "token", "id": req_id, "text": text_content})
                final_api = [m for m in api_messages if m.get("role") != "system"]
                emit({"type": "done", "id": req_id, "api_messages": final_api,
                      "verification": assessment.outcome})
                outcome = "SUCCEEDED"
                return

            # Build API assistant message with tool_calls
            tc_list = [
                {
                    "id": tool_calls_raw[i]["id"],
                    "type": "function",
                    "function": {
                        "name": tool_calls_raw[i]["name"],
                        "arguments": tool_calls_raw[i]["arguments"]
                    }
                }
                for i in sorted(tool_calls_raw)
            ]
            tool_call_count += len(tc_list)
            api_messages.append(_assistant_history_message(
                text_content, reasoning_content, tc_list))

            # Execute tools
            loop = asyncio.get_running_loop()
            for tc in tc_list:
                if cancel_event.is_set():
                    emit({"type": "cancelled", "id": req_id})
                    return

                tc_name = tc["function"]["name"]
                try:
                    tc_args = parse_tool_arguments(tc["function"]["arguments"])
                except ValueError as error:
                    output = str(error)
                    verifier.record(name=tc_name, state="FAILED", mutating=False)
                    emit({"type": "tool_result", "id": req_id, "call_id": tc["id"],
                          "output": output, "error": True, "state": "FAILED"})
                    api_messages.append({
                        "role": "tool", "tool_call_id": tc["id"], "content": output
                    })
                    continue

                input_text = _format_input_text(tc_name, tc_args)
                emit({"type": "tool_call", "id": req_id, "call_id": tc["id"],
                      "name": tc_name, "inputText": input_text})

                mutation = False
                try:
                    mutation = is_mutating(tc_name, tc_args)
                except ValueError as error:
                    output, is_error = str(error), True
                else:
                    if (not voice_may_authorize_sensitive(transcript_assessment) and
                            requires_approval(tc_name, tc_args)):
                        output, is_error = (
                            "Sensitive action rejected: uncertain voice transcript cannot authorize it",
                            True,
                        )
                    elif requires_approval(tc_name, tc_args):
                        approved = await approvals.request(
                            req_id, tc["id"], tc_name, tc_args,
                            on_pending=lambda: emit({
                                "type": "tool_approval_required", "id": req_id,
                                "call_id": tc["id"], "name": tc_name,
                                "arguments": tc_args, "inputText": input_text,
                                "expires_in_seconds": approvals.timeout_seconds,
                            }),
                        )
                        if not approved:
                            output, is_error = "Sensitive action rejected or approval expired", True
                        else:
                            output, is_error = await loop.run_in_executor(
                                None, execute_tool, tc_name, tc_args
                            )
                    else:
                        output, is_error = await loop.run_in_executor(
                            None, execute_tool, tc_name, tc_args
                        )

                result_state = _result_state(tc_name, output, is_error)
                verifier.record(name=tc_name, state=result_state, mutating=mutation)
                emit({"type": "tool_result", "id": req_id, "call_id": tc["id"],
                      "output": output, "error": is_error,
                      "state": result_state})

                api_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": output
                })

            emit({"type": "status", "id": req_id, "state": "thinking", "model": model})

        emit({"type": "error", "id": req_id,
              "message": f"Reached max tool iterations ({MAX_TOOL_ITERATIONS})"})

    except asyncio.CancelledError:
        outcome = "CANCELLED"
        emit({"type": "cancelled", "id": req_id})
    except httpx.TimeoutException:
        outcome = "TIMEOUT"
        emit({"type": "error", "id": req_id, "message": "Request timed out"})
    except httpx.NetworkError as exc:
        outcome = "NETWORK_ERROR"
        emit({"type": "error", "id": req_id, "message": f"Network error: {exc}"})
    except Exception as exc:
        emit({"type": "error", "id": req_id, "message": str(exc)})
    finally:
        await asyncio.to_thread(
            metrics.record_request,
            request_id=req_id, model=model, outcome=outcome,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            usage=usage, tool_calls=tool_call_count,
        )


def _conversations() -> ConversationStore:
    global _conversation_store
    if _conversation_store is None:
        _conversation_store = ConversationStore(CONVERSATION_DB, HISTORY_DIR)
    return _conversation_store


def save_session(session_id: str, messages: list, api_messages: list | None = None) -> str:
    _conversations().save(session_id, messages, api_messages)
    return str(CONVERSATION_DB)


def list_sessions() -> list:
    return _conversations().list_sessions()


def clear_history() -> int:
    return _conversations().clear()


async def main() -> None:
    loop = asyncio.get_running_loop()
    line_queue: asyncio.Queue[str | None] = asyncio.Queue()

    # Warm the semantic-memory embedder in the background so the first chat's
    # auto-recall doesn't block while the model loads.
    threading.Thread(target=lambda: vectordb.search("warmup"), daemon=True).start()
    desktop_context.STORE.start_event_listener()

    def _reader_thread() -> None:
        buf = b""
        try:
            stdin_bin = sys.stdin.buffer
            while True:
                chunk = stdin_bin.read1(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    s = line.strip().decode()
                    if s:
                        loop.call_soon_threadsafe(line_queue.put_nowait, s)
        except Exception:
            pass
        finally:
            loop.call_soon_threadsafe(line_queue.put_nowait, None)

    threading.Thread(target=_reader_thread, daemon=True).start()

    active_task: asyncio.Task | None = None
    cancel_event = asyncio.Event()
    approvals = ApprovalBroker()
    cloud_previews = CloudPreviewBroker()

    while True:
        try:
            line = await line_queue.get()
            if line is None:
                break
            if not line:
                continue

            req = json.loads(line)
            cmd = req.get("cmd")

            if cmd == "preview_cloud":
                messages = list(req.get("messages", []))
                objective = next(
                    (m.get("content", "") for m in reversed(messages)
                     if m.get("role") == "user" and isinstance(m.get("content"), str)),
                    "",
                )
                route, plan = ORCHESTRATOR.prepare(objective)
                emit({"type": "orchestration", "id": req["id"],
                      **ORCHESTRATOR.event(route, plan)})
                research_only = (route.needs_web_research and not route.needs_action
                                 and not route.needs_memory)
                if research_only:
                    preview_body = research_helper.build_query_body(
                        objective, req.get("model", "deepseek-v4-flash")
                    )
                    prepared = {
                        "_atlas_kind": "research",
                        "question": objective,
                        "model": req.get("model", "deepseek-v4-flash"),
                        "messages": messages,
                        "preview_body": preview_body,
                    }
                    visible_payload = preview_body
                else:
                    prepared = await asyncio.to_thread(
                        lambda: _build_cloud_body(
                            _inject_context(messages),
                            req.get("model", "deepseek-v4-flash"),
                            req.get("thinking", False),
                        )
                    )
                    visible_payload = copy.deepcopy(prepared)
                    prepared["_atlas_transcript_assessment"] = req.get(
                        "transcript_assessment", "ACCEPT")
                token = cloud_previews.issue(req["id"], prepared)
                emit({
                    "type": "cloud_preview", "id": req["id"], "token": token,
                    "payload": json.dumps(visible_payload, ensure_ascii=False, indent=2),
                    "expires_in_seconds": cloud_previews.ttl_seconds,
                })

            elif cmd == "chat_approved":
                prepared = cloud_previews.consume(req.get("id", ""), req.get("token", ""))
                if prepared is None:
                    emit({"type": "error", "id": req.get("id", "system"),
                          "message": "Cloud preview expired or was already used"})
                    continue
                if active_task and not active_task.done():
                    cancel_event.set()
                    active_task.cancel()
                    try:
                        await asyncio.wait_for(active_task, timeout=2.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                cancel_event = asyncio.Event()
                if prepared.get("_atlas_kind") == "research":
                    active_task = asyncio.create_task(
                        research_helper.research_pipeline(
                            req["id"], prepared["question"], prepared["model"], cancel_event,
                            prepared.get("messages"),
                        )
                    )
                else:
                    active_task = asyncio.create_task(
                        agent_loop(
                            req["id"],
                            [],
                            req.get("model", "deepseek-v4-flash"),
                            req.get("thinking", False),
                            cancel_event,
                            approvals,
                            prepared,
                        )
                    )

            elif cmd == "discard_cloud_preview":
                cloud_previews.discard(req.get("token", ""))

            elif cmd == "chat":
                emit({"type": "error", "id": req.get("id", "system"),
                      "message": "Cloud payload preview and approval are required"})

            elif cmd == "cancel":
                cancel_event.set()

            elif cmd == "tool_approval":
                try:
                    args = req.get("arguments", {})
                    accepted = approvals.resolve(
                        req["id"], req["call_id"], req["name"], args,
                        req.get("approved") is True,
                    )
                    if not accepted:
                        emit({"type": "error", "id": req.get("id", "system"),
                              "message": "Approval was duplicate, expired, or did not match"})
                except (KeyError, TypeError, ValueError) as error:
                    emit({"type": "error", "id": req.get("id", "system"),
                          "message": f"Invalid approval: {error}"})

            elif cmd == "save":
                try:
                    path = save_session(
                        req["id"],
                        req.get("messages", []),
                        req.get("api_messages")
                    )
                    emit({"type": "saved", "path": path})
                except Exception as exc:
                    emit({"type": "error", "id": "system", "message": f"Save failed: {exc}"})

            elif cmd == "load_last":
                limit = req.get("limit", 100)
                data = _conversations().load_last(limit)
                if data:
                    emit({
                        "type": "session_loaded",
                        "session_id": data["id"],
                        "messages": data["messages"],
                        "api_messages": data["api_messages"],
                    })
                else:
                    emit({"type": "session_loaded", "session_id": "",
                          "messages": [], "api_messages": []})

            elif cmd == "list_sessions":
                emit({"type": "sessions", "list": list_sessions()})

            elif cmd == "clear_history":
                count = clear_history()
                emit({"type": "cleared", "count": count})

        except json.JSONDecodeError:
            pass
        except Exception as exc:
            emit({"type": "error", "id": "system", "message": str(exc)})


if __name__ == "__main__":
    asyncio.run(main())
