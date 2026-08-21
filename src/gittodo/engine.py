"""Règles de déduction : à partir de l'état des PR, ce qui me concerne et ce que je dois faire.

Deux axes. « Ça me concerne » : toutes les PR ouvertes où j'ai un rôle (auteur, reviewer
sollicité, ou simplement déjà intervenu). « J'ai une action » : ce qui attend un geste de ma
part — le reste est affiché en informatif, sous les actions, et ne compte pas dans le badge.

Ce qui ne me concerne plus est hors périmètre par construction : toutes les recherches sont
`is:open`, donc les PR mergées, fermées ou archivées ne remontent jamais. Le brouillon de
quelqu'un d'autre est écarté de la même façon, quoi qu'il contienne.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .config import Config
from .formatting import join, short_repo, since
from .models import GROUPS, ORDER, Closure, Comment, Item, Kind, PullRequest, parse_ts

CI_BROKEN = {"FAILURE", "ERROR"}
CI_OK = {"SUCCESS", None}
MAX_CHIPS = 5


def _humans(comments: tuple[Comment, ...], ignored: set[str]) -> list[Comment]:
    return [c for c in comments if not c.is_bot and c.author.lower() not in ignored]


CODE = re.compile(r"```.*?```|`[^`]*`", re.S)


COMMAND = re.compile(r"^/[a-z0-9][\w-]*(\s.*)?$", re.I)


def _is_command(body: str) -> bool:
    """Commentaire qui n'est qu'une commande adressée à un bot, `/run-e2e` par exemple.

    Ce n'est pas une prise de parole : le compter comme telle ferait passer pour traité tout
    ce qui précède, alors que personne n'a répondu à personne.
    """
    lines = [line.strip() for line in (body or "").splitlines() if line.strip()]
    return bool(lines) and all(COMMAND.match(line) for line in lines)


def _names(text: str, me: str) -> bool:
    """`@moi` dans un texte, sans attraper un login plus long qui commence pareil.

    Le code est retiré d'abord, parce que GitHub n'y crée pas de mention. Les citations, si :
    répondre en citant notifie de nouveau la personne citée, donc c'est bien une sollicitation.
    """
    plain = CODE.sub(" ", text or "")
    # Bornes des deux côtés : ni `@moi-bot` (login plus long), ni `contact@moi.fr` (adresse).
    return re.search(rf"(?<![\w@])@{re.escape(me)}(?![\w-])", plain, re.IGNORECASE) is not None


def _all_comments(pr: PullRequest) -> tuple[Comment, ...]:
    """Tous les messages de la PR, fils résolus compris : un fil résolu est un point traité."""
    return tuple(c for thread in pr.threads for c in thread.comments) + pr.comments


def _named_out_of_sight(pr: PullRequest, me: str, cfg: Config) -> bool:
    """On me nomme dans cette PR, mais nulle part où l'app puisse le montrer et le suivre.

    Deux cas restent : la description de la PR, et un message trop ancien pour la fenêtre
    récupérée. Dans un message visible, c'est la conversation qui porte la sollicitation, avec
    ses réponses et ses accusés de réception, et un fil résolu est un point déjà traité.

    « Être déjà intervenu » se juge sur les recherches GitHub plutôt que sur les messages
    chargés : elles voient toute la PR, la fenêtre non.
    """
    if not cfg.include_mentions or "mentioned" not in pr.sources:
        return False
    if pr.sources.intersection({"commented", "reviewed"}) or pr.my_last_review(me):
        return False
    everything = _all_comments(pr)
    # Bots compris : un robot qui me nomme rend la mention visible, donc suivie ailleurs.
    return not any(c.author == me or _names(c.body, me) for c in everything)


QUOTE = re.compile(r"^\s*(?:>\s*)+(.*)$")
# Les empreintes sont tronquées : au-delà, une ligne recopiée puis reformatée cesserait de
# correspondre à elle-même.
GIST_MAX = 60


def _gist(line: str) -> str:
    """Empreinte d'une ligne, espaces et casse normalisés."""
    return " ".join((line or "").split()).lower()[:GIST_MAX]


def _quoted(body: str) -> set[str]:
    """Empreintes des lignes citées dans un message, chevrons retirés."""
    quotes = set()
    for line in (body or "").splitlines():
        if found := QUOTE.match(line):
            if gist := _gist(found.group(1)):
                quotes.add(gist)
    return quotes


