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
import datetime
import json
import os
import subprocess
import sys
import threading
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import httpx

import knowledge
import user_profile
import vectordb

LOCKDOWN_API = "http://127.0.0.1:8767"


def _lockdown_call(method: str, path: str, body: dict | None = None) -> tuple[dict, bool]:
    url = LOCKDOWN_API + path
    try:
        data = json.dumps(body).encode() if body is not None else None
        req  = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read()), False
    except urllib.error.URLError as e:
        return {"error": f"Lockdown daemon not reachable ({e}). Start it with: python ~/ai-sidebar/lockdown/daemon.py"}, True
    except Exception as e:
        return {"error": str(e)}, True

CONFIG_PATH = Path.home() / ".config/ai-sidebar/config.toml"
HISTORY_DIR = Path.home() / ".local/share/ai-sidebar/sessions"
DEEPSEEK_BASE = "https://api.deepseek.com"
MAX_TOOL_ITERATIONS = 20
OUTPUT_TRUNCATE = 8000

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute any shell command via bash -c. Returns stdout+stderr. Use for running programs, git, package managers, system info, file operations, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "timeout": {"type": "integer", "description": "Timeout seconds (default 30, max 120)"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file's contents. Use start_line/end_line for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or ~ path"},
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
            "name": "write_file",
            "description": "Write content to a file, creating it and parent dirs if needed. Overwrites existing files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or ~ path"},
                    "content": {"type": "string", "description": "Content to write"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List directory contents with file types and sizes.",
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
    if name == "bash":
        return "$ " + args.get("command", "")
    if name == "read_file":
        path = args.get("path", "")
        sl, el = args.get("start_line"), args.get("end_line")
        return f"{path}:{sl}-{el}" if (sl or el) else path
    if name == "write_file":
        return args.get("path", "")
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


def execute_tool(name: str, args: dict) -> tuple[str, bool]:
    """Returns (output, is_error). Runs synchronously — call via run_in_executor."""
    try:
        if name == "bash":
            cmd = args.get("command", "")
            timeout = min(int(args.get("timeout", 30)), 120)
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, env={**os.environ}
            )
            out = result.stdout + result.stderr
            if not out.strip():
                out = f"(exit {result.returncode})"
            if len(out) > OUTPUT_TRUNCATE:
                out = out[:OUTPUT_TRUNCATE] + f"\n... (truncated, {len(out)} total chars)"
            return out, result.returncode != 0

        elif name == "read_file":
            path = os.path.expanduser(args["path"])
            with open(path, "r", errors="replace") as f:
                lines = f.readlines()
            sl = args.get("start_line")
            el = args.get("end_line")
            start = (sl - 1) if sl else 0
            end = el if el else len(lines)
            content = "".join(lines[start:end])
            if len(content) > OUTPUT_TRUNCATE:
                content = content[:OUTPUT_TRUNCATE] + f"\n... (truncated, {len(lines)} lines total)"
            return content or "(empty file)", False

        elif name == "write_file":
            path = os.path.expanduser(args["path"])
            content = args["content"]
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return f"Written {len(content)} chars to {path}", False

        elif name == "list_dir":
            path = os.path.expanduser(args.get("path", "~"))
            entries = []
            for ename in sorted(os.listdir(path)):
                full = os.path.join(path, ename)
                try:
                    is_dir = os.path.isdir(full)
                    size = os.stat(full).st_size
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
            return json.dumps(result), err

        elif name == "lockdown_exception":
            result, err = _lockdown_call("POST", "/exception", args)
            return json.dumps(result), err

        elif name == "lockdown_end":
            result, err = _lockdown_call("POST", "/end", {})
            return json.dumps(result), err

        elif name == "knowledge_search":
            out = knowledge.search(args["query"], args.get("path_filter"))
            return out, False

        elif name == "knowledge_list":
            out = knowledge.list_files(args.get("path_filter"))
            return out, False

        elif name == "remember_fact":
            added = user_profile.add_fact(
                args.get("category", "Other"), args.get("fact", "")
            )
            if added:
                return f"Saved to profile: {args.get('fact', '')}", False
            return "Already known — not duplicated.", False

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
            return f"Deleted {n} memory item(s).", False

        elif name == "forget_fact":
            matches = user_profile.find_facts(args.get("fact", ""))
            if not matches:
                return "No matching profile fact found — nothing to remove.", False
            if not bool(args.get("confirm", False)):
                preview = "\n".join(f"- {m}" for m in matches)
                return (f"PREVIEW — {len(matches)} profile fact(s) match. Confirm with the user, "
                        f"then call forget_fact again with confirm=true:\n{preview}"), False
            removed = user_profile.remove_fact(args.get("fact", ""))
            return f"Removed {len(removed)} profile fact(s): " + "; ".join(removed), False

        else:
            return f"Unknown tool: {name}", True

    except subprocess.TimeoutExpired:
        return f"Error: timed out after {args.get('timeout', 30)}s", True
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}", True


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
    try:
        mems = vectordb.search(last_user, k=4) if last_user else []
    except Exception:
        mems = []
    if mems:
        parts.append("\n## Relevant memory from past conversations\n"
                     + "\n".join(f"- {m['text']}" for m in mems))

    if profile:
        parts.append("\n## What you already know about the user\n" + profile)

    preamble = "\n".join(parts)

    if api_messages and api_messages[0].get("role") == "system":
        head = dict(api_messages[0])
        head["content"] = (head.get("content") or "") + preamble
        return [head] + api_messages[1:]
    return [{"role": "system", "content": preamble.lstrip()}] + api_messages


