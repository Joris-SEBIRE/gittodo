"""Branches distantes sans aucune PR, sans angle mort volontaire.

Deux signaux, complémentaires, parce qu'aucun des deux ne suffit :

- **qui a poussé** (`/repos/{dépôt}/activity?actor=`) : c'est la sémantique de la page
  « Your branches » de GitHub, exacte même quand le dernier commit est de quelqu'un d'autre.
  Mais la fenêtre est courte — une centaine d'entrées, le paramètre `page` est ignoré par
  l'API — donc elle ne voit que les dernières semaines sur un dépôt actif ;
- **auteur du dernier commit**, obtenu en listant *toutes* les branches du dépôt. Aucune
  limite dans le temps, mais rate une branche que j'ai poussée sans en être l'auteur.

L'union des deux ne rate donc ni une vieille branche oubliée, ni une branche poussée pour
quelqu'un d'autre. Tout est distant : rien n'est lu dans les clones locaux.
"""

from __future__ import annotations

from datetime import datetime

from .models import Branch, parse_ts

PUSHED = {"push", "force_push", "branch_creation"}
PER_PAGE = 100


def pushed_refs(client, repos: list[str], login: str) -> dict[tuple[str, str], datetime]:
    """Branches poussées récemment par cette personne, d'après l'activité du dépôt."""
    seen: dict[tuple[str, str], datetime] = {}
    for repo in repos:
        for entry in client.activity(repo, login, PER_PAGE):
            if entry.get("activity_type") not in PUSHED:
                continue
            name = (entry.get("ref") or "").removeprefix("refs/heads/")
            if not name:
                continue
            when = parse_ts(entry.get("timestamp"))
            key = (repo, name)
            seen[key] = max(when, seen.get(key, when))
    return seen


def orphans(client, repos: list[str], login: str) -> list[Branch]:
    """Mes branches existantes sans PR ouverte, avec leur état et leur écart."""
    pushed = pushed_refs(client, repos, login)
    # `all_branch_names` renseigne au passage la branche par défaut de chaque dépôt.
    listed = set(client.all_branch_names(repos))
    # Pousser sur `main` (un merge, par exemple) apparaît dans l'activité : sans ce filtre,
    # la branche par défaut se retrouverait « à supprimer ».
    names = {
        (repo, name)
        for repo, name in listed | set(pushed)
        if name != client.defaults.get(repo, "main")
    }
    without_pr = client.branches_without_open_pr(sorted(names))
    mine = [
        branch
        for branch in without_pr
        if branch.author == login or (branch.repo, branch.name) in pushed
    ]
    # La divergence n'est calculée qu'ici : sur les miennes, pas sur toutes les orphelines.
    return sorted(client.compare_to_default(mine), key=lambda branch: branch.at, reverse=True)