def _quoted_target(quotes: set[str], earlier: list[tuple[int, Comment]]) -> int | None:
    """Position du message que cette citation désigne, ou rien si elle en désigne plusieurs.

    On garde les candidats qui partagent le plus de lignes avec la citation : une réponse citée
    recopie tout le message, donc le bon candidat se détache presque toujours. Une seule
    correspondance suffit, même sur une ligne courte : c'est l'unicité qui identifie, pas la
    longueur. À égalité, on ne tranche pas et les messages concernés restent en attente, plutôt
    que d'acquitter le mauvais.
    """
    scores: dict[int, int] = {}
    for position, comment in earlier:
        lines = {_gist(line) for line in comment.body.splitlines()}
        if shared := len(lines & quotes):
            scores[position] = shared
    if not scores:
        return None
    best = max(scores.values())
    winners = [position for position, shared in scores.items() if shared == best]
    return winners[0] if len(winners) == 1 else None


def _unanswered(humans: list[Comment], me: str, acks: set[str], only_named: bool = False) -> list[Comment]:
    """Messages des autres qu'aucun de mes messages postérieurs ne cite.

    La discussion générale d'une PR est une liste plate, pas un fil : y reprendre la parole ne
    répond à rien en particulier, et la règle « depuis ma dernière intervention » laissait
    tomber tout ce qui précédait. La citation à chevrons, que GitHub écrit quand on répond
    vraiment à un message, est le seul signal explicite disponible. Une réaction reste
    l'autre façon d'acter un point.
    """
    answered: set[int] = set()
    for index, comment in enumerate(humans):
        if comment.author != me:
            continue
        if quotes := _quoted(comment.body):
            earlier = [(position, c) for position, c in enumerate(humans[:index]) if c.author != me]
            if (target := _quoted_target(quotes, earlier)) is not None:
                answered.add(target)
    return [
        comment
        for position, comment in enumerate(humans)
        if comment.author != me
        and position not in answered
        and not acks.intersection(comment.my_reactions)
        and (not only_named or _names(comment.body, me))
    ]


def _pending(humans: list[Comment], me: str, acks: set[str], from_mention: bool = False) -> list[Comment]:
    """Messages des autres depuis ma dernière intervention, accusés de réception exclus.

    Un 👍 posé sur un message vaut réponse : le point est acté, il n'y a plus à y revenir.
    Quand je n'entre dans la conversation que parce qu'on m'y nomme, elle ne me concerne qu'à
    partir de la mention : ce qui la précède ne m'attendait pas.
    """
    spoke = [index for index, comment in enumerate(humans) if comment.author == me]
    if spoke:
        after = humans[spoke[-1] + 1 :]
    elif from_mention:
        # Je n'entre dans la conversation que parce qu'on m'y nomme : seuls ces messages
        # m'attendent. Compter tout ce qui suit gonflerait le badge d'un échange qui se
        # poursuit sans moi.
        after = [c for c in humans if _names(c.body, me)]
    else:
        after = humans
    return [c for c in after if c.author != me and not acks.intersection(c.my_reactions)]


ON_CODE = "bubble.left.and.bubble.right"
IN_DISCUSSION = "text.bubble"


def _flags(pr: PullRequest) -> tuple[tuple[str, str], ...]:
    """Drapeaux d'état de la PR, sans nombre : ils ne comptent pas de notification."""
    verdicts = set(pr.verdicts().values())
    flags: list[tuple[str, str]] = []
    if pr.is_draft:
        flags.append(("pencil.line", ""))
    if pr.mergeable == "CONFLICTING":
        flags.append(("arrow.triangle.branch", ""))
    if pr.ci_state in CI_BROKEN:
        flags.append(("xmark.octagon", ""))
    elif pr.ci_state == "PENDING":
        flags.append(("clock", ""))
    if "CHANGES_REQUESTED" in verdicts:
        flags.append(("arrow.uturn.left", ""))
    if "APPROVED" in verdicts:
        flags.append(("checkmark.seal", ""))
    return tuple(flags)


