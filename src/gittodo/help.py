"""Panneau « Comment ça marche ? » : le mode d'emploi complet, dans l'app.

Le texte est volontairement autoportant. Quelqu'un qui n'a jamais vu GitTodo doit pouvoir
comprendre d'où viennent les informations, avec quel compte, et comment tout se règle. Les
valeurs propres à l'installation (compte, origine du token, périmètre, chemins) y sont
injectées, pour que le panneau décrive la réalité et pas un exemple.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import objc
from Cocoa import (
    NSAttributedString,
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSMakeSize,
    NSMutableAttributedString,
    NSBox,
    NSBoxSeparator,
    NSMutableParagraphStyle,
    NSObject,
    NSParagraphStyleAttributeName,
    NSScrollView,
    NSTextView,
    NSView,
    NSViewHeightSizable,
    NSViewMaxXMargin,
    NSViewMinXMargin,
    NSViewMinYMargin,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)

from . import settings
from .config import CONFIG_PATH, STATE_PATH, Config
from .models import GROUPS, ORDER

# Deux volets : le formulaire tient dans une colonne étroite, le texte a besoin de largeur.
FORM_WIDTH = 380.0
DOC_WIDTH = 620.0
BAR_HEIGHT = 34.0
BAR_INSET = 14.0

TEXT = """
# GitTodo

Ce qu'il te reste à faire sur GitHub, dans la barre des menus. **Lecture seule** : aucune \
écriture sur GitHub.

## Cette installation

- Compte observé : {identity}
- Token fourni par : {token}
- Périmètre : {scope}
- Réglages : `{config}`
- État local : `{state}`
- Version {version}, code du {built}

## Compte et token

Aucun token n'est stocké. L'app en cherche un dans cet ordre, et s'arrête au premier trouvé.

- `token_command` : une commande qui imprime un token
- le fichier `{token_file}`
- `GITHUB_TOKEN`, sinon `GH_TOKEN`
- `gh auth token`, donc le compte de ton `gh auth login`

Droits utilisés : `repo` et `read:org`, en lecture. Une PR dans un dépôt hors de portée du \
token n'existe pas pour l'app.

## Sources

- PR : six recherches `is:open is:pr` dans une seule requête GraphQL, sur `author:`, \
`user-review-requested:`, `commenter:`, `reviewed-by:`, `assignee:` et `mentions:`. Chaque PR remonte ses \
`reviewThreads` avec `isResolved`, ses `comments`, ses `reactionGroups`, ses `reviews`, plus \
`mergeable`, `statusCheckRollup`, `reviewRequests`, `headRefName` et `baseRefName`
- Mentions : deux sources. La recherche `mentions:` porte les PR ouvertes et survit à la \
lecture, mais GitHub n'y indexe que la description et les commentaires généraux, pas les \
commentaires de revue en ligne. `GET /notifications` (raisons `mention` et `team_mention`) \
rattrape le reste : commentaires de revue, issues ouvertes, mentions d'équipe. Un sujet fermé \
ou mergé en est écarté comme partout ailleurs, ainsi que le draft de quelqu'un d'autre. Cette \
boîte est celle du token, donc rien en mode « voir en tant que », elle est lue sur cinq pages \
de cent au plus et le menu signale la troncature au-delà, et l'API ne donne ni auteur ni \
numéro : `resolve_mentions()` les complète en une requête d'alias pour tout le lot. Ouvrir la \
PR la marque lue côté GitHub et la retire de cette seconde source
- PR clôturées : deux recherches `is:closed`, sur `author:` pour l'histoire de tes PR et \
`involves:` pour ce qui s'y dit après, dans une requête mesurée à 3 points, sur sa propre \
cadence. Elles portent déjà `mergedBy` et l'acteur de la fermeture, lu dans la timeline faute de \
`closedBy`, donc aucune requête de détail n'est nécessaire. `review-requested:` est écartée à la \
mesure : 357 PR fermées y remontent sur trente jours, presque toutes par du bruit administratif
- Branches : `GET /repos/{{dépôt}}/activity?actor=` donne qui a poussé, \
`refs(refPrefix: "refs/heads/")` énumère les branches, `pullRequests(headRefName:)` donne l'état \
de leurs PR, `Ref.compare` l'écart à la branche par défaut
- Photos de profil : cache disque de quatorze jours

