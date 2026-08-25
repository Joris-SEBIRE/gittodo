"""Point d'entrée : app de barre des menus, ou dump texte pour vérifier/déboguer.

    python -m gittodo                  lance l'app
    python -m gittodo --print          affiche ce que le menu contiendrait
    python -m gittodo --print --as X   idem, vu à la place du login X
"""

from __future__ import annotations

import sys

from . import branches as remote_branches
from .config import CONFIG_PATH, Config
from .engine import build_items, summarize
from .github import GitHub, GitHubError
from .models import GROUPS, ORDER


def dump(argv: list[str]) -> int:
    cfg = Config.load()
    if "--as" in argv:
        position = argv.index("--as") + 1
        if position >= len(argv):
            print("--as attend un login GitHub", file=sys.stderr)
            return 2
        cfg.view_as = argv[position]
    client = GitHub(cfg)
    try:
        prs, viewer, rate, truncated = client.fetch_pull_requests()
        identity = cfg.view_as or viewer
        notifications = []
        if cfg.include_mentions and identity == viewer:
            try:
                notifications = client.fetch_notifications()[0]
            except GitHubError as exc:
                print(f"(boîte des non-lues indisponible : {exc})")
        client.resolve_mentions(notifications)
        found = []
        if cfg.show_branches:
            try:
                repos = set(client.contributed_repos(identity, viewer)) | {pr.repo for pr in prs}
                found = remote_branches.orphans(client, sorted(repos | set(cfg.branch_repos)), identity)
            except GitHubError as exc:
                print(f"(branches indisponibles : {exc})")
    except GitHubError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    items = build_items(prs, notifications, identity, cfg, found)
    badge, _ = summarize(items)
    seen_as = f" (token de @{viewer})" if identity != viewer else ""
    print(f"@{identity}{seen_as} · {len(prs)} PR lues · badge {badge} · quota {rate}")
    print(f"config {CONFIG_PATH}")
    for note in truncated:
        print(f"  ⚠︎ limite atteinte : {note}")
    for kind in ORDER:
        group = [i for i in items if i.kind == kind]
        if not group:
            continue
        marker = "!" if GROUPS[kind].is_action else " "
        total = sum(i.weight for i in group) if GROUPS[kind].is_action else len(group)
        print(f"\n{marker} {GROUPS[kind].label.upper()} ({total})")
        for item in group:
            pill = f"[{item.weight}] " if item.weight else "[ ] "
            chips = "  ".join(f"{name}{' ' + label if label else ''}" for name, label, *_ in item.chips)
            chips = f"shield  {chips}" if item.guarded else chips
            print(f"    {pill}{item.title}\n      {item.detail}\n      {chips}\n      {item.url}")
    return 0


def main() -> None:
    if "--print" in sys.argv:
        raise SystemExit(dump(sys.argv))
    from .app import run

    run()


if __name__ == "__main__":
    main()