def _item(
    pr: PullRequest, kind: Kind, key: str, at: datetime, url: str = "", counted: tuple = (),
    note: str = "", **extra
) -> Item:
    """Une ligne : titre de la PR en haut, métadonnées uniformes en dessous.

    Métadonnées toujours identiques — dépôt simplifié, auteur de la PR, délai depuis la
    dernière action — pour que l'œil trouve la même information à la même place partout.

    `counted` porte les pastilles chiffrées, celles qui décomposent la pastille rouge. Le
    poids en est déduit, ce qui garantit par construction que les nombres de la ligne
    s'additionnent jusqu'au badge. Une action sans décomposition compte pour elle-même,
    avec l'icône de sa catégorie. Les drapeaux d'état viennent après, sans nombre.
    """
    conflict = pr.mergeable == "CONFLICTING"
    # Une ligne compte si elle est actionnable, ou si elle relève du suivi des PR clôturées :
    # celle-là porte son propre badge, le violet.
    compte = GROUPS[kind].is_action or bool(extra.get("closed"))
    if compte and not counted:
        counted = ((GROUPS[kind].symbol, 1),)
    numbered = tuple((symbol, str(number)) for symbol, number in counted if number)
    taken = {symbol for symbol, _ in numbered}
    # L'étiquette « conflit » dit déjà tout : pas de drapeau ⑄ en plus.
    if conflict:
        taken.add("arrow.triangle.branch")
    flags = tuple(flag for flag in _flags(pr) if flag[0] not in taken)
    return Item(
        id=f"{kind.value}:{key}",
        kind=kind,
        title=pr.title,
        detail=join(short_repo(pr.slug), f"@{pr.author}", since(at), note),
        url=url or pr.url,
        at=at,
        fingerprint=f"{key}:{at.isoformat()}",
        repo=pr.repo,
        weight=sum(number for _, number in counted) if compte else 0,
        chips=(numbered + flags)[:MAX_CHIPS],
        route=f"{pr.head} → {pr.base}" if pr.head and pr.base else "",
        tag="conflit" if conflict else "",
        **extra,
    )