## Rythme

`rateLimit` accorde 5 000 points par heure. La requête complète des PR en coûte une quarantaine.

- toutes les 20 s (`refresh_seconds`), une sonde à 1 point lit `id` et `updatedAt` des mêmes PR. \
Empreinte identique, le cycle s'arrête là
- empreinte différente : requête complète
- toutes les 60 s au plus tard (`full_refresh_seconds`), requête complète forcée. Une réaction \
et un `isResolved` ne changent pas `updatedAt`, la sonde ne peut pas les voir
- chaque cycle revalide aussi l'affichage, branches comprises. Une branche supprimée sort en 20 s
- toutes les 5 min (`branch_refresh_seconds`), découverte des branches, dans son propre thread
- ⌘R relance tout, sans passer par la sonde
- sous 800 points restants, le rythme passe à 2 min et le menu le signale

Le menu se met à jour pendant qu'il est ouvert.

## Quand GitHub répond mal

Un triangle d'avertissement remplace la pastille de comptage, et une section en tête du menu dit \
quelle source ne répond pas, depuis quand, et ce que cela fausse. Le nombre disparaît pendant ce \
temps : un compte qu'on sait douteux vaut moins que pas de compte. Chaque ligne ouvre \
`githubstatus.com`.

Trois niveaux, du plus bénin au plus grave, à la couleur du triangle et au titre de la section :

- jaune, « données incomplètes » : rien n'est cassé chez GitHub, mais l'app ne peut plus tout \
lire. Quota épuisé, ou droits refusés au token
- orange, « GitHub répond mal » : une source auxiliaire tombe en erreur serveur ou reste \
injoignable, ou GitHub annonce un incident `minor`
- rouge, « GitHub en panne » : la requête des PR elle-même échoue, ou GitHub annonce un incident \
`major` ou `critical`

Le niveau croise nos propres échecs avec la gravité publiée par GitHub. Quand quelque chose \
échoue, et seulement dans ce cas, l'app lit `githubstatus.com/api/v2/status.json` au plus une \
fois toutes les deux minutes, sans jeton puisque c'est un autre hôte que l'API, et affiche la \
phrase officielle. Chaque nature d'échec a son remède : `quota épuisé` repart au réarmement \
horaire, `refusé` demande des droits au token, `erreur serveur` et `réseau injoignable` \
s'attendent. Une source auxiliaire qui tombe ne fait plus perdre le reste du cycle : les PR \
restent affichées, et le dernier balayage connu est conservé.

## Sections

Les premières attendent un geste et comptent dans la pastille rouge de la barre. Les suivantes \
non. Une PR sortie du périmètre ouvert compte à part, dans la pastille violette : les deux sommes \
valent chacune leur badge, et ne se mélangent jamais.

### À faire

{actions}

### Pour information

{waiting}

## Règles

