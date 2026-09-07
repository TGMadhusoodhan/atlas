"""Small pre-decode vocabulary; never substitutes decoded words."""
import json
import re
import subprocess
from pathlib import Path

BASE = ('Atlas', 'Hyprland', 'Quickshell', 'PipeWire', 'WirePlumber', 'CTranslate2',
        'RealtimeSTT', 'DeepSeek', 'GitHub', 'Brave', 'Arch Linux')


def bounded_terms(*groups, limit=32):
    terms, seen = [], set()
    for group in groups:
        for term in group:
            term = str(term).strip()
            if (not re.fullmatch(r'[\w .+/-]{1,48}', term) or term.casefold() in seen):
                continue
            terms.append(term)
            seen.add(term.casefold())
            if len(terms) >= limit:
                return terms
    return terms


def _read(command):
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=.25).strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def desktop_vocabulary(configured=(), project_paths=(), tool_names=()):
    # App classes only: window titles/documents are neither collected nor logged.
    apps = []
    try:
        active = json.loads(_read(['hyprctl', 'activewindow', '-j']) or '{}')
        apps.append(active.get('class', ''))
        clients = json.loads(_read(['hyprctl', 'clients', '-j']) or '[]')
        apps.extend(c.get('class', '') for c in clients[:8])
    except (ValueError, AttributeError, TypeError):
        pass
    projects = []
    for raw in list(project_paths)[:3]:
        path = Path(raw).expanduser()
        projects.extend([path.name, _read(['git', '-C', str(path), 'branch', '--show-current'])])
    player = _read(['playerctl', '-l']).splitlines()[:2]
    return bounded_terms(BASE, configured, apps, projects, player, tool_names)