def _messages(
    pr: PullRequest, me: str, ignored: set[str], acks: set[str], since=None, mine_closes: bool = False
) -> list[Item]:
    """Les messages de la PR, regroupés en une ligne par PR.

    Un commentaire de code et un commentaire général sont la même chose : un message. Il
    attend soit ma réponse, soit ma vérification quand c'est une réponse à ce que j'ai
    ouvert. Le troisième cas, j'ai parlé en dernier, est du suivi, pas une action.

    `since` ne retient que les messages postérieurs à une date, sans cacher le reste aux
    règles : la citation doit pouvoir retrouver un message d'avant la clôture pour l'acquitter.

    `mine_closes` rend mon dernier message clôturant, même sans citation. La règle de citation
    protège une demande enterrée par un échange qui continue ; sur une PR fermée plus rien ne
    continue, et la ligne resterait comptée sans aucun moyen de l'éteindre.
    """
    mine = pr.author == me

    def speech(comments: tuple[Comment, ...]) -> list[Comment]:
        """Les messages humains, mes commandes de bot exclues : elles ne répondent à rien."""
        return [c for c in _humans(comments, ignored) if not (c.author == me and _is_command(c.body))]

    to_answer: list[tuple[Comment, bool]] = []
    to_check: list[tuple[Comment, bool]] = []
    awaited: dict[str, Comment] = {}
    portraits: dict[str, str] = {}

    def collect(humans: list[Comment], opener: str, on_code: bool = True) -> None:
        last = humans[-1]
        speakers = {c.author for c in humans}
        # Me nommer me met dans la conversation : une question posée à moi attend une réponse
        # de moi, que je sois reviewer, simple spectateur, ou rien du tout.
        silent = me not in speakers and not mine
        if silent and not any(_names(c.body, me) for c in humans):
            return
        portraits.update({c.author: c.avatar for c in humans if c.avatar})
        # Un fil de code est une conversation : y répondre répond au fil. La discussion générale
        # est une liste plate, où seule une citation dit à quoi on répond.
        pending = (
            _pending(humans, me, acks, silent)
            if on_code or mine_closes
            else _unanswered(humans, me, acks, silent)
        )
        if since is not None:
            pending = [c for c in pending if c.created_at > since]
        if pending:
            # Le fil que j'ai ouvert est à moi de le clore : je lis la réponse puis je résous.
            (to_check if opener == me else to_answer).extend((comment, on_code) for comment in pending)
        elif last.author == me:
            # On attend l'autre partie : l'auteur sur la PR d'autrui, le reviewer sur la mienne.
            # Tous ceux dont on attend une réponse, pas seulement le premier : la ligne montre
            # leurs visages, et l'infobulle les nomme.
            for other in sorted(speakers - {me}) or ([pr.author] if not mine else []):
                awaited.setdefault(other, last)

    for thread in pr.threads:
        if thread.resolved:
            continue
        if humans := speech(thread.comments):
            collect(humans, thread.opener or humans[0].author)
    if humans := speech(pr.comments):
        collect(humans, humans[0].author, on_code=False)

    items: list[Item] = []

    def add(kind: Kind, batch: list[tuple[Comment, bool]], verb: str) -> None:
        if not batch:
            return
        comments = [comment for comment, _ in batch]
        oldest = min(comments, key=lambda comment: comment.created_at)
        # Une tête par personne concernée, dans l'ordre où elles ont pris la parole.
        speaking: dict[str, str] = {}
        for comment in sorted(comments, key=lambda comment: comment.created_at):
            speaking.setdefault(comment.author, comment.avatar)
        latest = max(comment.created_at for comment in comments)
        on_code = sum(1 for _, code in batch if code)
        general = len(batch) - on_code
        parts = [f"{on_code} sur le code"] if on_code else []
        if general:
            parts.append(f"{general} en discussion générale")
        who = ", ".join("@" + name for name in sorted({c.author for c in comments}))
        items.append(
            _item(
                pr, kind, pr.id, latest, oldest.url,
                counted=((ON_CODE, on_code), (IN_DISCUSSION, general)),
                avatar=oldest.avatar,
                faces=tuple(face for face in speaking.values() if face),
                hint=f"{len(batch)} message(s) de {who} {verb} : {', '.join(parts)}",
            )
        )

    add(Kind.MESSAGES_TO_ANSWER, to_answer, "en attente de ta réponse")
    add(Kind.REPLIES_TO_CHECK, to_check, "dans des fils que tu as ouverts, à vérifier puis résoudre")
    if awaited and not items:
        who, last = min(awaited.items(), key=lambda pair: pair[1].created_at)
        items.append(
            _item(
                pr, Kind.WAITING_REPLY, pr.id, last.created_at, last.url,
                avatar=portraits.get(who) or "",
                faces=tuple(portraits[name] for name in sorted(awaited) if portraits.get(name)),
                hint=f"tu as parlé en dernier, tu attends {', '.join('@' + n for n in sorted(awaited))}",
            )
        )
    return items


def _mine_items(pr: PullRequest, me: str, cfg: Config, talk: list[Item]) -> list[Item]:
    """Ma PR : ce qui la bloque, et ce qu'il me reste à faire pour la sortir."""
    pending_talk = any(item.group.is_action for item in talk)
    live = not pr.is_draft or cfg.drafts_are_actionable
    items: list[Item] = []

    def add(kind: Kind, hint: str, urgent: bool | None = None, note: str = "", who: tuple = ()) -> None:
        items.append(
            _item(
                pr, kind, pr.id, pr.updated_at, avatar=pr.avatar, urgent=urgent, hint=hint, note=note,
                faces=tuple(pr.portraits[name] for name in who if pr.portraits.get(name)),
            )
        )

    verdicts = pr.verdicts()
    approved = [who for who, state in verdicts.items() if state == "APPROVED"]
    refused = [who for who, state in verdicts.items() if state == "CHANGES_REQUESTED"]
    if refused:
        add(
            Kind.CHANGES_REQUESTED,
            f"{', '.join('@' + who for who in refused)} demande des changements",
            who=tuple(refused),
        )
    # Un draft en conflit n'est pas une action : c'est un choix de le laisser en draft. Le
    # conflit est signalé sur sa ligne, dans la section Draft.
    if pr.mergeable == "CONFLICTING" and not pr.is_draft:
        add(Kind.CONFLICTS, f"conflits avec {pr.base or 'la branche cible'}")
    if live and pr.ci_state in CI_BROKEN:
        add(Kind.CI_FAILING, "la CI du dernier commit est rouge")
    # Merger avant d'avoir répondu aux messages n'aurait pas de sens.
    if live and approved and not refused and not pending_talk and pr.ci_state in CI_OK and pr.mergeable == "MERGEABLE":
        add(
            Kind.READY_TO_MERGE,
            f"approuvée par {', '.join('@' + who for who in approved)}, CI verte, sans conflit",
            who=tuple(approved),
        )
    # Solliciter une review n'est pas le prochain geste si la PR est déjà bloquée.
    if live and not pr.reviewers and not pr.reviews and not items:
        add(Kind.NO_REVIEWER, "personne n'a été sollicité et aucune review n'a été posée")
    if items or talk:
        return items  # déjà listée : ne pas la répéter en informatif
    # Rien à faire : la PR me concerne quand même, on la garde sous les yeux.
    if pr.is_draft:
        conflict = pr.mergeable == "CONFLICTING"
        add(
            Kind.DRAFT,
            "draft : rien n'est attendu de toi tant qu'il n'est pas ouvert"
            + (f" — mais il est en conflit avec {pr.base}" if conflict else ""),
        )
    elif pr.reviewers:
        add(
            Kind.WAITING_REVIEW,
            f"chez {', '.join('@' + r.lstrip('@') for r in pr.reviewers)}",
            who=tuple(r.lstrip("@") for r in pr.reviewers),
        )
    elif pr.reviews:
        add(Kind.WAITING_REVIEW, "reviewée, aucun point ouvert")
    return items