- un commentaire de code et un commentaire général sont un même objet, un message. Tous ceux \
d'une PR tiennent sur une ligne
- un fil de code est une conversation : y répondre répond au fil, et ce qui suit ta dernière \
intervention reste en attente. La discussion générale est une liste plate : seule une citation \
à chevrons, celle que GitHub écrit avec « Quote reply », y marque un message comme traité. \
Reprendre la parole sans citer ne clôt rien, et une commande de bot comme `/run-e2e` n'est pas \
une prise de parole
- une citation désigne le message dont elle reprend le plus de lignes. Si deux messages \
correspondent autant l'un que l'autre, aucun n'est acquitté : mieux vaut deux lignes en attente \
qu'une demande effacée par erreur. Une réaction les départage
- être nommé met dans la conversation : un message qui écrit `@toi` attend ta réponse même si tu \
n'as jamais parlé dans cette PR. Seuls les messages qui te nomment comptent alors, pas la suite \
de l'échange, et un `@toi` dans un bloc de code ou dans une citation n'en est pas un
- une review demandée ou une assignation passe devant la mention : elle dit mieux ce qu'on \
attend. La section « Mentions » ne garde que ce qu'aucune conversation visible ne porte, la \
description de la PR au premier chef
- une réaction posée sur un message le sort du compte : un point acté n'attend plus rien. \
`acknowledge_reactions` fixe lesquelles, 👍 et 👎 par défaut, parce qu'un refus est une réponse. \
Pas 👀, qui dit qu'on a vu et non qu'on a tranché
- rien n'est compté deux fois. « À reviewer » cède devant un message en attente, un avis déjà \
rendu, ou `mergeable: CONFLICTING`. « Mes PR à merger » attend que les messages soient \
traités. « Mes PR sans reviewer » se tait sur une PR bloquée
- les pastilles chiffrées d'une ligne somment à sa pastille, les lignes au total de leur \
section, les sections d'action à la pastille de la barre. `_item()` déduit le poids de la \
décomposition
- le titre d'une section qui regarde une fenêtre de temps la porte, écrite comme les délais des \
lignes : deux plus grandes unités consécutives, donc « 1 mois » pour trente jours réglés
- un compte suivi d'un `+` est un plancher, pas un total : la liste est écrêtée, ou une recherche \
a buté sur `max_per_search`. Sans `+`, le compte est exact, y compris quand il tombe pile sur le \
plafond
- jamais affiché : une PR hors `is:open`, le draft d'un autre, une branche dont la PR est \
`MERGED` sans commit en avance, une branche avec une PR `OPEN`, la branche par défaut

## Une ligne

Photo de la personne concernée, ou l'icône de la section à défaut, surmontée du nombre de \
notifications. Toutes les vignettes font la même largeur, sinon les titres ne s'aligneraient \
plus d'une ligne à l'autre. Puis le titre de la PR. Puis des métadonnées constantes : dépôt et numéro, auteur, délai, pastilles d'état. Puis \
`headRefName → baseRefName`, qui révèle les PR empilées.

Le délai et les pastilles chiffrées prennent la couleur de la pastille de la ligne — rouge pour \
ce qu'il reste à faire, violet pour le suivi des PR clôturées — et restent gris sur une ligne qui \
ne compte rien. Les drapeaux d'état, eux, ne comptent rien et gardent leur gris. Le délai est \
l'information qu'on cherche en premier sur une ligne qui attend ; les pastilles chiffrées disent \
de quoi son compte est fait.

L'étiquette rouge `conflit` marque un `mergeable: CONFLICTING`. Le survol détaille le compte.

Clic : ouvrir au bon endroit. **⌥** : masquer jusqu'à la prochaine activité, en local. **⌘** : \
copier le lien. Le point ● marque ce qui est arrivé depuis la dernière ouverture.

## Voir en tant que

Les membres de l'org, les plus actifs en tête. Le token ne change pas, seules les recherches \
changent. Les mentions deviennent indisponibles. Tu ne vois que ce que ton token voit. Les \
éléments masqués sont mémorisés par identité.

## Réglages

À gauche. Chaque champ est écrit dans `{config}`, relu à chaud : l'enregistrement suffit, sans \
redémarrage. Le fichier se complète seul quand une option apparaît.

## Diagnostic

- `{status}` : ce que la barre affiche, avec la géométrie réelle de l'élément
- `{errors}` : les pannes et leur pile
- une seule instance à la fois. Sans donnée fraîche, l'icône passe en alerte et le pied du menu \
affiche « figé depuis »
- `python -m gittodo --print` en texte, `--as <login>` pour la vue d'un collègue

## Icône de la barre

Deux comptes, deux coins. La pastille rouge, en haut à droite, est ce qu'il reste à faire sur les \
PR ouvertes. La pastille violette, en bas à gauche, est le suivi des PR qui en sont sorties : les \
messages restés sans réponse dessus, et les clôtures faites par quelqu'un d'autre que toi, \
jusqu'à ce que tu ouvres la ligne. Le violet est la couleur dont GitHub colore une PR mergée.

