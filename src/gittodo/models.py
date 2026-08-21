"""Domaine : ce qu'on lit de GitHub et ce qu'on en déduit à faire."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Kind(str, Enum):
    REVIEW_REQUESTED = "review_requested"
    REVIEW_AGAIN = "review_again"
    REPLIES_TO_CHECK = "replies_to_check"
    MESSAGES_TO_ANSWER = "messages_to_answer"
    MENTION = "mention"
    CHANGES_REQUESTED = "changes_requested"
    CONFLICTS = "conflicts"
    CI_FAILING = "ci_failing"
    READY_TO_MERGE = "ready_to_merge"
    NO_REVIEWER = "no_reviewer"
    ASSIGNED = "assigned"
    WAITING_REPLY = "waiting_reply"
    WAITING_REVIEW = "waiting_review"
    BLOCKED_FOR_AUTHOR = "blocked_for_author"
    APPROVED_BY_ME = "approved_by_me"
    CHANGES_REQUESTED_BY_ME = "changes_requested_by_me"
    DRAFT = "draft"
    ORPHAN_BRANCH = "orphan_branch"
    BRANCH_TO_DELETE = "branch_to_delete"
    RECENTLY_CLOSED = "recently_closed"


@dataclass(frozen=True)
class Group:
    kind: Kind
    label: str
    symbol: str
    is_action: bool
    urgent: bool = False


GROUPS: dict[Kind, Group] = {
    Kind.REVIEW_REQUESTED: Group(Kind.REVIEW_REQUESTED, "À reviewer", "eye", True),
    Kind.REVIEW_AGAIN: Group(Kind.REVIEW_AGAIN, "À reviewer de nouveau", "arrow.clockwise", True),
    Kind.ASSIGNED: Group(Kind.ASSIGNED, "Assignées à moi", "person.crop.circle.badge.checkmark", True),
    Kind.MESSAGES_TO_ANSWER: Group(
        Kind.MESSAGES_TO_ANSWER, "On attend ma réponse", "bubble.left.and.bubble.right", True
    ),
    Kind.REPLIES_TO_CHECK: Group(
        Kind.REPLIES_TO_CHECK, "On m'a répondu", "arrowshape.turn.up.left", True
    ),
    Kind.MENTION: Group(Kind.MENTION, "Mentions", "at", True),
    Kind.CHANGES_REQUESTED: Group(Kind.CHANGES_REQUESTED, "Mes PR à corriger", "arrow.uturn.left", True, True),
    Kind.CONFLICTS: Group(Kind.CONFLICTS, "Mes PR en conflit", "arrow.triangle.branch", True, True),
    Kind.CI_FAILING: Group(Kind.CI_FAILING, "Mes PR avec CI rouge", "xmark.octagon", True, True),
    Kind.READY_TO_MERGE: Group(Kind.READY_TO_MERGE, "Mes PR à merger", "checkmark.seal", True),
    Kind.NO_REVIEWER: Group(Kind.NO_REVIEWER, "Mes PR sans reviewer", "person.badge.plus", True),
    Kind.WAITING_REVIEW: Group(Kind.WAITING_REVIEW, "Mes PR en attente de review", "clock", False),
    Kind.WAITING_REPLY: Group(Kind.WAITING_REPLY, "Mes messages sans retour", "hourglass", False),
    Kind.DRAFT: Group(Kind.DRAFT, "Mes drafts", "pencil.line", False),
    Kind.APPROVED_BY_ME: Group(Kind.APPROVED_BY_ME, "J'ai approuvé", "hand.thumbsup", False),
    Kind.CHANGES_REQUESTED_BY_ME: Group(
        Kind.CHANGES_REQUESTED_BY_ME, "J'ai demandé des changements", "exclamationmark.bubble", False
    ),
    Kind.BLOCKED_FOR_AUTHOR: Group(
        Kind.BLOCKED_FOR_AUTHOR, "L'auteur doit rebaser", "arrow.triangle.branch", False
    ),
    Kind.ORPHAN_BRANCH: Group(Kind.ORPHAN_BRANCH, "Mes branches sans PR", "arrow.branch", False),
    Kind.BRANCH_TO_DELETE: Group(Kind.BRANCH_TO_DELETE, "Mes branches à supprimer", "trash", False),
    # Histoire, pas travail : en toute fin de menu. Ses lignes comptent malgré tout, dans la
    # pastille violette et non dans la rouge, tant qu'on ne les a pas ouvertes.
    Kind.RECENTLY_CLOSED: Group(Kind.RECENTLY_CLOSED, "Mes PR récemment clôturées", "flag.checkered", False),
}

# L'ordre du menu est celui de ce dictionnaire : d'abord ce qu'on attend de moi, puis mes
# PR, puis l'informatif. Renommer ou déplacer une section se fait ici, et nulle part ailleurs.
ORDER: list[Kind] = list(GROUPS)


@dataclass(frozen=True)
class Person:
    login: str
    name: str = ""
    avatar: str = ""
    # Dernière action visible sur les dépôts, pour classer le menu « voir en tant que ».
    last_seen: datetime | None = None

    @property
    def label(self) -> str:
        return f"{self.name} (@{self.login})" if self.name else f"@{self.login}"


@dataclass(frozen=True)
class Branch:
    """Branche distante poussée par la personne observée."""

    repo: str  # owner/name
    name: str
    committed_at: datetime
    author: str = ""
    # État de la PR la plus récente de la branche : "" (aucune), MERGED, CLOSED.
    pr_state: str = ""
    # Comparaison à la branche par défaut : GitHub ne calcule pas de conflit sans PR.
    base: str = ""
    status: str = ""  # AHEAD, BEHIND, DIVERGED, IDENTICAL
    ahead: int = 0
    behind: int = 0

    @property
    def resolved(self) -> bool:
        """PR mergée et pas un commit propre : le sujet est clos, il n'y a rien à décider.

        La comparaison doit avoir abouti (`status` renseigné) : sans elle, `ahead` vaut 0
        par défaut et on filtrerait à tort.
        """
        return self.pr_state == "MERGED" and bool(self.status) and not self.ahead

    @property
    def obsolete(self) -> bool:
        """Plus rien à en tirer : PR mergée, PR abandonnée, ou aucun commit propre."""
        return self.pr_state in ("MERGED", "CLOSED") or (bool(self.status) and not self.ahead)

    @property
    def note(self) -> str:
        if self.pr_state == "MERGED":
            return "PR mergée, la branche a fait son temps"
        if self.pr_state == "CLOSED":
            return "PR fermée sans merge"
        if not self.status:
            return ""
        if not self.ahead:
            return f"rien en avance sur {self.base}"
        if self.status == "DIVERGED":
            return f"divergé de {self.base} : {self.behind} commits de retard"
        if self.behind:
            return f"{self.behind} commits de retard sur {self.base}"
        return f"à jour avec {self.base}"

    @property
    def delete_url(self) -> str:
        return f"https://github.com/{self.repo}/branches/all?query={self.name}"

    @property
    def key(self) -> str:
        return f"{self.repo}:{self.name}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}/compare/{self.name}?expand=1"

    @property
    def at(self) -> datetime:
        return self.committed_at


@dataclass(frozen=True)
class Comment:
    author: str
    is_bot: bool
    created_at: datetime
    url: str
    body: str
    avatar: str = ""
    # Réactions que j'ai posées sur ce message : un 👍 vaut accusé de réception.
    my_reactions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Review:
    author: str
    state: str
    submitted_at: datetime


@dataclass(frozen=True)
class Thread:
    id: str
    resolved: bool
    outdated: bool
    path: str
    line: int | None
    comments: tuple[Comment, ...]
    # Auteur du premier message : c'est lui qui a la charge de résoudre le fil.
    opener: str = ""


@dataclass
class PullRequest:
    id: str
    repo: str
    number: int
    title: str
    url: str
    author: str
    avatar: str
    is_draft: bool
    created_at: datetime
    updated_at: datetime
    review_decision: str | None
    mergeable: str | None
    ci_state: str | None
    reviewers: tuple[str, ...]
    reviews_count: int
    threads: tuple[Thread, ...]
    comments: tuple[Comment, ...]
    head: str = ""
    base: str = ""
    reviews: tuple[Review, ...] = ()
    last_commit_at: datetime | None = None
    sources: set[str] = field(default_factory=set)
    # Photo par login, alimentée par l'auteur, les reviewers et les messages : de quoi mettre
    # un visage sur n'importe qui est nommé dans une ligne.
    portraits: dict[str, str] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return f"{self.repo}#{self.number}"

    def verdicts(self) -> dict[str, str]:
        """Dernier avis tranché (APPROVED / CHANGES_REQUESTED) par reviewer."""
        latest: dict[str, str] = {}
        for review in self.reviews:
            if review.state in ("APPROVED", "CHANGES_REQUESTED"):
                latest[review.author] = review.state
        return latest

    def my_last_review(self, me: str) -> Review | None:
        mine = [review for review in self.reviews if review.author == me]
        return mine[-1] if mine else None


@dataclass(frozen=True)
class Item:
    id: str
    kind: Kind
    title: str
    detail: str
    url: str
    at: datetime
    fingerprint: str
    repo: str = ""
    # Photo de la personne concernée par la ligne (auteur de la PR ou du commentaire).
    avatar: str = ""
    # Toutes les personnes concernées par cette action, la première à avoir parlé en tête.
    # `avatar` reste la principale : c'est elle qu'on affiche seule quand il n'y en a qu'une.
    faces: tuple[str, ...] = ()
    # Nombre de notifications portées par la ligne : 1 par action, davantage quand
    # plusieurs messages attendent une réponse. 0 pour les lignes informatives.
    weight: int = 1
    # État de la PR résumé en pastilles (symbole SF, nombre éventuel).
    chips: tuple[tuple[str, str], ...] = ()
    # Explication au survol : ce que la pastille compte, et pourquoi la ligne est là.
    hint: str = ""
    # « branche → cible », affiché sous les métadonnées.
    route: str = ""
    # Étiquette dessinée en tête des métadonnées, pour un état qui doit sauter aux yeux.
    tag: str = ""
    # None = l'urgence par défaut de la catégorie.
    urgent: bool | None = None
    # La PR n'est plus ouverte : la ligne compte dans la pastille violette, pas dans la rouge.
    # Les deux sommes restent égales à leur badge respectif.
    closed: bool = False

    @property
    def group(self) -> Group:
        return GROUPS[self.kind]

    @property
    def is_urgent(self) -> bool:
        return self.group.urgent if self.urgent is None else self.urgent


@dataclass(frozen=True)
class Closure:
    """Ce qui est arrivé à une PR sortie du périmètre ouvert, et par la main de qui."""

    pr: PullRequest
    merged: bool
    actor: str
    actor_avatar: str
    at: datetime


@dataclass
class Snapshot:
    items: list[Item]
    viewer: str = ""
    # Identité observée : le propriétaire du token, ou la personne du mode « voir en tant que ».
    identity: str = ""
    fetched_at: datetime | None = None
    rate_remaining: int | None = None
    error: str | None = None
    truncated: list[str] = field(default_factory=list)
    people: tuple[Person, ...] = ()

    @property
    def impersonating(self) -> bool:
        return bool(self.identity) and self.identity != self.viewer

    def actions(self) -> list[Item]:
        return [i for i in self.items if i.group.is_action]
