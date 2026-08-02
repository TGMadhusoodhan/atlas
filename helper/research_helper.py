#!/usr/bin/env python3
"""Research pipeline helper for ai-sidebar.

Commands: {"cmd":"research","id":"...","question":"...","model":"..."}
          {"cmd":"cancel","id":"..."}
Events:   {"type":"progress","id":"...","step":"...","detail":"..."}
          {"type":"token","id":"...","text":"..."}
          {"type":"done","id":"...","sources":[...],"api_messages":[...]}
          {"type":"error","id":"...","message":"..."}
          {"type":"cancelled","id":"..."}
"""

import asyncio
import datetime
import json
import os
import sys
import threading
import tomllib
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

import httpx
import trafilatura

CONFIG_PATH = Path.home() / ".config/ai-sidebar/config.toml"
LOG_FILE    = Path.home() / ".local/share/ai-sidebar/research_log.jsonl"
DEEPSEEK_BASE   = "https://api.deepseek.com"
SEARXNG_URL     = os.environ.get("SEARXNG_URL", "http://localhost:8888")

SEARXNG_CANDIDATES = 15
MAX_PAGES          = 10
CHARS_PER_PAGE     = 4000
MIN_TEXT_LENGTH    = 500   # raised — short pages are rarely useful
MAX_PER_DOMAIN     = 2     # dedup: no more than 2 pages from same domain
SCRAPE_TIMEOUT     = 8.0
SCRAPE_CONCURRENCY = 5
ROBOTS_TIMEOUT     = 3.0

_robots_cache: dict[str, bool] = {}

# Domains to skip entirely — login walls, social media, content farms
BLOCKED_DOMAINS: frozenset[str] = frozenset({
    # Social media / login walls
    "twitter.com", "x.com", "facebook.com", "instagram.com",
    "linkedin.com", "tiktok.com", "snapchat.com", "pinterest.com",
    "threads.net", "bsky.app",
    # Aggregators with no real content
    "news.ycombinator.com", "t.co",
    # Known paywalls (trafilatura gets nothing useful)
    "wsj.com", "ft.com", "bloomberg.com", "economist.com",
    "thetimes.co.uk", "telegraph.co.uk",
    # Low-quality / SEO farms
    "answers.com", "ask.com", "ehow.com",
    "liveabout.com", "thespruce.com", "thoughtco.com",
    "reference.com", "leaf.tv",
})

# Domains that produce consistently reliable content — sorted first
HIGH_TRUST_DOMAINS: frozenset[str] = frozenset({
    # Encyclopedic
    "wikipedia.org", "wikimedia.org", "britannica.com",
    # Dev docs & code
    "github.com", "gitlab.com", "stackoverflow.com", "stackexchange.com",
    "developer.mozilla.org", "docs.python.org", "docs.rs",
    "learn.microsoft.com", "docs.microsoft.com",
    "developer.apple.com", "developer.android.com",
    "rust-lang.org", "golang.org", "nodejs.org",
    "archlinux.org", "wiki.archlinux.org",
    # Academic / science
    "arxiv.org", "pubmed.ncbi.nlm.nih.gov", "scholar.google.com",
    "nature.com", "science.org", "sciencedirect.com",
    # Health / gov
    "nih.gov", "cdc.gov", "who.int", "nhs.uk",
    # Established news
    "bbc.com", "bbc.co.uk", "reuters.com", "apnews.com",
    "theguardian.com",
})


def _domain_trust(domain: str) -> int:
    """Returns 2 = high trust, 1 = neutral, -1 = blocked."""
    bare = domain.removeprefix("www.")
    for b in BLOCKED_DOMAINS:
        if bare == b or bare.endswith("." + b):
            return -1
    for h in HIGH_TRUST_DOMAINS:
        if bare == h or bare.endswith("." + h):
            return 2
    if bare.endswith((".gov", ".edu", ".ac.uk", ".gov.uk")):
        return 2
    return 1


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


def _progress(req_id: str, step: str, detail: str) -> dict:
    return {"type": "progress", "id": req_id, "step": step, "detail": detail}


# ── Query generation ──────────────────────────────────────────────────────────

async def generate_query(question: str, api_key: str, model: str) -> str:
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Generate a concise web search query (3-8 words) to find current information "
                    "answering the question. Return ONLY the search query, nothing else."
                ),
            },
            {"role": "user", "content": question},
        ],
        "max_tokens": 30,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{DEEPSEEK_BASE}/v1/chat/completions", json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip().strip('"').strip("'")


# ── Search ────────────────────────────────────────────────────────────────────