Par défaut, la photo de l'identité observée, une pastille rouge, et un anneau qui se remplit \
dans le sens horaire jusqu'au prochain cycle. L'anneau s'efface pendant une lecture, où le \
compteur animé prend le relais, et quand une source ne répond plus, où le décompte n'a plus de \
sens. `show_refresh_ring` l'éteint. `badge_style` accepte aussi \
`count`, `icon_count` et `icon`, plus étroits quand la barre des menus est saturée. \
⌘-glisser l'icône vers la droite la met à l'abri des masquages de macOS.
"""

def _sections(action: bool) -> str:
    lines = []
    for kind in ORDER:
        group = GROUPS[kind]
        if group.is_action is action:
            urgent = " ; urgente, passe l'icône de la barre en alerte" if group.urgent else ""
            lines.append(f"- **{group.label}** : {DESCRIPTIONS[kind.value]}{urgent}")
    return "\n".join(lines)


DESCRIPTIONS = {
    "review_requested": "`user-review-requested:` sur la PR d'un autre, et tu n'as rien posé dessus",
    "review_again": "tu as demandé des changements, l'auteur a poussé depuis",
    "replies_to_check": "on a répondu dans un fil que tu as ouvert : à lire, puis à résoudre",
    "messages_to_answer": "des messages attendent ta réponse ; la pastille les compte",
    "mention": "on t'a nommé et tu n'as pas encore répondu ; une mention dans un message \
compte comme un message à traiter, cette section porte le reste",
    "changes_requested": "un reviewer a posé `CHANGES_REQUESTED` sur ta PR",
    "conflicts": "ta PR est `CONFLICTING` avec son `baseRefName`",
    "ci_failing": "`statusCheckRollup` du dernier commit de ta PR est rouge",
    "ready_to_merge": "ta PR est `APPROVED`, CI verte, `MERGEABLE`, sans message en attente",
    "no_reviewer": "ta PR est ouverte, aucun `reviewRequests`, aucune `reviews`, rien qui la bloque",
    "assigned": "`assignee:` toi sur la PR d'un autre",
    "waiting_review": "ta PR est chez ses `reviewRequests`, rien d'ouvert de ton côté",
    "blocked_for_author": "la PR d'un autre est `CONFLICTING` : à lui de rebaser avant ta relecture",
    "waiting_reply": "tu as parlé en dernier, ou tu es intervenu dans la PR",
    "approved_by_me": "tu as posé `APPROVED` : à son auteur de merger",
    "changes_requested_by_me": "tu as posé `CHANGES_REQUESTED`, rien de neuf depuis",
    "draft": "ta PR est en draft, c'est un choix ; un conflit y est signalé sans devenir une action",
    "orphan_branch": "une branche que tu as poussée porte du travail qu'aucune PR ne soumet",
    "branch_to_delete": "PR `MERGED`, PR `CLOSED` sans merge, ou aucun commit en avance sur la cible",
    "recently_closed": "une de tes PR est sortie du périmètre ouvert ; la ligne compte en violet \
tant que tu ne l'as pas ouverte, et jamais si c'est toi qui as clôturé",
}


def built_at() -> str:
    """Date du code réellement exécuté, pour repérer un bundle installé resté en arrière.

    Le numéro de version ne bouge pas à chaque modification : une copie périmée afficherait
    la même. La date des sources chargées, elle, la trahit.
    """
    stamps = [source.stat().st_mtime for source in Path(__file__).parent.glob("*.py")]
    return datetime.fromtimestamp(max(stamps)).strftime("%d/%m/%Y à %H:%M") if stamps else "inconnue"


def document(context: dict) -> str:
    from . import VERSION
    from .github import TOKEN_FILE

    identity = context.get("identity") or "inconnu"
    viewer = context.get("viewer") or "inconnu"
    scope = ", ".join(context.get("scope") or []) or "tous les dépôts accessibles"
    return TEXT.format(
        identity=f"@{identity}" + (f", observé avec le token de @{viewer}" if identity != viewer else ""),
        token=context.get("token") or "aucun token trouvé",
        scope=scope,
        config=CONFIG_PATH,
        state=STATE_PATH,
        status=STATE_PATH.with_name("status.json"),
        errors=STATE_PATH.with_name("errors.log"),
        token_file=TOKEN_FILE,
        version=VERSION,
        built=built_at(),
        actions=_sections(True),
        waiting=_sections(False),
    )


BULLET = "•   "
_INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`)")


def _style(before: float = 0.0, indent: float = 0.0) -> NSMutableParagraphStyle:
    style = NSMutableParagraphStyle.alloc().init()
    style.setParagraphSpacingBefore_(before)
    style.setParagraphSpacing_(2.0)
    style.setLineSpacing_(2.0)
    if indent:
        style.setHeadIndent_(indent)
    return style


def _inline(line: str, size: float, weight: float, colour, style) -> NSMutableAttributedString:
    """Rend une ligne en gérant **gras** et `code`, le reste étant du texte courant."""
    out = NSMutableAttributedString.alloc().init()
    for piece in _INLINE.split(line):
        if not piece:
            continue
        font = NSFont.systemFontOfSize_weight_(size, weight)
        shade = colour
        if piece.startswith("**") and piece.endswith("**"):
            piece, font = piece[2:-2], NSFont.systemFontOfSize_weight_(size, NSFontWeightSemibold)
        elif piece.startswith("`") and piece.endswith("`"):
            piece = piece[1:-1]
            font = NSFont.monospacedSystemFontOfSize_weight_(size - 1.0, NSFontWeightRegular)
            shade = NSColor.secondaryLabelColor()
        out.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                piece,
                {
                    NSFontAttributeName: font,
                    NSForegroundColorAttributeName: shade,
                    NSParagraphStyleAttributeName: style,
                },
            )
        )
    return out


def render(document_text: str) -> NSMutableAttributedString:
    body = NSMutableAttributedString.alloc().init()
    for raw in document_text.strip("\n").split("\n"):
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("# "):
            piece = _inline(line[2:], 22.0, NSFontWeightSemibold, NSColor.labelColor(), _style(2.0))
        elif line.startswith("## "):
            piece = _inline(line[3:], 15.0, NSFontWeightSemibold, NSColor.labelColor(), _style(22.0))
        elif line.startswith("### "):
            piece = _inline(line[4:], 12.5, NSFontWeightSemibold, NSColor.labelColor(), _style(14.0))
        elif line.startswith("- "):
            piece = _inline(BULLET + line[2:], 12.0, NSFontWeightRegular, NSColor.labelColor(), _style(3.0, 20.0))
        else:
            piece = _inline(line, 12.0, NSFontWeightRegular, NSColor.labelColor(), _style(8.0))
        body.appendAttributedString_(piece)
        body.appendAttributedString_(NSAttributedString.alloc().initWithString_("\n"))
    return body


class Panel(NSObject):
    """Fenêtre unique : le mode d'emploi à droite, les réglages modifiables à gauche.

    Les deux traitaient du même sujet dans deux endroits séparés, dont un fichier JSON à
    éditer à la main. Le bouton d'enregistrement n'apparaît qu'une fois quelque chose modifié :
    tant qu'il est absent, il n'y a rien à valider.
    """

    def initWithContext_(self, context):
        self = objc.super(Panel, self).init()
        if self is None:
            return None
        self.form = settings.SettingsForm.alloc().initWithConfig_onDirty_(Config.load(), self.dirty)
        self.window = self._window(context)
        return self

    @objc.python_method
    def dirty(self, changed: bool) -> None:
        self.save.setHidden_(not changed)

    def save_(self, sender):
        self.form.commit()
        self.save.setHidden_(True)

    def windowWillClose_(self, notification):
        # Des modifications en attente survivent à la fermeture : rien ne doit disparaître
        # sans avoir été enregistré ni jeté explicitement. Sinon on repart des valeurs du
        # fichier, qui ont pu changer entre-temps.
        if not self.form.changed():
            _ALIVE.pop("panel", None)

    @objc.python_method
    def _window(self, context: dict):
        frame = NSMakeRect(0, 0, FORM_WIDTH + DOC_WIDTH, 760)
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered,
            False,
        )
        window.setTitle_("GitTodo, mode d'emploi et réglages")
        window.setReleasedWhenClosed_(False)
        window.setMinSize_(NSMakeSize(FORM_WIDTH + 320, 420))
        window.setDelegate_(self)

        content = NSView.alloc().initWithFrame_(frame)
        width, height = frame.size.width, frame.size.height
        bar = NSView.alloc().initWithFrame_(NSMakeRect(0, height - BAR_HEIGHT, width, BAR_HEIGHT))
        bar.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
        where = settings._label(
            str(CONFIG_PATH),
            NSMakeRect(BAR_INSET, 9.0, width - 2 * BAR_INSET - 40.0, 16.0),
            10.0,
            NSFontWeightRegular,
            NSColor.tertiaryLabelColor(),
        )
        where.setFont_(NSFont.monospacedSystemFontOfSize_weight_(9.5, NSFontWeightRegular))
        bar.addSubview_(where)
        self.save = settings.save_button(self, "save:")
        self.save.setFrame_(NSMakeRect(width - BAR_INSET - 30.0, 5.0, 30.0, 24.0))
        self.save.setAutoresizingMask_(NSViewMinXMargin)
        bar.addSubview_(self.save)
        content.addSubview_(bar)
        content.addSubview_(_rule(NSMakeRect(0, height - BAR_HEIGHT, width, 1.0), NSViewWidthSizable | NSViewMinYMargin))

        body_height = height - BAR_HEIGHT
        left = _scroller(NSMakeRect(0, 0, FORM_WIDTH, body_height))
        left.setAutoresizingMask_(NSViewHeightSizable | NSViewMaxXMargin)
        left.setDocumentView_(self.form.build(FORM_WIDTH - 15.0))
        content.addSubview_(left)

        doc = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, width - FORM_WIDTH, body_height))
        doc.setEditable_(False)
        doc.setSelectable_(True)
        doc.setDrawsBackground_(False)
        doc.setTextContainerInset_(NSMakeSize(22.0, 22.0))
        doc.setHorizontallyResizable_(False)
        doc.setAutoresizingMask_(NSViewWidthSizable)
        doc.textContainer().setWidthTracksTextView_(True)
        doc.textStorage().setAttributedString_(render(document(context)))
        right = _scroller(NSMakeRect(FORM_WIDTH, 0, width - FORM_WIDTH, body_height))
        right.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        right.setDocumentView_(doc)
        content.addSubview_(right)
        content.addSubview_(
            _rule(NSMakeRect(FORM_WIDTH - 1, 0, 1.0, body_height), NSViewHeightSizable | NSViewMaxXMargin)
        )

        window.setContentView_(content)
        window.center()
        return window


def _rule(box, mask):
    """Filet de séparation : sans lui, les deux volets et le bandeau flottent sans limite."""
    line = NSBox.alloc().initWithFrame_(box)
    line.setBoxType_(NSBoxSeparator)
    line.setAutoresizingMask_(mask)
    return line


def _scroller(box):
    scroll = NSScrollView.alloc().initWithFrame_(box)
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setDrawsBackground_(False)
    return scroll


# Le contrôleur doit survivre à l'appel : sans cette référence, il serait ramassé et les
# boutons n'auraient plus de cible.
_ALIVE: dict = {}


def panel(context: dict):
    """Fenêtre du mode d'emploi et des réglages, créée à la demande."""
    live = _ALIVE.get("panel")
    if live is None:
        live = Panel.alloc().initWithContext_(context)
        _ALIVE["panel"] = live
    return live