def _others_items(pr: PullRequest, me: str, cfg: Config, talk: list[Item]) -> list[Item]:
    """PR de quelqu'un d'autre : à reviewer, ou déjà tranchée par moi."""
    verdict = pr.verdicts().get(me)
    my_review = pr.my_last_review(me)

    def add(kind: Kind, hint: str) -> Item:
        return _item(pr, kind, pr.id, pr.updated_at, avatar=pr.avatar, hint=hint)

    if verdict == "CHANGES_REQUESTED":
        pushed_since = bool(my_review and pr.last_commit_at and pr.last_commit_at > my_review.submitted_at)
        if pushed_since:
            return [add(Kind.REVIEW_AGAIN, "tu avais demandé des changements, l'auteur a poussé depuis")]
        return [add(Kind.CHANGES_REQUESTED_BY_ME, "tu as demandé des changements, rien de neuf depuis")]
    if verdict == "APPROVED":
        return [add(Kind.APPROVED_BY_ME, f"tu as approuvé : à @{pr.author} de merger")]
    # Un message en attente porte déjà l'action : ne pas la doubler d'un « à reviewer ».
    if any(item.group.is_action for item in talk):
        return []
    named = _named_out_of_sight(pr, me, cfg)
    if pr.mergeable == "CONFLICTING" and not named:
        # Rien à reviewer tant que l'auteur n'a pas rebasé. Une question posée à moi, elle,
        # n'attend pas le rebase : la mention passe devant.
        return [add(Kind.BLOCKED_FOR_AUTHOR, f"en conflit : à @{pr.author} de rebaser avant que tu reviewes")]
    if "toReview" in pr.sources:
        return [add(Kind.REVIEW_REQUESTED, f"review demandée nommément sur la PR de @{pr.author}")]
    if "assigned" in pr.sources:
        return [add(Kind.ASSIGNED, f"PR de @{pr.author} qui t'est assignée")]
    # Une review demandée ou une assignation dit déjà, et mieux, ce qu'on attend de moi : la
    # mention ne vient qu'après, sinon un « cc @moi » dans la description escamoterait la
    # sollicitation la plus forte.
    if named:
        return [add(Kind.MENTION, f"@{pr.author} t'a nommé dans cette PR")]
    # Ligne de repli seulement si aucune conversation n'est déjà listée pour cette PR.
    if not talk and (my_review or "commented" in pr.sources):
        return [add(Kind.WAITING_REPLY, "tu es déjà intervenu dans cette PR")]
    return []