async def agent_loop(req_id: str, messages: list, model: str, thinking: bool,
                     cancel_event: asyncio.Event) -> None:
    api_key = get_api_key()
    if not api_key:
        emit({"type": "error", "id": req_id,
              "message": "No API key. Set DEEPSEEK_API_KEY or add api_key to ~/.config/ai-sidebar/config.toml"})
        return

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    api_messages = _inject_context(list(messages))
    emit({"type": "status", "id": req_id, "state": "thinking", "model": model})

    try:
        for _iter in range(MAX_TOOL_ITERATIONS):
            if cancel_event.is_set():
                emit({"type": "cancelled", "id": req_id})
                return

            body: dict = {
                "model": model,
                "messages": api_messages,
                "stream": True,
                "tools": TOOLS,
                "tool_choice": "auto",
            }
            if thinking:
                body["thinking"] = {"type": "enabled", "budget_tokens": 8192}

            text_content = ""
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
                            choice = chunk["choices"][0]
                            delta = choice.get("delta", {})

                            reasoning = delta.get("reasoning_content") or ""
                            if reasoning:
                                emit({"type": "thinking_token", "id": req_id, "text": reasoning})

                            content = delta.get("content") or ""
                            if content:
                                if first_text:
                                    emit({"type": "status", "id": req_id, "state": "streaming"})
                                    first_text = False
                                text_content += content
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
                api_messages.append({"role": "assistant", "content": text_content})
                final_api = [m for m in api_messages if m.get("role") != "system"]
                emit({"type": "done", "id": req_id, "api_messages": final_api})
                # Store this exchange in long-term semantic memory (fire-and-forget).
                try:
                    last_user = next(
                        (m.get("content") for m in reversed(messages)
                         if m.get("role") == "user" and isinstance(m.get("content"), str)),
                        "",
                    )
                    if last_user and text_content.strip():
                        exchange = f"User: {last_user.strip()}\nATLAS: {text_content.strip()}"
                        asyncio.get_running_loop().run_in_executor(
                            None, vectordb.add, exchange, "chat")
                except Exception:
                    pass
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
            api_messages.append({
                "role": "assistant",
                "content": text_content or None,
                "tool_calls": tc_list
            })

            # Execute tools
            loop = asyncio.get_running_loop()
            for tc in tc_list:
                if cancel_event.is_set():
                    emit({"type": "cancelled", "id": req_id})
                    return

                tc_name = tc["function"]["name"]
                try:
                    tc_args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    tc_args = {}

                input_text = _format_input_text(tc_name, tc_args)
                emit({"type": "tool_call", "id": req_id, "call_id": tc["id"],
                      "name": tc_name, "inputText": input_text})

                output, is_error = await loop.run_in_executor(
                    None, execute_tool, tc_name, tc_args
                )

                emit({"type": "tool_result", "id": req_id, "call_id": tc["id"],
                      "output": output, "error": is_error})

                api_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": output
                })

            emit({"type": "status", "id": req_id, "state": "thinking", "model": model})

        emit({"type": "error", "id": req_id,
              "message": f"Reached max tool iterations ({MAX_TOOL_ITERATIONS})"})

    except asyncio.CancelledError:
        emit({"type": "cancelled", "id": req_id})
    except httpx.TimeoutException:
        emit({"type": "error", "id": req_id, "message": "Request timed out"})
    except httpx.NetworkError as exc:
        emit({"type": "error", "id": req_id, "message": f"Network error: {exc}"})
    except Exception as exc:
        emit({"type": "error", "id": req_id, "message": str(exc)})