async def search_searxng(query: str) -> list[dict]:
    params = {
        "q": query,
        "format": "json",
        "categories": "general",
        "language": "en",
        "safesearch": "0",
        "pageno": "1",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{SEARXNG_URL}/search", params=params)
        resp.raise_for_status()
        return resp.json().get("results", [])


# ── Scraping ──────────────────────────────────────────────────────────────────

async def _check_robots(domain: str, url: str) -> bool:
    if domain in _robots_cache:
        return _robots_cache[domain]
    try:
        robots_url = f"https://{domain}/robots.txt"
        async with httpx.AsyncClient(timeout=ROBOTS_TIMEOUT) as client:
            resp = await client.get(robots_url, follow_redirects=True)
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(resp.text.splitlines())
        result = rp.can_fetch("*", url)
    except Exception:
        result = True  # assume allowed if robots.txt unreachable
    _robots_cache[domain] = result
    return result


def _extract_title(html: str, url: str) -> str:
    import re
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    return m.group(1).strip()[:120] if m else urlparse(url).netloc


def _run_trafilatura(html: str, url: str) -> str | None:
    return trafilatura.extract(
        html, url=url,
        include_comments=False,
        include_tables=True,
        no_fallback=False,
        output_format="txt",
    )


async def _scrape_one(client: httpx.AsyncClient, result: dict,
                      sem: asyncio.Semaphore,
                      loop: asyncio.AbstractEventLoop) -> dict | None:
    url = result.get("url", "")
    if not url:
        return None
    domain = urlparse(url).netloc

    # Reject blocked domains immediately — no network request needed
    if _domain_trust(domain) == -1:
        return None

    async with sem:
        try:
            if not await _check_robots(domain, url):
                return None

            resp = await client.get(
                url, timeout=SCRAPE_TIMEOUT, follow_redirects=True,
                headers={"User-Agent": "ai-sidebar/1.0 (personal research tool)"},
            )
            if "html" not in resp.headers.get("content-type", ""):
                return None

            html = resp.text
            text = await loop.run_in_executor(None, _run_trafilatura, html, url)
            if not text or len(text) < MIN_TEXT_LENGTH:
                return None

            return {
                "url": url,
                "title": _extract_title(html, url),
                "text": text[:CHARS_PER_PAGE],
                "trust": _domain_trust(domain),
            }
        except Exception:
            return None


async def scrape_concurrent(results: list[dict], cancel_event: asyncio.Event,
                            req_id: str) -> list[dict]:
    sem  = asyncio.Semaphore(SCRAPE_CONCURRENCY)
    loop = asyncio.get_running_loop()
    scraped: list[dict] = []
    domain_counts: dict[str, int] = {}
    candidates = results[:SEARXNG_CANDIDATES]

    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    async with httpx.AsyncClient(limits=limits) as client:
        tasks = [
            asyncio.create_task(_scrape_one(client, r, sem, loop))
            for r in candidates
        ]
        for fut in asyncio.as_completed(tasks):
            if cancel_event.is_set() or len(scraped) >= MAX_PAGES:
                for t in tasks:
                    t.cancel()
                break
            page = await fut
            if page:
                domain = urlparse(page["url"]).netloc
                if domain_counts.get(domain, 0) < MAX_PER_DOMAIN:
                    domain_counts[domain] = domain_counts.get(domain, 0) + 1
                    scraped.append(page)
                    emit(_progress(req_id, "scraping_update",
                                   f"Scraped {len(scraped)} / {len(candidates)} sites"))

    # Sort: high-trust sources first so synthesis model sees best content early
    scraped.sort(key=lambda p: p.get("trust", 1), reverse=True)
    return scraped[:MAX_PAGES]


# ── Synthesis ─────────────────────────────────────────────────────────────────

async def synthesize_stream(req_id: str, question: str, scraped: list[dict],
                            model: str, api_key: str,
                            cancel_event: asyncio.Event) -> str:
    context = "\n\n---\n\n".join(
        f"[{i+1}] {p['title']}\nURL: {p['url']}\n\n{p['text']}"
        for i, p in enumerate(scraped)
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are a research assistant. Synthesize a clear, accurate, well-structured answer "
                "using ONLY the provided source excerpts. Cite sources inline as [1], [2], etc. "
                "If sources disagree, note it explicitly. Do not add facts from training knowledge as if current. "
                "End with a '## Sources' section as a numbered list of sources cited."
            ),
        },
        {
            "role": "user",
            "content": f"Question: {question}\n\nSource excerpts:\n\n{context}",
        },
    ]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    body = {"model": model, "messages": messages, "stream": True}
    full = ""

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
    ) as client:
        async with client.stream(
            "POST", f"{DEEPSEEK_BASE}/v1/chat/completions",
            json=body, headers=headers,
        ) as resp:
            if resp.status_code != 200:
                err = await resp.aread()
                raise RuntimeError(f"HTTP {resp.status_code}: {err.decode()[:200]}")

            async for raw in resp.aiter_lines():
                if cancel_event.is_set():
                    return full
                if not raw.startswith("data: "):
                    continue
                payload = raw[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    delta = chunk["choices"][0]["delta"]
                    reasoning = delta.get("reasoning_content") or ""
                    if reasoning:
                        emit({"type": "thinking_token", "id": req_id, "text": reasoning})
                    content = delta.get("content") or ""
                    if content:
                        full += content
                        emit({"type": "token", "id": req_id, "text": content})
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

    return full


# ── Logging ───────────────────────────────────────────────────────────────────

def _log_run(question: str, query: str, attempted: int, scraped: int, model: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.datetime.now().isoformat(),
        "question": question[:200],
        "query": query,
        "urls_attempted": attempted,
        "urls_scraped": scraped,
        "model": model,
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Pipeline ──────────────────────────────────────────────────────────────────

async def research_pipeline(req_id: str, question: str, model: str,
                            cancel_event: asyncio.Event) -> None:
    api_key = get_api_key()
    if not api_key:
        emit({"type": "error", "id": req_id,
              "message": "No API key. Set DEEPSEEK_API_KEY env var."})
        return

    try:
        # 1. Generate query
        emit(_progress(req_id, "query", "Generating search query..."))
        query = await generate_query(question, api_key, model)
        if not query:
            query = question[:60]

        if cancel_event.is_set():
            emit({"type": "cancelled", "id": req_id})
            return

        # 2. Search
        emit(_progress(req_id, "searching", f'Searching: "{query}"'))
        try:
            results = await search_searxng(query)
        except Exception as exc:
            emit({"type": "error", "id": req_id,
                  "message": (
                      f"SearxNG unreachable at {SEARXNG_URL}: {exc}. "
                      "Start it with: docker compose up -d  (from ~/ai-sidebar)"
                  )})
            return

        if not results:
            emit({"type": "error", "id": req_id,
                  "message": "No search results. Try a different query or check SearxNG engines."})
            return

        # 3. Scrape
        candidate_count = min(len(results), SEARXNG_CANDIDATES)
        emit(_progress(req_id, "scraping", f"Scraping {candidate_count} sites..."))
        scraped = await scrape_concurrent(results, cancel_event, req_id)

        if cancel_event.is_set():
            emit({"type": "cancelled", "id": req_id})
            return

        n = len(scraped)
        if n == 0:
            emit({"type": "error", "id": req_id,
                  "message": "Could not extract usable content from any search results."})
            return

        # 4. Synthesize
        emit(_progress(req_id, "synthesizing", f"Synthesizing from {n} sources..."))

        prefix = ""
        if n < 4:
            prefix = f"> ⚠ Only {n} usable source{'s' if n != 1 else ''} found for this query.\n\n"
            emit({"type": "token", "id": req_id, "text": prefix})

        answer = prefix + await synthesize_stream(
            req_id, question, scraped, model, api_key, cancel_event
        )

        if cancel_event.is_set():
            emit({"type": "cancelled", "id": req_id})
            return

        try:
            _log_run(question, query, candidate_count, n, model)
        except Exception:
            pass

        sources = [
            {"url": s["url"], "title": s["title"], "idx": i + 1}
            for i, s in enumerate(scraped)
        ]
        emit({
            "type": "done",
            "id": req_id,
            "sources": sources,
            "api_messages": [
                {"role": "user",      "content": question},
                {"role": "assistant", "content": answer},
            ],
        })

    except asyncio.CancelledError:
        emit({"type": "cancelled", "id": req_id})
    except httpx.TimeoutException:
        emit({"type": "error", "id": req_id, "message": "Request timed out"})
    except httpx.NetworkError as exc:
        emit({"type": "error", "id": req_id, "message": f"Network error: {exc}"})
    except Exception as exc:
        emit({"type": "error", "id": req_id, "message": str(exc)})


# ── Main loop ─────────────────────────────────────────────────────────────────

async def main() -> None:
    loop = asyncio.get_running_loop()
    line_queue: asyncio.Queue[str | None] = asyncio.Queue()

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

            if cmd == "research":
                if active_task and not active_task.done():
                    cancel_event.set()
                    try:
                        await asyncio.wait_for(active_task, timeout=1.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                cancel_event = asyncio.Event()
                active_task = asyncio.create_task(
                    research_pipeline(
                        req["id"],
                        req["question"],
                        req.get("model", "deepseek-v4-flash"),
                        cancel_event,
                    )
                )

            elif cmd == "cancel":
                cancel_event.set()

        except json.JSONDecodeError:
            pass
        except Exception as exc:
            emit({"type": "error", "id": "system", "message": str(exc)})


if __name__ == "__main__":
    asyncio.run(main())