def _mention_items(notifications: list[dict], known_urls: set[str], me: str) -> list[Item]:
    items: list[Item] = []
    for note in notifications:
        if note.get("reason") not in ("mention", "team_mention"):
            continue
        subject = note.get("subject") or {}
        if subject.get("type") not in ("PullRequest", "Issue"):
            continue
        repo = (note.get("repository") or {}).get("full_name") or ""
        url = _html_url(subject.get("url") or "", repo)
        if not url or url in known_urls:
            continue
        known_urls.add(url)
        extra = note.get("gittodo") or {}
        # Un sujet fermé ou mergé n'attend plus rien de personne, et le draft d'un autre ne me
        # concerne pas : les mêmes règles que pour les PR, que les recherches `is:open`
        # appliquent d'office.
        if (extra.get("state") or "OPEN") != "OPEN":
            continue
        if extra.get("draft") and extra.get("login") != me:
            continue
        # Sujet introuvable alors que le lot a répondu : supprimé, ou hors de portée du token.
        # Rien à y faire, donc rien à afficher. Une résolution en panne, elle, ne marque aucune
        # notification : tout reste alors visible, une mention perdue ne revenant jamais.
        if extra and not extra.get("resolved"):
            continue
        at = parse_ts(note.get("updated_at"))
        number = extra.get("number")
        author = extra.get("login")
        items.append(
            Item(
                id=f"{Kind.MENTION.value}:{note.get('id')}",
                kind=Kind.MENTION,
                title=subject.get("title") or "(sans titre)",
                detail=join(
                    short_repo(repo) + (f"#{number}" if number else ""),
                    f"@{author}" if author else "",
                    since(at),
                ),
                avatar=extra.get("avatar") or "",
                url=url,
                at=at,
                fingerprint=f"{note.get('id')}:{note.get('updated_at')}",
                repo=repo,
                chips=((GROUPS[Kind.MENTION].symbol, "1"),),
                hint=f"tu es mentionné dans {short_repo(repo)}",
            )
        )
    return items


def branch_items(branches: list) -> list[Item]:
    """Mes branches sans PR ouverte, réparties selon ce qu'il en reste à faire.

    Soit il y a du travail propre dessus et il attend une PR, soit il n'y a plus rien à en
    tirer — PR mergée, PR abandonnée, ou aucun commit en avance — et elle encombre.
    """
    items = []
    for branch in branches:
        if branch.resolved:
            continue  # mergée et rien en avance : plus aucune décision à prendre
        kind = Kind.BRANCH_TO_DELETE if branch.obsolete else Kind.ORPHAN_BRANCH
        items.append(
            Item(
                id=f"{kind.value}:{branch.key}",
                kind=kind,
                title=branch.name,
                detail=join(short_repo(branch.repo), since(branch.at), branch.note),
                url=branch.delete_url if branch.obsolete else branch.url,
                at=branch.at,
                fingerprint=f"{branch.key}:{branch.at.isoformat()}",
                repo=branch.repo,
                weight=0,
                chips=(("arrow.triangle.branch", ""),) if branch.status == "DIVERGED" else (),
                hint=(
                    f"{branch.note} — plus rien à en tirer, la supprimer"
                    if branch.obsolete
                    else f"branche poussée sans PR — {branch.note or 'à ouvrir en PR'}"
                ),
            )
        )
    return items


def _html_url(api_url: str, repo: str) -> str:
    tail = api_url.rsplit("/", 2)[-2:]
    if len(tail) != 2 or not tail[1].isdigit():
        return ""
    kind = "pull" if tail[0] == "pulls" else "issues"
    return f"https://github.com/{repo}/{kind}/{tail[1]}"