def save_session(session_id: str, messages: list, api_messages: list | None = None) -> str:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / f"{session_id}.json"
    with open(path, "w") as f:
        json.dump({
            "id": session_id,
            "created_at": datetime.datetime.now().isoformat(),
            "messages": messages,
            "api_messages": api_messages if api_messages is not None else messages,
        }, f, ensure_ascii=False, indent=2)
    return str(path)


def list_sessions() -> list:
    if not HISTORY_DIR.exists():
        return []
    results = []
    for p in sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:50]:
        try:
            with open(p) as f:
                data = json.load(f)
            preview = next(
                (m["content"][:80] for m in data.get("messages", [])
                 if m.get("role") == "user" and isinstance(m.get("content"), str)),
                ""
            )
            results.append({
                "id": data.get("id", p.stem),
                "created_at": data.get("created_at", ""),
                "preview": preview,
            })
        except Exception:
            pass
    return results


def clear_history() -> int:
    if not HISTORY_DIR.exists():
        return 0
    count = 0
    for p in HISTORY_DIR.glob("*.json"):
        p.unlink(missing_ok=True)
        count += 1
    return count


async def run_extract(session_id: str, messages: list) -> None:
    """Background: learn durable facts about the user from the latest exchange."""
    key = get_api_key()
    if not key:
        return
    try:
        added = await user_profile.extract(session_id, messages, key)
        if added:
            emit({"type": "profile_updated", "facts": added})
    except Exception:
        pass


async def main() -> None:
    loop = asyncio.get_running_loop()
    line_queue: asyncio.Queue[str | None] = asyncio.Queue()

    # Warm the semantic-memory embedder in the background so the first chat's
    # auto-recall doesn't block while the model loads.
    threading.Thread(target=lambda: vectordb.search("warmup"), daemon=True).start()

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

    while True:
        try:
            line = await line_queue.get()
            if line is None:
                break
            if not line:
                continue

            req = json.loads(line)
            cmd = req.get("cmd")

            if cmd == "chat":
                if active_task and not active_task.done():
                    cancel_event.set()
                    active_task.cancel()
                    try:
                        await asyncio.wait_for(active_task, timeout=2.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                cancel_event = asyncio.Event()
                active_task = asyncio.create_task(
                    agent_loop(
                        req["id"],
                        req["messages"],
                        req.get("model", "deepseek-v4-flash"),
                        req.get("thinking", False),
                        cancel_event,
                    )
                )

            elif cmd == "cancel":
                cancel_event.set()

            elif cmd == "save":
                try:
                    path = save_session(
                        req["id"],
                        req.get("messages", []),
                        req.get("api_messages")
                    )
                    emit({"type": "saved", "path": path})
                    asyncio.create_task(
                        run_extract(req["id"], req.get("messages", []))
                    )
                except Exception as exc:
                    emit({"type": "error", "id": "system", "message": f"Save failed: {exc}"})

            elif cmd == "load_last":
                limit = req.get("limit", 100)
                loaded = False
                if HISTORY_DIR.exists():
                    for candidate in sorted(HISTORY_DIR.glob("*.json"), reverse=True):
                        try:
                            with open(candidate) as f:
                                data = json.load(f)
                            msgs = data.get("messages", [])[-limit:]
                            api_msgs = data.get("api_messages", msgs)[-limit:]
                            emit({
                                "type": "session_loaded",
                                "session_id": data.get("id", candidate.stem),
                                "messages": msgs,
                                "api_messages": api_msgs,
                            })
                            loaded = True
                            break
                        except Exception:
                            continue
                if not loaded:
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
