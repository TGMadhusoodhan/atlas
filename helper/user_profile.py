#!/usr/bin/env python3
"""Long-term user profile ('learn about me') for ai-sidebar.

Stores durable facts the assistant learns about the user across sessions in a
single markdown file, which is injected into the system prompt every turn.
Updated two ways:
  * remember_fact tool  — the model saves something explicitly, mid-chat
  * background extraction — after each session save, a cheap flash call pulls
    durable facts from the newly-added messages and merges them in.

Named user_profile (not profile) to avoid shadowing the stdlib profile module,
since helper/ is first on sys.path.
"""

import asyncio
import datetime
import json
import re
from pathlib import Path

import httpx

PROFILE_DIR = Path.home() / ".local/share/ai-sidebar/profile"
PROFILE_PATH = PROFILE_DIR / "profile.md"
DEEPSEEK_BASE = "https://api.deepseek.com"
EXTRACT_MODEL = "deepseek-v4-flash"

SECTIONS = ["Identity", "Preferences", "Projects", "Environment", "Goals", "Other"]

# Serialises profile writes; tracks how many messages per session are already
# extracted so we never re-process the whole conversation each save.
_lock = asyncio.Lock()
_extract_progress: dict[str, int] = {}


# ── Read / write ───────────────────────────────────────────────────────────────

def load_profile() -> str:
    """Full profile markdown, or '' if nothing learned yet."""
    try:
        return PROFILE_PATH.read_text()
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def _parse_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*\S)\s*$", line)
        if m:
            current = m.group(1).strip()
            sections.setdefault(current, [])
            continue
        if current and line.strip().startswith("- "):
            sections[current].append(line.strip()[2:].strip())
    return sections


def _render(sections: dict[str, list[str]]) -> str:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = ["# User Profile", f"_Last updated: {ts}_", ""]
    ordered = [s for s in SECTIONS if sections.get(s)]
    ordered += [s for s in sections if s not in SECTIONS and sections.get(s)]
    for sec in ordered:
        out.append(f"## {sec}")
        for fact in sections[sec]:
            out.append(f"- {fact}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def _is_duplicate(fact: str, existing: list[str]) -> bool:
    nf = _norm(fact)
    if not nf:
        return True
    for e in existing:
        ne = _norm(e)
        if nf == ne or nf in ne or ne in nf:
            return True
    return False


def add_fact(category: str, fact: str) -> bool:
    """Add one durable fact under `category`. Returns True if newly added.

    Synchronous file IO — safe to call from execute_tool's executor thread.
    """
    fact = (fact or "").strip()
    if not fact:
        return False
    cat = category.strip().title() if category else "Other"
    if cat not in SECTIONS:
        cat = "Other"

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    sections = _parse_sections(load_profile())
    bucket = sections.setdefault(cat, [])
    if _is_duplicate(fact, bucket):
        return False
    bucket.append(fact)
    PROFILE_PATH.write_text(_render(sections))
    return True


def _fact_matches(nq: str, q_tokens: set, fact: str) -> bool:
    nf = _norm(fact)
    return bool(nq in nf or nf in nq or (q_tokens and q_tokens <= set(nf.split())))


def find_facts(query: str) -> list[str]:
    """Preview which profile facts match `query` (does not modify anything)."""
    nq = _norm((query or "").strip())
    if not nq:
        return []
    q_tokens = set(nq.split())
    out: list[str] = []
    for facts in _parse_sections(load_profile()).values():
        out.extend(f for f in facts if _fact_matches(nq, q_tokens, f))
    return out


def remove_fact(query: str) -> list[str]:
    """Remove profile fact(s) matching `query`. Returns the facts actually removed.
    Synchronous file IO — safe to call from execute_tool's executor thread."""
    to_remove = set(find_facts(query))
    if not to_remove:
        return []
    sections = _parse_sections(load_profile())
    removed: list[str] = []
    for cat, facts in list(sections.items()):
        kept = []
        for f in facts:
            if f in to_remove:
                removed.append(f)
            else:
                kept.append(f)
        sections[cat] = kept
    if removed:
        PROFILE_PATH.write_text(_render(sections))
    return removed


def _add_facts(facts: list[tuple[str, str]]) -> list[str]:
    """Batch add (category, fact); returns list of facts actually written."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    sections = _parse_sections(load_profile())
    added: list[str] = []
    for category, fact in facts:
        fact = (fact or "").strip()
        if not fact:
            continue
        cat = category.strip().title() if category else "Other"
        if cat not in SECTIONS:
            cat = "Other"
        bucket = sections.setdefault(cat, [])
        if _is_duplicate(fact, bucket):
            continue
        bucket.append(fact)
        added.append(fact)
    if added:
        PROFILE_PATH.write_text(_render(sections))
    return added


# ── Background extraction ──────────────────────────────────────────────────────

_EXTRACT_SYSTEM = (
    "You maintain a long-term profile of a user from their chat with an assistant. "
    "Extract only DURABLE facts about the user as a person: identity/background, "
    "stable preferences and working style, recurring projects they own, their tools "
    "and hardware/OS environment, and long-term goals. "
    "IGNORE one-off task details, transient state, questions, and anything about the "
    "assistant. Do not restate facts already in the existing profile. "
    "Return STRICT JSON only: {\"facts\":[{\"category\":\"Identity|Preferences|Projects|"
    "Environment|Goals|Other\",\"fact\":\"...\"}]}. "
    "If there is nothing durable and new, return {\"facts\":[]}."
)


def _parse_facts_json(raw: str) -> list[tuple[str, str]]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except Exception:
            return []
    out = []
    for item in data.get("facts", []):
        if isinstance(item, dict) and item.get("fact"):
            out.append((str(item.get("category", "Other")), str(item["fact"])))
    return out


async def extract(session_id: str, messages: list, api_key: str) -> list[str]:
    """Extract durable facts from messages added since last extraction.

    Returns the list of facts newly written to the profile (may be empty).
    """
    async with _lock:
        last = _extract_progress.get(session_id, 0)
        _extract_progress[session_id] = len(messages)
        new = messages[last:]

    convo = [
        m for m in new
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
        and m.get("content", "").strip()
    ]
    if not any(m["role"] == "assistant" for m in convo):
        return []

    transcript = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in convo)
    if len(transcript) > 12000:
        transcript = transcript[-12000:]

    existing = load_profile() or "(empty)"
    body = {
        "model": EXTRACT_MODEL,
        "messages": [
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content":
                f"Existing profile:\n{existing}\n\n"
                f"New conversation:\n{transcript}"},
        ],
        "stream": False,
        "max_tokens": 500,
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{DEEPSEEK_BASE}/v1/chat/completions", json=body, headers=headers)
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]

    facts = _parse_facts_json(raw)
    if not facts:
        return []
    async with _lock:
        return _add_facts(facts)
