"""Configuration utilisateur, dans ~/.config/gittodo/config.json."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints

# Surchargeables pour tester sans toucher à la configuration et à l'état réels.
CONFIG_PATH = Path(os.environ.get("GITTODO_CONFIG") or Path.home() / ".config" / "gittodo" / "config.json")
STATE_PATH = Path(
    os.environ.get("GITTODO_STATE") or Path.home() / "Library" / "Application Support" / "GitTodo" / "state.json"
)

DEFAULT_IGNORED = ["linear", "github-actions", "dependabot", "codecov", "coderabbitai", "sonarcloud", "vercel"]



def _accepted(hint) -> tuple:
    """Types concrets qu'une annotation accepte : `str | None` et `list[str]` compris."""
    if get_origin(hint) in (Union, UnionType):
        return tuple(t for arm in get_args(hint) for t in _accepted(arm))
    origin = get_origin(hint) or hint
    return (origin,) if isinstance(origin, type) else ()


def _keep(value, hint) -> bool:
    """Le fichier est modifiable à la main : une valeur du mauvais type est écartée.

    Sans ce filtre, un `refresh_seconds: "vingt"` fait lever une exception à chaque cycle,
    dans le fil de fond, et l'app se fige en continuant d'afficher un état crédible.
    """
    accepted = _accepted(hint)
    if not accepted:
        return True
    # `True` est un entier pour Python : sans ce garde-fou, un booléen passerait pour un délai.
    if isinstance(value, bool) and bool not in accepted:
        return False
    return isinstance(value, accepted)

@dataclass
class Config:
    # Qualificateurs ajoutés à chaque recherche GitHub. [] = tous les dépôts que le token voit,
    # ce qui est large : le premier réglage à poser est souvent un `org:` ici.
    scope: list[str] = field(default_factory=list)
    refresh_seconds: int = 20
    # Une réaction ou la résolution d'un fil ne modifient pas la date de la PR : la sonde
    # légère ne peut pas les voir. D'où une requête complète forcée à cet intervalle.
    full_refresh_seconds: int = 60
    # Chaque PR lue coûte du quota GraphQL : mesuré à 47 points par requête complète avec 12
    # par recherche, soit ~2940 points/heure au rythme par défaut, sous le plafond de 5000.
    # C'est la requête complète forcée qui domine, pas la sonde. Monter l'un impose de baisser
    # l'autre.
    max_per_search: int = 12
    # Auteurs dont les commentaires ne déclenchent jamais un « à répondre ».
    ignored_authors: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORED))
    # Réactions qui valent accusé de réception : le message est traité, il ne compte plus. Le
    # pouce vers le bas en fait partie, parce qu'un refus est une réponse.
    # Contenus GitHub possibles : THUMBS_UP, THUMBS_DOWN, LAUGH, HOORAY, CONFUSED, HEART,
    # ROCKET, EYES.
    acknowledge_reactions: list[str] = field(default_factory=lambda: ["THUMBS_UP", "THUMBS_DOWN"])
    # true : « à reviewer » ne garde que les demandes nominatives, pas celles faites à une équipe.
    direct_review_requests_only: bool = True
    # Login d'un collègue pour voir l'app à sa place, null pour soi-même.
    view_as: str | None = None
    # Branches distantes que j'ai poussées et qui n'ont aucune PR.
    show_branches: bool = True
    # Dépôts supplémentaires à balayer pour les branches, en plus de ceux où j'ai committé
    # et de ceux de mes PR ouvertes. Format « org/dépôt ».
    branch_repos: list[str] = field(default_factory=list)
    # Intervalle du balayage des branches : il dure ~20 s et coûte ~20 points de quota,
    # d'où une cadence plus lente que celle des PR. « Actualiser » le relance aussitôt.
    branch_refresh_seconds: int = 300
    # La boîte des non-lues se lit page par page, jusqu'à dix appels REST : trop lent pour
    # chaque cycle, alors qu'une mention n'arrive pas à la seconde.
    mention_refresh_seconds: int = 300
    # Sections informatives (en attente, brouillons) affichées sous les actions.
    # Suivi parallèle des PR sorties du périmètre ouvert : ce qui reste à répondre dessus, et
    # l'histoire de mes clôtures. Deux recherches mesurées à 3 points, sur leur propre cadence.
    show_closed: bool = True
    closed_days: int = 30
    closed_history_rows: int = 10
    closed_refresh_seconds: int = 300
    show_waiting: bool = True
    # Les brouillons ne remontent ni CI en échec ni absence de reviewer.
    drafts_are_actionable: bool = False
    # Les mentions non lues viennent de l'API notifications.
    include_mentions: bool = True
    hide_when_zero: bool = False
    # Anneau de progression autour de la photo : il se remplit jusqu'au prochain cycle, ce qui
    # dit quand l'app va relire sans avoir à ouvrir le menu.
    show_refresh_ring: bool = True
    # Format de l'élément dans la barre : "avatar" (photo de l'identité + pastille de
    # comptage, ~35-40 pt), "count" (nombre seul, ~35 pt), "icon_count" (~57 pt),
    # "icon" (icône seule, ~34 pt). Une barre saturée évince les éléments les plus larges.
    badge_style: str = "avatar"
    gh_path: str | None = None
    token_command: list[str] | None = None

    @classmethod
    def load(cls) -> "Config":
        known = {f.name for f in fields(cls)}
        hints = get_type_hints(cls)
        data: dict = {}
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text() or "{}")
            except (json.JSONDecodeError, OSError):
                raw = {}
            if isinstance(raw, dict):
                data = {k: v for k, v in raw.items() if k in known and _keep(v, hints[k])}
        cfg = cls(**data)
        # Réécrit le fichier quand des options apparaissent, pour qu'il reste exhaustif.
        if set(data) != known:
            cfg.save()
        return cfg

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n")

    def scoped(self, query: str) -> str:
        return " ".join([query.format(who=self.view_as or "@me"), *self.scope])

    def orgs(self) -> list[str]:
        return [entry.removeprefix("org:") for entry in self.scope if entry.startswith("org:")]

    def ignored(self) -> set[str]:
        return {a.lower() for a in self.ignored_authors}

    def acknowledged(self) -> set[str]:
        return {r.upper() for r in self.acknowledge_reactions}
