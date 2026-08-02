#!/usr/bin/env python3
"""Knowledge retrieval over curated personal directories for ai-sidebar.

Agentic RAG: no embeddings, nothing leaves the machine. Uses ripgrep scoped to
a configured set of roots (respecting .gitignore + hidden-file defaults plus
extra ignore globs) so the assistant can search and browse the user's own
files. Consumed by ai_helper's tool layer.

Config lives at ~/.config/ai-sidebar/knowledge.toml and is created with sane
defaults on first use. Edit it to add/remove roots.
"""

import os
import subprocess
import tomllib
from pathlib import Path

CONFIG_PATH = Path.home() / ".config/ai-sidebar/knowledge.toml"

# Curated personal/project dirs — high signal about the user. Non-existent
# entries are silently skipped, so this list can be broad.
DEFAULT_ROOTS = [
    "~/Documents", "~/Important", "~/Resume", "~/Notes", "~/notes",
    "~/Desktop", "~/projects", "~/Projects", "~/prj",
    "~/agastya", "~/agent", "~/atlas", "~/crucible", "~/ai-sidebar",
    "~/arch-dotfiles", "~/dotfiles", "~/hyprlock",
    "~/.config/hypr", "~/.config/ai-sidebar",
]

# Extra ignore globs, on top of .gitignore and hidden files. Keeps junk
# (deps, build output, binaries, media) out of results.
DEFAULT_IGNORE = [
    "node_modules", ".git", "venv", ".venv", "env", "miniconda3",
    "__pycache__", ".cache", "dist", "build", "target", ".next",
    "*.lock", "*.min.js", "*.map",
    # secrets — never pull these into model context via search
    "*.token", "*.key", "*.pem", "*.secret", ".env", ".env.*",
    "id_rsa*", "id_ed25519*", "*credentials*", "config.toml",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.svg", "*.ico",
    "*.pdf", "*.zip", "*.tar", "*.gz", "*.xz", "*.7z",
    "*.mp4", "*.mkv", "*.mp3", "*.wav", "*.flac",
    "*.bin", "*.so", "*.o", "*.a", "*.pyc", "*.wasm",
]

DEFAULT_MAX_RESULTS = 40
MAX_SNIPPET_COLS = 240
MAX_MATCHES_PER_FILE = 4


def _default_config_text() -> str:
    roots = "\n".join(f'  "{r}",' for r in DEFAULT_ROOTS)
    ignore = "\n".join(f'  "{g}",' for g in DEFAULT_IGNORE)
    return (
        "# ai-sidebar knowledge index — the personal dirs the assistant may search.\n"
        "# Agentic retrieval via ripgrep: no embeddings, nothing leaves your machine.\n"
        "# Missing dirs are skipped, so it's safe to list dirs you don't have yet.\n\n"
        f"roots = [\n{roots}\n]\n\n"
        "# Extra ignore globs, on top of .gitignore and hidden files.\n"
        f"ignore = [\n{ignore}\n]\n\n"
        "# Max match lines returned by a single knowledge_search.\n"
        f"max_results = {DEFAULT_MAX_RESULTS}\n"
    )


def _ensure_config() -> None:
    if CONFIG_PATH.exists():
        return
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(_default_config_text())
    except Exception:
        pass


def load_config() -> tuple[list[str], list[str], int]:
    """Returns (existing_roots, ignore_globs, max_results)."""
    _ensure_config()
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "rb") as f:
                cfg = tomllib.load(f)
        except Exception:
            cfg = {}
    roots = cfg.get("roots", DEFAULT_ROOTS)
    ignore = cfg.get("ignore", DEFAULT_IGNORE)
    max_results = int(cfg.get("max_results", DEFAULT_MAX_RESULTS))

    existing = []
    for r in roots:
        p = Path(os.path.expanduser(str(r)))
        if p.exists():
            existing.append(str(p))
    return existing, ignore, max_results


def _ignore_args(ignore: list[str]) -> list[str]:
    args: list[str] = []
    for g in ignore:
        args += ["--glob", f"!{g}"]
    return args


def _scope(roots: list[str], path_filter: str | None) -> list[str]:
    if not path_filter:
        return roots
    pf = os.path.expanduser(path_filter)
    matched = [r for r in roots if pf in r or r in pf]
    if matched:
        return matched
    # Allow searching an explicit subpath that lives under a root.
    if os.path.exists(pf) and any(pf.startswith(r) for r in roots):
        return [pf]
    return roots


def roots_summary() -> str:
    """Short human-readable list of indexed root names for the system prompt."""
    roots, _, _ = load_config()
    if not roots:
        return "(none configured)"
    names = [os.path.basename(r.rstrip("/")) or r for r in roots]
    return ", ".join(names)


def search(query: str, path_filter: str | None = None,
           max_results: int | None = None) -> str:
    """Ripgrep the curated roots for `query`. Returns formatted match lines."""
    roots, ignore, cfg_max = load_config()
    if not roots:
        return "No knowledge roots configured or none exist. Edit ~/.config/ai-sidebar/knowledge.toml."
    limit = min(max_results or cfg_max, 100)
    scope = _scope(roots, path_filter)

    cmd = [
        "rg", "--line-number", "--no-heading", "--color", "never",
        "--smart-case", "--max-columns", str(MAX_SNIPPET_COLS),
        "--max-count", str(MAX_MATCHES_PER_FILE),
        *_ignore_args(ignore), "--regexp", query, *scope,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return "ripgrep (rg) is not installed — install it with: sudo pacman -S ripgrep"
    except subprocess.TimeoutExpired:
        return "Search timed out after 30s. Try a narrower query or path_filter."

    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    if not lines:
        return f"No matches for {query!r} in indexed dirs ({roots_summary()})."

    home = str(Path.home())
    out = []
    for ln in lines[:limit]:
        out.append(ln.replace(home, "~", 1))
    header = f"{len(out)} match line(s)" + (
        f" (capped at {limit})" if len(lines) > limit else ""
    )
    return header + ":\n" + "\n".join(out)


def list_files(path_filter: str | None = None, limit: int = 300) -> str:
    """Manifest of files under the roots so the model knows what exists."""
    roots, ignore, _ = load_config()
    if not roots:
        return "No knowledge roots configured. Edit ~/.config/ai-sidebar/knowledge.toml."
    scope = _scope(roots, path_filter)
    cmd = ["rg", "--files", *_ignore_args(ignore), *scope]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return "ripgrep (rg) is not installed — install it with: sudo pacman -S ripgrep"
    except subprocess.TimeoutExpired:
        return "Listing timed out. Narrow it with a path_filter."

    home = str(Path.home())
    files = [ln.replace(home, "~", 1) for ln in result.stdout.splitlines() if ln.strip()]
    if not files:
        return "No files found in the indexed dirs."
    shown = files[:limit]
    header = f"{len(files)} file(s)" + (f", showing {limit}" if len(files) > limit else "")
    return header + ":\n" + "\n".join(shown)