def closed_items(closures: list[Closure], me: str, cfg: Config) -> list[Item]:
    """Ce qui reste à faire, et ce qui s'est passé, sur les PR sorties du périmètre ouvert.

    Les messages postérieurs à la clôture repassent par `_messages()` : citation, acquittement
    par réaction et comptage s'appliquent sans être réécrits. Une clôture faite par quelqu'un
    d'autre devient une ligne d'histoire. Ce que j'ai clôturé moi-même s'affiche sans compter,
    puisque je le sais déjà.
    """
    ignored, acks = cfg.ignored(), cfg.acknowledged()
    # L'histoire est bornée par la fenêtre annoncée dans le titre de la section. Un message
    # tardif, lui, ne l'est pas : la recherche `involves:` porte sur la date de mise à jour, et
    # une question posée trois mois après un merge attend toujours une réponse.
    depuis = now() - timedelta(days=max(1, cfg.closed_days))
    actions: list[Item] = []
    histoire: list[Item] = []
    for closure in sorted(closures, key=lambda c: c.at, reverse=True):
        pr = closure.pr
        fait = "mergée" if closure.merged else "fermée sans merge"
        par = f"{fait} par @{closure.actor}" if closure.actor else fait
        for item in _messages(pr, me, ignored, acks, since=closure.at, mine_closes=True):
            # « J'ai parlé en dernier » n'est pas une information sur une PR fermée : personne
            # n'y répondra plus.
            if item.kind is Kind.WAITING_REPLY:
                continue
            actions.append(replace(item, closed=True, detail=join(item.detail, par)))
        if pr.author != me or closure.at < depuis:
            continue  # l'histoire, c'est celle de mes PR, dans la fenêtre annoncée
        histoire.append(
            _item(
                pr,
                Kind.RECENTLY_CLOSED,
                pr.id,
                closure.at,
                note=par,
                avatar=closure.actor_avatar or pr.avatar,
                hint=f"{par} le {closure.at:%d/%m à %H:%M}",
                closed=True,
                counted=((GROUPS[Kind.RECENTLY_CLOSED].symbol, 0 if closure.actor == me else 1),),
            )
        )
    # Aucun écrêtage ici : le plafond est un plafond d'affichage, tenu par le menu, qui sait
    # alors dire qu'il en reste. Le compte, lui, porte tout ce qui est arrivé.
    return actions + histoire


def build_items(
    prs: list[PullRequest],
    notifications: list[dict],
    me: str,
    cfg: Config,
    branches: list | None = None,
    closures: list | None = None,
) -> list[Item]:
    ignored = cfg.ignored()
    items: list[Item] = []
    for pr in prs:
        # Le brouillon de quelqu'un d'autre ne me concerne pas, quoi qu'il contienne :
        # tant qu'il n'est pas ouvert à la review, rien n'est attendu de moi.
        if pr.is_draft and pr.author != me:
            continue
        talk = _messages(pr, me, ignored, cfg.acknowledged())
        found = _mine_items(pr, me, cfg, talk) if pr.author == me else _others_items(pr, me, cfg, talk)
        items += talk + found
    if cfg.include_mentions:
        # Toutes les PR chargées, pas seulement celles qui ont produit une action : sinon la
        # boîte des non-lues rentre par la porte de service et ressuscite ce que les règles
        # ont écarté, un draft d'autrui ou un message déjà acquitté d'un 👍.
        covered = {i.url.split("#")[0] for i in items} | {pr.url for pr in prs}
        items += _mention_items(notifications, covered, me)
    if cfg.show_branches and branches:
        items += branch_items(branches)
    if cfg.show_closed and closures:
        items += closed_items(closures, me, cfg)
    if not cfg.show_waiting:
        items = [i for i in items if i.group.is_action]
    # Deux invariants, un par badge : la somme des pastilles rouges vaut le badge rouge, celle
    # des violettes le badge violet. Une ligne informative ne compte dans aucun des deux, sauf
    # l'histoire des clôtures, qui porte son propre compte jusqu'à ce qu'on l'ouvre.
    items = [
        item if (item.group.is_action or item.closed) else replace(item, weight=0) for item in items
    ]
    rank = {kind: index for index, kind in enumerate(ORDER)}
    items.sort(key=lambda i: (rank[i.kind], -i.at.timestamp()))
    return _dedupe(items)


def _dedupe(items: list[Item]) -> list[Item]:
    seen: set[str] = set()
    unique: list[Item] = []
    for item in items:
        if item.id in seen:
            continue
        seen.add(item.id)
        unique.append(item)
    return unique


def summarize(items: list[Item]) -> tuple[int, bool]:
    """Somme des notifications actionnables sur PR ouvertes, et présence d'une urgence."""
    actions = [i for i in items if i.group.is_action and not i.closed]
    return sum(i.weight for i in actions), any(i.is_urgent for i in actions)


def summarize_closed(items: list[Item]) -> int:
    """Somme du suivi des PR sorties du périmètre : c'est le badge violet."""
    return sum(i.weight for i in items if i.closed)


def now() -> datetime:
    return datetime.now(timezone.utc)
