"""Élément de barre des menus : badge, menu détaillé, rafraîchissement périodique."""

from __future__ import annotations

import sys
import threading
import traceback
from dataclasses import replace

import objc
from Cocoa import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAttributedString,
    NSBaselineOffsetAttributeName,
    NSBezierPath,
    NSBundle,
    NSColor,
    NSCompositingOperationSourceOver,
    NSEventModifierFlagCommand,
    NSEventModifierFlagOption,
    NSFont,
    NSFontAttributeName,
    NSFontWeightLight,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSForegroundColorAttributeName,
    NSImage,
    NSImageLeft,
    NSImageSymbolConfiguration,
    NSImageSymbolScaleSmall,
    NSMakeRange,
    NSMakeRect,
    NSMakeSize,
    NSMenu,
    NSMenuItem,
    NSMutableAttributedString,
    NSMutableParagraphStyle,
    NSObject,
    NSParagraphStyleAttributeName,
    NSPasteboard,
    NSPasteboardTypeString,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSScreen,
    NSStatusBar,
    NSTextAttachment,
    NSTimer,
    NSURL,
    NSVariableStatusItemLength,
    NSWorkspace,
    NSZeroRect,
)
from PyObjCTools import AppHelper

from . import branches, launchagent
from . import help as manual
from .avatars import BAR_SIZE, SIZE as AVATAR_SIZE, Avatars
from .config import CONFIG_PATH, Config
from .engine import build_items, now, summarize
from .formatting import ago, countdown, join, since, truncate
from .github import GitHub, GitHubError, service_status, signature_of
from .models import GROUPS, ORDER, Item, Person, Snapshot
from .state import State, acquire_single_instance, log_error, write_status

BUNDLE_ID = "fr.jsebire.gittodo"
TICK_SECONDS = 1.0
# Sous ce reste de quota GraphQL, on espace les requêtes plutôt que de tomber en erreur.
QUOTA_FLOOR = 800
THROTTLED_SECONDS = 120
# Au-delà de ce silence, l'app le dit au lieu d'afficher des données périmées en silence.
FROZEN_AFTER = 120.0
# Un cycle qui ne rend jamais la main : au-delà, on relâche le drapeau de force.
STUCK_AFTER = 90.0
MAX_ROWS_PER_GROUP = 15
# Toutes les images font la même largeur : le badge ne tremble pas pendant l'animation.
SPINNER_FRAMES = ("◐", "◓", "◑", "◒")
SPINNER_INTERVAL = 0.13
TITLE_WIDTH = 70
DETAIL_WIDTH = 84
ROUTE_WIDTH = 92
# Échelle typographique : un rôle, une taille, un seul endroit pour la changer.
TITLE_FONT = 13.0
META_FONT = 11.0
ROUTE_FONT = 10.0
HEADER_FONT = 10.0
BAR_FONT = 12.0
# Glyphes SF, du plus large (barre des menus) au plus étroit (pastilles dans le texte).
GLYPH_BAR = 15.0
GLYPH_ROW = 14.0
GLYPH_HEADER = 12.0
# Icônes de chrome (titres de section, pied de menu) : petites et en trait fin. Elles repèrent
# la ligne sans venir concurrencer son texte.
CHROME_GLYPH = 11.0
# Marge de gauche commune aux icônes de chrome et aux vignettes des lignes : la même constante
# pour les deux, sinon les deux colonnes se désaligneraient au premier réglage de l'une.
LEFT_MARGIN = 6.0
GLYPH_CHIP = 10.0
# Étiquette de texte (« conflit ») et pastille de comptage : même primitive, deux géométries.
TAG_HEIGHT, TAG_RADIUS, TAG_PADDING = 13.0, 3.0, 10.0
COUNT_HEIGHT, COUNT_RADIUS, COUNT_PADDING = 10.0, 5.0, 4.0
LABEL_FONT = 9.0
COUNT_FONT = 8.0
# Recouvrement de la pastille sur la photo : franc, pour qu'elle dépasse à peine et laisse le
# texte se rapprocher de la vignette.
COUNT_OVERLAP = 8.0
# Sources de données, et ce qu'une panne de chacune fausse dans le menu. C'est ce texte que
# lit quelqu'un qui voit le cône de chantier et se demande à quoi il ne peut pas se fier.
SOURCES = {
    "pull_requests": ("PR et conversations", "tout est figé, rien n'est à jour"),
    "notifications": ("boîte des non-lues", "une mention peut manquer"),
    "mentions": ("sujets des mentions", "une mention fermée peut rester affichée"),
    "branches": ("branches distantes", "les branches peuvent être périmées"),
    "people": ("annuaire de l'organisation", "« voir en tant que » est incomplet"),
}
# Triangle d'avertissement posé à la place de la pastille de comptage : le rouge de la pastille
# compte ce qu'il y a à faire, celui-ci dit que le compte lui-même n'est pas fiable.
ALERT_SIZE = 14.0
# Anneau de progression autour de la photo dans la barre. La barre fait 22 pt de haut : 21 pt
# de diamètre extérieur laissent un demi-point de marge de chaque côté, et un trait fin suffit
# à le lire sans manger la photo.
RING_SIZE = 21.0
RING_WIDTH = 1.5
# Le remplissage avance par pas d'une seconde : inutile de redessiner plus souvent que le tic.
RING_STEPS = 60
# Trois niveaux, du moins au plus grave, avec le mot qui titre la section et la couleur du
# triangle. Le niveau croise nos propres échecs et la gravité annoncée par GitHub.
LEVELS = {
    "attention": ("DONNÉES INCOMPLÈTES", "systemYellowColor"),
    "alerte": ("GITHUB RÉPOND MAL", "systemOrangeColor"),
    "critique": ("GITHUB EN PANNE", "systemRedColor"),
}
# Gravité officielle de githubstatus.com traduite dans nos niveaux.
OFFICIAL = {"critical": "critique", "major": "critique", "minor": "alerte"}
# La page d'état est relue rarement, et seulement quand quelque chose échoue déjà.
SERVICE_REFRESH = 120.0
# Ce qu'on écrit quand il n'y a pas de code HTTP à montrer.
CODES = {
    "quota": "quota épuisé",
    "panne": "erreur serveur",
    "refus": "refusé",
    "réseau": "réseau injoignable",
    "erreur": "erreur",
}
# Respiration entre la vignette et le texte : le titre d'un élément de menu démarre juste après
# la colonne d'image, sans marge propre, et les avatars y touchaient presque les lettres.
FACE_GAP = 3.0
# Place réservée à la pastille de comptage, occupée ou non : la zone de tête garde ainsi la même
# largeur d'une ligne à l'autre, et le texte ne se déplace pas quand un compte apparaît. Elle
# reste courte parce que la pastille mord franchement sur la photo, à la façon d'un badge.
COUNT_RESERVE = 5.0
# Pile de visages : décalage, nombre maximum affiché, et cercle de séparation.
STACK_STEP = 12.0
STACK_MAX = 3
RING_WIDTH = 1.2


def _symbol(name: str, size: float):
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if image is None:
        return None
    image.setTemplate_(True)
    image.setSize_(NSMakeSize(size, size))
    return image


_BOXED: dict[tuple, object] = {}


def _boxed_symbol(name: str, size: float = AVATAR_SIZE, tinted: bool = False):
    """Symbole centré dans un carré de la taille d'un avatar, pour aligner les textes.

    `tinted` l'aplatit dans la couleur du texte : un template ne se compose pas, il
    ressortirait noir sur fond sombre dès qu'on pose une pastille dessus. La couleur est
    résolue sous l'apparence effective de l'application, celle que suit le menu, sans quoi
    un dessin hors fenêtre la résoudrait à l'envers et le symbole disparaîtrait. Le résultat
    n'est donc pas mis en cache, le menu étant de toute façon reconstruit à chaque ouverture.
    """
    if not tinted and (name, size) in _BOXED:
        return _BOXED[(name, size)]
    glyph = min(GLYPH_ROW, size)
    symbol = _symbol(name, glyph)
    if symbol is None:
        return None
    canvas = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    offset = (size - glyph) / 2

    def paint():
        drawn = symbol
        if tinted:
            drawn = symbol.imageWithSymbolConfiguration_(
                NSImageSymbolConfiguration.configurationWithPaletteColors_([NSColor.labelColor()])
            )
        canvas.lockFocus()
        drawn.drawInRect_fromRect_operation_fraction_(
            NSMakeRect(offset, offset, glyph, glyph), NSZeroRect, NSCompositingOperationSourceOver, 1.0
        )
        canvas.unlockFocus()

    NSApplication.sharedApplication().effectiveAppearance().performAsCurrentDrawingAppearance_(paint)
    if tinted:
        return canvas
    canvas.setTemplate_(True)
    _BOXED[(name, size)] = canvas
    return canvas


_CHROME: dict[str, object] = {}


def _chrome_symbol(name: str):
    """Symbole de chrome : trait fin, petite taille, précédé de la marge de gauche.

    La marge se fait en dessinant le glyphe dans une image plus large que lui, la position dans
    un menu n'étant pas réglable autrement. L'image reste un gabarit, donc AppKit continue de la
    teinter avec le texte, y compris en blanc sur une ligne survolée.
    """
    if name not in _CHROME:
        symbol = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        if symbol is None:
            return None
        thin = symbol.imageWithSymbolConfiguration_(
            NSImageSymbolConfiguration.configurationWithPointSize_weight_scale_(
                CHROME_GLYPH, NSFontWeightLight, NSImageSymbolScaleSmall
            )
        )
        span = thin.size()
        canvas = NSImage.alloc().initWithSize_(NSMakeSize(span.width + LEFT_MARGIN, span.height))
        canvas.lockFocus()
        thin.drawInRect_fromRect_operation_fraction_(
            NSMakeRect(LEFT_MARGIN, 0, span.width, span.height),
            NSZeroRect,
            NSCompositingOperationSourceOver,
            1.0,
        )
        canvas.unlockFocus()
        canvas.setTemplate_(True)
        _CHROME[name] = canvas
    return _CHROME[name]


_LABELS: dict[tuple, object] = {}


def _red_label(text: str, height: float, radius: float, padding: float, font: float = LABEL_FONT):
    """Texte blanc sur fond rouge : la seule chose d'une ligne qui doive crier.

    Dessiné en image plutôt qu'en texte coloré, parce que c'est le fond qui le rend
    repérable d'un coup d'œil dans une liste, comme les labels de GitHub. Une seule
    primitive pour l'étiquette de texte et pour la pastille de comptage, à la géométrie près.
    """
    key = (text, height, radius, padding, font)
    if key not in _LABELS:
        glyph = _run(text, font, color=NSColor.whiteColor(), weight=NSFontWeightSemibold)
        measured = glyph.size()
        width = max(height, measured.width + padding)
        canvas = NSImage.alloc().initWithSize_(NSMakeSize(width, height))
        canvas.lockFocus()
        NSColor.systemRedColor().setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, width, height), radius, radius
        ).fill()
        glyph.drawAtPoint_(((width - measured.width) / 2, (height - measured.height) / 2))
        canvas.unlockFocus()
        _LABELS[key] = canvas
    return _LABELS[key]


def _with_count(base, count: str, size: float):
    """Pastille de comptage en débord à droite de l'image, façon badge d'application.

    En débord plutôt que posée dessus : elle ne cache alors aucun visage, et la largeur
    supplémentaire ne coûte rien à la mise en page du menu.
    """
    if base is None or not count:
        return base
    pill = _red_label(count, COUNT_HEIGHT, COUNT_RADIUS, COUNT_PADDING, COUNT_FONT)
    span = pill.size()
    inner = base.size().width
    width = inner + span.width - COUNT_OVERLAP
    canvas = NSImage.alloc().initWithSize_(NSMakeSize(width, size))
    canvas.lockFocus()
    base.drawInRect_fromRect_operation_fraction_(
        NSMakeRect(0, 0, inner, size), NSZeroRect, NSCompositingOperationSourceOver, 1.0
    )
    pill.drawInRect_fromRect_operation_fraction_(
        NSMakeRect(width - span.width, size - span.height, span.width, span.height),
        NSZeroRect,
        NSCompositingOperationSourceOver,
        1.0,
    )
    canvas.unlockFocus()
    return canvas


def _alert_symbol(level: str, size: float):
    """Triangle d'avertissement plein dans la couleur du niveau, point d'exclamation en blanc.

    Deux couleurs de palette : avec une seule, le symbole est rempli d'un bloc et le point
    d'exclamation disparaît. Ces teintes sont fixes, donc insensibles au thème.
    """
    glyph = _symbol("exclamationmark.triangle.fill", size)
    if glyph is None:
        return None
    tint = getattr(NSColor, LEVELS.get(level, LEVELS["alerte"])[1])()
    return glyph.imageWithSymbolConfiguration_(
        NSImageSymbolConfiguration.configurationWithPaletteColors_([NSColor.whiteColor(), tint])
    )


def _with_ring(face, fraction: float, size: float = RING_SIZE):
    """Photo entourée d'un anneau qui se remplit dans le sens horaire jusqu'au prochain cycle.

    Les couleurs sont résolues sous l'apparence effective de l'application : un dessin hors
    fenêtre les résoudrait à l'envers, et l'anneau disparaîtrait sur une barre claire.
    """
    if face is None:
        return None
    inner = size - 2 * (RING_WIDTH + 0.5)
    canvas = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    middle = size / 2

    def paint():
        canvas.lockFocus()
        face.drawInRect_fromRect_operation_fraction_(
            NSMakeRect(middle - inner / 2, middle - inner / 2, inner, inner),
            NSZeroRect,
            NSCompositingOperationSourceOver,
            1.0,
        )
        radius = (size - RING_WIDTH) / 2
        track = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(RING_WIDTH / 2, RING_WIDTH / 2, size - RING_WIDTH, size - RING_WIDTH)
        )
        track.setLineWidth_(RING_WIDTH)
        NSColor.quaternaryLabelColor().setStroke()
        track.stroke()
        if fraction > 0:
            arc = NSBezierPath.bezierPath()
            # Départ à midi, dans le sens horaire : c'est le sens d'un cadran.
            arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                (middle, middle), radius, 90.0, 90.0 - 360.0 * min(1.0, fraction), True
            )
            arc.setLineWidth_(RING_WIDTH)
            NSColor.secondaryLabelColor().setStroke()
            arc.stroke()
        canvas.unlockFocus()

    NSApplication.sharedApplication().effectiveAppearance().performAsCurrentDrawingAppearance_(paint)
    return canvas


def _with_alert(base, size: float, level: str):
    """Avertissement en débord, à la place de la pastille de comptage.

    Il la remplace au lieu de s'y ajouter : afficher un nombre qu'on sait douteux serait pire
    que ne rien afficher. Posé à nu, sans disque derrière : le triangle plein et son point
    d'exclamation blanc se lisent déjà seuls, et le disque alourdissait la barre.
    """
    if base is None:
        return None
    glyph = _alert_symbol(level, ALERT_SIZE)
    if glyph is None:
        return base
    span = glyph.size()
    inner = base.size().width
    width = inner + span.width - COUNT_OVERLAP
    canvas = NSImage.alloc().initWithSize_(NSMakeSize(width, size))
    canvas.lockFocus()
    base.drawInRect_fromRect_operation_fraction_(
        NSMakeRect(0, 0, inner, size), NSZeroRect, NSCompositingOperationSourceOver, 1.0
    )
    glyph.drawInRect_fromRect_operation_fraction_(
        NSMakeRect(width - span.width, size - span.height, span.width, span.height),
        NSZeroRect,
        NSCompositingOperationSourceOver,
        1.0,
    )
    canvas.unlockFocus()
    return canvas


def _stack(faces, size: float):
    """Visages décalés vers la droite, cerclés pour rester distincts là où ils se chevauchent.

    Le cercle est d'un gris moyen plutôt que de la couleur du fond : la ligne survolée passe
    en bleu, un contour couleur de fond y trahirait la découpe.
    """
    if len(faces) < 2:
        return faces[0] if faces else None
    canvas = NSImage.alloc().initWithSize_(NSMakeSize(size + (len(faces) - 1) * STACK_STEP, size))
    canvas.lockFocus()
    for index, face in enumerate(faces):
        box = NSMakeRect(index * STACK_STEP, 0, size, size)
        face.drawInRect_fromRect_operation_fraction_(box, NSZeroRect, NSCompositingOperationSourceOver, 1.0)
        NSColor.colorWithWhite_alpha_(0.55, 0.85).setStroke()
        ring = NSBezierPath.bezierPathWithOvalInRect_(box)
        ring.setLineWidth_(RING_WIDTH)
        ring.stroke()
    canvas.unlockFocus()
    return canvas


ROW_ZONE = LEFT_MARGIN + AVATAR_SIZE + COUNT_RESERVE + FACE_GAP


def _padded(image, left: float = LEFT_MARGIN, right: float = FACE_GAP):
    """Zone de tête d'une ligne, de largeur fixe, contenu calé après la marge de gauche.

    Largeur imposée pour que toutes les lignes se ressemblent, quoi qu'elles portent : une
    photo, un triangle, une icône, avec ou sans pastille. Seule une pile de plusieurs visages
    la dépasse, puisqu'elle a besoin de cette place pour exister.
    """
    if image is None or left + right <= 0:
        return image
    span = image.size()
    width = max(ROW_ZONE, span.width + left + right)
    canvas = NSImage.alloc().initWithSize_(NSMakeSize(width, span.height))
    canvas.lockFocus()
    image.drawInRect_fromRect_operation_fraction_(
        NSMakeRect(left, 0, span.width, span.height), NSZeroRect, NSCompositingOperationSourceOver, 1.0
    )
    canvas.unlockFocus()
    # Un gabarit doit le rester : sinon AppKit cesse de le teinter avec le texte.
    canvas.setTemplate_(image.isTemplate())
    return canvas


def _row_image(content, size: float = AVATAR_SIZE):
    """Zone de tête d'une ligne : toujours la même largeur, contenu centré dedans.

    Avatar, triangle d'alerte ou simple symbole passent par ici, sans quoi chacun se collerait
    où bon lui semble dans la colonne d'image et les textes des lignes ne partiraient plus du
    même endroit.
    """
    if content is None:
        return None
    span = content.size()
    if abs(span.width - size) > 0.5 or abs(span.height - size) > 0.5:
        canvas = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
        canvas.lockFocus()
        content.drawInRect_fromRect_operation_fraction_(
            NSMakeRect((size - span.width) / 2, (size - span.height) / 2, span.width, span.height),
            NSZeroRect,
            NSCompositingOperationSourceOver,
            1.0,
        )
        canvas.unlockFocus()
        canvas.setTemplate_(content.isTemplate())
        content = canvas
    return _padded(content)


def _face(avatars, urls, symbol: str, count: str = "", size: float = AVATAR_SIZE):
    """Vignette de gauche : les personnes concernées, et le compte de ce qu'elles attendent.

    Plusieurs personnes sur une même action donnent une pile décalée, la première à avoir
    parlé devant. Le compte ne dépend pas des photos : une ligne sans portrait garde son
    nombre, c'est lui qui dit ce qu'il y a à faire.
    """
    if isinstance(urls, str):
        urls = (urls,)
    faces = [image for image in (avatars.image(url, size) for url in tuple(urls)[:STACK_MAX] if url) if image]
    base = _stack(faces, size) or _boxed_symbol(symbol, size, tinted=bool(count))
    return _padded(_with_count(base, count, size))


def _attachment(image, offset: float):
    holder = NSTextAttachment.alloc().init()
    holder.setImage_(image)
    piece = NSMutableAttributedString.alloc().initWithAttributedString_(
        NSAttributedString.attributedStringWithAttachment_(holder)
    )
    piece.addAttribute_value_range_(NSBaselineOffsetAttributeName, offset, NSMakeRange(0, piece.length()))
    return piece


def _chip_run(chips):
    """Pastilles d'état inline : icône SF + nombre, dans la couleur secondaire du thème.

    La configuration de palette laisse AppKit résoudre la couleur au dessin, donc les
    icônes suivent le passage clair/sombre sans être recalculées.
    """
    run = NSMutableAttributedString.alloc().init()
    for name, label in chips:
        image = _symbol(name, GLYPH_CHIP)
        if image is None:
            continue
        tinted = image.imageWithSymbolConfiguration_(
            NSImageSymbolConfiguration.configurationWithPaletteColors_([NSColor.secondaryLabelColor()])
        )
        run.appendAttributedString_(_attachment(tinted, -1.0))
        run.appendAttributedString_(
            _run((f" {label}" if label else "") + "   ", META_FONT, color=NSColor.secondaryLabelColor())
        )
    return run


def _run(text: str, size: float, color=None, weight=None, paragraph=None):
    """Fragment de texte : tout le style du menu passe par ici, une seule fois par rôle."""
    attributes = {
        NSFontAttributeName: (
            NSFont.systemFontOfSize_(size) if weight is None else NSFont.systemFontOfSize_weight_(size, weight)
        )
    }
    if color is not None:
        attributes[NSForegroundColorAttributeName] = color
    if paragraph is not None:
        attributes[NSParagraphStyleAttributeName] = paragraph
    return NSAttributedString.alloc().initWithString_attributes_(text, attributes)


def _rich(title: str, detail: str, is_new: bool = False, chips=(), route: str = "", tag: str = ""):
    """Ligne complète : titre, ligne de métadonnées, puis trajet de branche.

    Trois niveaux de lecture, toujours dans le même ordre et les mêmes tailles, pour que
    l'œil trouve la même information à la même place sur toutes les lignes de toutes
    les sections.
    """
    paragraph = NSMutableParagraphStyle.alloc().init()
    paragraph.setLineSpacing_(1.0)
    text = NSMutableAttributedString.alloc().init()
    text.appendAttributedString_(
        _run(
            ("● " if is_new else "") + truncate(title, TITLE_WIDTH),
            TITLE_FONT,
            weight=NSFontWeightMedium if is_new else NSFontWeightRegular,
            paragraph=paragraph,
        )
    )
    if detail:
        if tag:
            text.appendAttributedString_(_run("\n", META_FONT, paragraph=paragraph))
            text.appendAttributedString_(_attachment(_red_label(tag, TAG_HEIGHT, TAG_RADIUS, TAG_PADDING), -2.0))
        text.appendAttributedString_(
            _run(
                ("  " if tag else "\n") + truncate(detail, DETAIL_WIDTH) + ("   " if chips else ""),
                META_FONT,
                color=NSColor.secondaryLabelColor(),
                paragraph=paragraph,
            )
        )
    if chips:
        text.appendAttributedString_(_chip_run(chips))
    if route:
        text.appendAttributedString_(
            _run(
                "\n" + truncate(route, ROUTE_WIDTH),
                ROUTE_FONT,
                color=NSColor.tertiaryLabelColor(),
                paragraph=paragraph,
            )
        )
    return text


def _header(text: str):
    """Titre de section : petit, gras, en capitales, pour se distinguer des lignes."""
    return _run(text, HEADER_FONT, color=NSColor.secondaryLabelColor(), weight=NSFontWeightSemibold)


def _note(text: str):
    """Ligne de service (état, avertissement, décompte) : même taille que les métadonnées."""
    return _run(text, META_FONT, color=NSColor.secondaryLabelColor())


class GitTodoApp(NSObject):
    def init(self):
        self = objc.super(GitTodoApp, self).init()
        if self is None:
            return None
        self.cfg = Config.load()
        self.cfg_mtime = self.config_mtime()
        self.state = State()
        self.state.scope = self.cfg.view_as or ""
        self.client = GitHub(self.cfg)
        self.avatars = Avatars()
        self.people: tuple[Person, ...] = ()
        self.snapshot = Snapshot(items=[])
        self.rows = {}
        self.fetching = False
        self.status_item = None
        self.menu = None
        self.timer = None
        self.spinner_timer = None
        self.spinner_frame = 0
        self.show_spinner = False
        self.menu_open = False
        self.footer_item = None
        self.shown = None
        self.help_window = None
        self.signature = ""
        self.last_full = None
        self.branches = []
        self.branches_at = None
        self.branches_busy = False
        # Dernier balayage connu par identité : rebasculer doit être immédiat, pas coûter
        # une minute d'attente le temps d'un nouveau balayage.
        self.branch_cache: dict[str, tuple[list, object]] = {}
        self.notifications: list = []
        self.notifications_at = None
        self.notifications_limit = ""
        # Sources actuellement dégradées : le triangle et sa section en vivent.
        self.health: dict[str, dict] = {}
        # Dernière gravité annoncée par GitHub, et quand on l'a demandée.
        self.service: tuple[str, str] = ("", "")
        self.service_at = None
        self.fetch_started = None
        self.shown_step = -1
        return self

    # --- cycle de vie ---------------------------------------------------

    def applicationDidFinishLaunching_(self, notification):
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self.status_item.button().setImagePosition_(NSImageLeft)
        self.menu = NSMenu.alloc().init()
        self.menu.setDelegate_(self)
        self.status_item.setMenu_(self.menu)
        self.render()
        self.timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            TICK_SECONDS, self, "tick:", None, True
        )
        # Modes communs : le décompte continue d'avancer quand le menu est ouvert.
        NSRunLoop.currentRunLoop().addTimer_forMode_(self.timer, NSRunLoopCommonModes)
        NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            self, "wake:", "NSWorkspaceDidWakeNotification", None
        )
        self.start_fetch()

    def tick_(self, timer):
        # Garde-fou : un cycle qui ne rend jamais la main bloquerait tous les suivants.
        if self.fetching and self.fetch_started and (now() - self.fetch_started).total_seconds() > STUCK_AFTER:
            log_error("cycle bloqué au-delà du délai : drapeau relâché de force")
            self.fetching = False
        self.maybe_refresh_branches()
        if self.menu_open:
            # Menu ouvert : on le reconstruit dès que son contenu change, pour ne pas
            # obliger à le refermer. Sans changement, seul le décompte bouge — reconstruire
            # à chaque seconde réinitialiserait la ligne survolée.
            if self.contents() != self.shown:
                self.build_menu(self.menu)
            else:
                self.update_footer()
        # L'anneau avance seul : on ne repeint que la barre, et seulement quand le pas change.
        if self.cfg.show_refresh_ring and self.ring_step() != self.shown_step:
            self.shown_step = self.ring_step()
            self.repaint_ring()
        if self.countdown() <= 0:
            self.start_fetch()

    @objc.python_method
    def note_incident(self, source: str, trouble, kind: str = "") -> None:
        """Retient qu'une source ne répond pas, en gardant la date du premier échec."""
        known = self.health.get(source)
        self.health[source] = {
            "status": getattr(trouble, "status", None),
            "kind": kind or getattr(trouble, "kind", "erreur"),
            "message": str(trouble),
            "since": known["since"] if known else now(),
        }

    @objc.python_method
    def clear_incident(self, source: str) -> None:
        self.health.pop(source, None)
        if not self.health:
            self.service, self.service_at = ("", ""), None

    @objc.python_method
    def ask_service(self) -> None:
        """Gravité officielle, demandée seulement quand quelque chose échoue déjà."""
        if not self.health:
            return
        age = None if self.service_at is None else (now() - self.service_at).total_seconds()
        if age is not None and age < SERVICE_REFRESH:
            return
        self.service, self.service_at = service_status(), now()

    @objc.python_method
    def level(self) -> str:
        """Niveau de gravité : nos propres échecs croisés avec ce qu'annonce GitHub.

        Un quota épuisé ou un refus de droits ne sont pas des pannes de GitHub, quelle que soit
        la source touchée : les données sont figées, mais rien n'est cassé en face. La panne de
        la source principale, elle, arrête tout, et l'annonce officielle a le dernier mot.
        """
        if not self.health:
            return ""
        official = OFFICIAL.get(self.service[0], "")
        if official == "critique":
            return "critique"
        broken = {name for name, t in self.health.items() if t["kind"] in ("panne", "réseau")}
        if "pull_requests" in broken:
            return "critique"
        if broken or official == "alerte":
            return "alerte"
        return "attention"

    @objc.python_method
    def read_notifications(self, wanted: bool) -> list:
        """Boîte des non-lues, relue sur sa propre cadence et réutilisée entre deux passages.

        Dix appels REST pour la parcourir en entier : à faire rarement, et à garder en mémoire
        le temps que l'auteur des mentions soit résolu une fois pour toutes.
        """
        if not wanted:
            self.notifications, self.notifications_at, self.notifications_limit = [], None, ""
            self.clear_incident("notifications")
            self.clear_incident("mentions")
            return []
        age = None if self.notifications_at is None else (now() - self.notifications_at).total_seconds()
        if age is not None and age < max(20, self.cfg.mention_refresh_seconds):
            return self.notifications
        try:
            found, limit = self.client.fetch_notifications()
        except GitHubError as exc:
            # Source auxiliaire : son échec ne doit pas emporter les données de PR du cycle.
            # On garde le dernier balayage et on réessaiera, sans avancer sa date.
            log_error(f"boîte des non-lues ignorée : {type(exc).__name__}: {exc}")
            self.note_incident("notifications", exc)
            return self.notifications
        self.clear_incident("notifications")
        # L'auteur du sujet mentionné, que l'API des notifications ne donne pas.
        if failed := self.client.resolve_mentions(found):
            self.note_incident("mentions", failed, kind="panne")
        else:
            self.clear_incident("mentions")
        self.notifications, self.notifications_at, self.notifications_limit = found, now(), limit
        return found

    @objc.python_method
    def interval(self) -> int:
        """Rythme demandé, espacé d'office si le quota GraphQL s'épuise.

        Quota déjà dépassé : le reste n'est plus lisible, puisque plus aucune requête ne
        passe. On s'espace sur le seul signal disponible, l'échec lui-même, plutôt que de
        frapper une API qui refuse toutes les trois secondes.
        """
        wanted = max(5, self.cfg.refresh_seconds)
        remaining = self.snapshot.rate_remaining
        if remaining is not None and remaining < QUOTA_FLOOR:
            return max(wanted, THROTTLED_SECONDS)
        if any(trouble["kind"] == "quota" for trouble in self.health.values()):
            return max(wanted, THROTTLED_SECONDS)
        return wanted

    @objc.python_method
    def frozen_for(self) -> float:
        """Âge de la dernière donnée réelle : au-delà de quelques cycles, c'est une panne."""
        if self.snapshot.fetched_at is None:
            return 0.0
        return (now() - self.snapshot.fetched_at).total_seconds()

    @objc.python_method
    def is_frozen(self) -> bool:
        return self.frozen_for() > max(4 * self.interval(), FROZEN_AFTER)

    @objc.python_method
    def progress(self) -> float:
        """Part du cycle déjà écoulée, entre 0 et 1."""
        if self.snapshot.fetched_at is None:
            return 0.0
        interval = max(1, self.interval())
        return max(0.0, min(1.0, (now() - self.snapshot.fetched_at).total_seconds() / interval))

    @objc.python_method
    def ring_step(self) -> int:
        """Pas d'avancement affiché : redessiner plus finement ne se verrait pas."""
        return int(self.progress() * RING_STEPS)

    @objc.python_method
    def countdown(self) -> int:
        if self.snapshot.fetched_at is None:
            return 0
        return round(self.interval() - (now() - self.snapshot.fetched_at).total_seconds())

    def wake_(self, notification):
        self.start_fetch()

    # --- données -------------------------------------------------------

    @objc.python_method
    def start_fetch(self, spinner: bool = False) -> None:
        if self.fetching:
            return
        self.fetching = True
        self.fetch_started = now()
        # Animation quand l'utilisateur attend un contenu : démarrage, bascule d'identité,
        # actualisation manuelle. Pas sur le rafraîchissement automatique, qui ne doit pas
        # faire clignoter la barre toutes les cinq minutes.
        if spinner or self.snapshot.fetched_at is None:
            self.start_spinner()
        self.render()
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    @objc.python_method
    def loading(self) -> bool:
        return self.fetching and self.show_spinner

    @objc.python_method
    def start_spinner(self) -> None:
        self.show_spinner = True
        self.spinner_frame = 0
        if self.spinner_timer is None:
            self.spinner_timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
                SPINNER_INTERVAL, self, "spin:", None, True
            )
            # Modes communs : l'animation continue même quand le menu est ouvert.
            NSRunLoop.currentRunLoop().addTimer_forMode_(self.spinner_timer, NSRunLoopCommonModes)

    @objc.python_method
    def stop_spinner(self) -> None:
        self.show_spinner = False
        if self.spinner_timer is not None:
            self.spinner_timer.invalidate()
            self.spinner_timer = None
        self.render()

    def spin_(self, timer):
        if not self.loading():
            self.stop_spinner()
            return
        self.spinner_frame = (self.spinner_frame + 1) % len(SPINNER_FRAMES)
        self.draw_spinner()

    @objc.python_method
    def draw_spinner(self) -> None:
        button = self.status_item.button()
        self.status_item.setLength_(NSVariableStatusItemLength)
        button.setImage_(None)
        button.setAttributedTitle_(
            NSAttributedString.alloc().initWithString_attributes_(
                SPINNER_FRAMES[self.spinner_frame],
                {NSFontAttributeName: NSFont.monospacedDigitSystemFontOfSize_weight_(13.0, NSFontWeightMedium)},
            )
        )
        who = f" en tant que @{self.snapshot.identity}" if self.snapshot.impersonating else ""
        button.setToolTip_(f"GitTodo — chargement{who}…")

    @objc.python_method
    def config_mtime(self) -> float:
        try:
            return CONFIG_PATH.stat().st_mtime
        except OSError:
            return 0.0

    @objc.python_method
    def reload_config(self) -> None:
        """Prend en compte une édition de config.json sans relancer l'app."""
        mtime = self.config_mtime()
        if mtime and mtime != self.cfg_mtime:
            self.cfg = Config.load()
            self.client.cfg = self.cfg
            self.state.scope = self.cfg.view_as or ""
            self.cfg_mtime = mtime

    @objc.python_method
    def _fetch_worker(self) -> None:
        """Enveloppe du cycle : le drapeau « en cours » doit retomber quoi qu'il arrive.

        Sans ce `finally`, une exception inattendue laisse l'app figée pour de bon : plus
        aucun cycle ne repart, et l'affichage reste crédible tout en étant périmé.
        """
        try:
            self._fetch_once()
        except Exception as exc:
            log_error(f"cycle interrompu : {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            AppHelper.callAfter(self.apply_snapshot, self._failed(f"{type(exc).__name__}: {exc}"))
        finally:
            AppHelper.callAfter(self.release_fetch)

    @objc.python_method
    def release_fetch(self) -> None:
        if self.fetching:
            self.fetching = False
            self.render()

    @objc.python_method
    def _fetch_once(self) -> None:
        self.reload_config()
        # Tout est revalidé au même rythme : une branche supprimée, ou qui vient de recevoir
        # une PR, disparaît au cycle suivant. Seule la découverte de branches *nouvelles*
        # garde sa cadence lente, parce qu'elle coûte vingt fois plus cher.
        self.validate_branches()
        # Sonde à 1 point : inutile de payer la requête complète si rien n'a bougé. Une
        # requête complète est tout de même forcée régulièrement, car certains changements
        # (résolution d'un fil, par exemple) ne modifient pas la date de la PR.
        fresh = self.snapshot.fetched_at is not None and not self.snapshot.error
        stale = max(self.cfg.refresh_seconds, self.cfg.full_refresh_seconds)
        overdue = self.last_full is None or (now() - self.last_full).total_seconds() >= stale
        if fresh and not overdue and self.signature:
            try:
                signature, rate = self.client.fetch_signature()
            except GitHubError as exc:
                AppHelper.callAfter(self.apply_snapshot, self._failed(str(exc)))
                return
            if signature == self.signature:
                AppHelper.callAfter(self.touch_snapshot, rate)
                return
        try:
            signature, _ = "", None
            prs, viewer, rate, truncated = self.client.fetch_pull_requests()
            signature = signature_of({pr.id: pr.updated_at for pr in prs})
            identity = self.cfg.view_as or viewer
            if not self.people:
                self.people = self.known_people(prs, viewer)
            # Les notifications sont celles du token : pas de mentions à la place d'un collègue.
            wants_mentions = self.cfg.include_mentions and identity == viewer
            notifications = self.read_notifications(wants_mentions)
            if self.notifications_limit:
                truncated = truncated + [self.notifications_limit]
            self.clear_incident("pull_requests")
            self.ask_service()
            snapshot = Snapshot(
                items=build_items(prs, notifications, identity, self.cfg, self.branches),
                viewer=viewer,
                identity=identity,
                fetched_at=now(),
                rate_remaining=rate,
                truncated=truncated,
                people=self.people,
            )
        except GitHubError as exc:
            snapshot = self._failed(str(exc), exc)
            self.ask_service()
        except Exception as exc:  # une exception ne doit jamais tuer la boucle d'événements
            snapshot = self._failed(f"{type(exc).__name__}: {exc}")
        if not snapshot.error:
            self.signature, self.last_full = signature, now()
        AppHelper.callAfter(self.apply_snapshot, snapshot)
        # Les photos arrivent après le badge : le menu est reconstruit à chaque ouverture.
        if not snapshot.error:
            faces = {item.avatar for item in snapshot.items} | {person.avatar for person in snapshot.people}
            self.avatars.prefetch({face for face in faces if face})

    @objc.python_method
    def validate_branches(self) -> None:
        if not self.branches:
            return
        try:
            kept = self.client.still_valid(list(self.branches))
        except (GitHubError, OSError) as exc:
            log_error(f"revalidation des branches ignorée : {type(exc).__name__}: {exc}")
            return
        if len(kept) != len(self.branches):
            AppHelper.callAfter(self.prune_branches, kept)

    @objc.python_method
    def prune_branches(self, kept: list) -> None:
        """Retire de l'affichage les branches disparues, sans relancer la découverte."""
        gone = {branch.key for branch in self.branches} - {branch.key for branch in kept}
        self.branches = kept
        identity = self.snapshot.identity or self.snapshot.viewer
        if identity in self.branch_cache:
            self.branch_cache[identity] = (kept, self.branch_cache[identity][1])
        if not gone:
            return
        self.snapshot.items = [
            item for item in self.snapshot.items if item.id.split(":", 1)[-1] not in gone
        ]
        self.render()

    @objc.python_method
    def maybe_refresh_branches(self) -> None:
        """Balayage des branches : son propre thread, sa propre cadence.

        Il dure une vingtaine de secondes ; le mêler au cycle des PR retarderait la liste
        principale, et une panne de son côté ne doit rien emporter.
        """
        if not self.cfg.show_branches or self.branches_busy or not self.snapshot.viewer:
            return
        interval = max(60, self.cfg.branch_refresh_seconds)
        due = self.branches_at is None or (now() - self.branches_at).total_seconds() >= interval
        if not due:
            return
        self.branches_busy = True
        identity = self.snapshot.identity or self.snapshot.viewer
        threading.Thread(target=self._branch_worker, args=(identity,), daemon=True).start()

    @objc.python_method
    def _branch_worker(self, identity: str) -> None:
        try:
            repos = set(self.client.contributed_repos(identity, self.snapshot.viewer))
            repos |= {item.repo for item in self.snapshot.items if "/" in item.repo}
            repos |= set(self.cfg.branch_repos)
            found = branches.orphans(self.client, sorted(repos), identity)
            AppHelper.callAfter(self.apply_branches, identity, found)
            AppHelper.callAfter(self.clear_incident, "branches")
        except Exception as exc:
            log_error(f"branches ignorées : {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            AppHelper.callAfter(self.note_incident, "branches", exc)
        finally:
            AppHelper.callAfter(self.release_branches)

    @objc.python_method
    def apply_branches(self, identity: str, found: list) -> None:
        # Le balayage dure une vingtaine de secondes : d'ici là, l'identité observée peut
        # avoir changé. Sans ce contrôle, les branches d'un collègue atterrissent chez moi.
        stamp = now()
        self.branch_cache[identity] = (found, stamp)
        if identity != (self.snapshot.identity or self.snapshot.viewer):
            log_error(f"branches de @{identity} mises de côté : identité changée entre-temps")
            return
        self.branches = found
        self.branches_at = stamp

    @objc.python_method
    def release_branches(self) -> None:
        self.branches_busy = False
        if self.branches_at is None:
            self.branches_at = now()  # évite de repartir en boucle après une panne

    @objc.python_method
    def known_people(self, prs, viewer: str) -> tuple[Person, ...]:
        """Membres de l'org ; à défaut, les personnes croisées dans les PR lues."""
        try:
            found = self.client.fetch_people()
            self.clear_incident("people")
        except GitHubError as exc:
            log_error(f"annuaire ignoré : {exc}")
            self.note_incident("people", exc)
            found = []
        if not found:
            seen = {pr.author: pr.avatar for pr in prs if pr.author != viewer}
            found = [Person(login, "", avatar) for login, avatar in sorted(seen.items())]
        return tuple(found)

    @objc.python_method
    def _failed(self, message: str, trouble=None) -> Snapshot:
        self.note_incident("pull_requests", trouble if trouble is not None else message)
        return Snapshot(
            items=self.snapshot.items,
            viewer=self.snapshot.viewer,
            identity=self.snapshot.identity,
            fetched_at=self.snapshot.fetched_at,
            rate_remaining=self.snapshot.rate_remaining,
            error=message,
            people=self.people,
        )

    @objc.python_method
    def touch_snapshot(self, rate: int | None) -> None:
        """Rien n'a changé : on remet le compteur à zéro sans toucher aux lignes."""
        self.fetching = False
        self.snapshot = replace(self.snapshot, fetched_at=now(), rate_remaining=rate, error=None)
        self.stop_spinner()

    @objc.python_method
    def apply_snapshot(self, snapshot: Snapshot) -> None:
        self.fetching = False
        expected = self.cfg.view_as or snapshot.viewer
        if snapshot.identity and snapshot.viewer and snapshot.identity != expected:
            # L'identité a changé pendant la requête : on recommence, animation maintenue.
            self.start_fetch(spinner=self.show_spinner)
            return
        self.snapshot = snapshot
        if not snapshot.error:
            self.state.prune(snapshot.items)
            self.state.save()
        self.stop_spinner()

    @objc.python_method
    def person_for(self, login: str) -> Person:
        for person in self.snapshot.people:
            if person.login == login:
                return person
        return Person(login)

    @objc.python_method
    def set_identity(self, login: str | None) -> None:
        self.cfg.view_as = login
        self.cfg.save()
        self.cfg_mtime = self.config_mtime()
        self.state.scope = login or ""
        self.signature = ""  # autre identité : la sonde ne peut pas servir de référence
        # Les branches appartiennent à une personne : garder celles de l'identité précédente
        # les afficherait sous le nom d'une autre. On reprend celles déjà connues pour cette
        # identité — la revalidation du prochain cycle corrigera ce qui a bougé — et on
        # relance de toute façon une découverte.
        known = self.branch_cache.get(login or self.snapshot.viewer)
        self.branches, self.branches_at = known if known else ([], None)
        self.snapshot = Snapshot(
            items=[],
            viewer=self.snapshot.viewer,
            identity=login or self.snapshot.viewer,
            people=self.snapshot.people,
        )
        self.render()
        self.start_fetch()
        self.maybe_refresh_branches()

    @objc.python_method
    def visible(self) -> list[Item]:
        return self.state.visible(self.snapshot.items)

    # --- barre des menus ------------------------------------------------

    @objc.python_method
    def render(self) -> None:
        if self.status_item is None:
            return
        if self.loading():
            self.draw_spinner()
            self.status_item.setVisible_(True)
            write_status({"chargement": True, "identite": self.snapshot.identity, **self.geometry()})
            return
        count, urgent = summarize(self.visible())
        badge = str(count) if count else ""
        shown = self.draw_badge(count, urgent, badge)
        who = f" (vu en tant que @{self.snapshot.identity})" if self.snapshot.impersonating else ""
        tip = f"GitTodo — {count} chose(s) à faire{who}" if count else f"GitTodo — rien à faire{who}"
        if self.snapshot.error:
            # L'erreur n'encombre pas la pastille : elle est dans le survol et dans le menu.
            tip = f"GitTodo — {truncate(self.snapshot.error, 80)}"
        self.status_item.button().setToolTip_(tip)
        visible = not (self.cfg.hide_when_zero and count == 0 and not self.snapshot.error)
        self.status_item.setVisible_(visible)
        write_status(
            {
                "format": self.cfg.badge_style,
                **shown,
                "visible": visible,
                "identite": self.snapshot.identity,
                "actions": count,
                "erreur": self.snapshot.error,
                "figé": self.is_frozen(),
                "branches": len(self.branches),
                "branches_maj": self.branches_at.isoformat() if self.branches_at else None,
                "maj": self.snapshot.fetched_at.isoformat() if self.snapshot.fetched_at else None,
                # Géométrie réelle : c'est le seul moyen de savoir si macOS a relégué
                # l'élément hors écran faute de place dans la barre.
                **self.geometry(),
            }
        )

    @objc.python_method
    def repaint_ring(self) -> None:
        """Repeint la seule image de la barre, pour faire avancer l'anneau.

        Passer par `render()` réécrirait le fichier d'état à chaque seconde, pour une image qui
        change d'un degré.
        """
        if self.status_item is None or self.loading() or self.health:
            return
        count, urgent = summarize(self.visible())
        self.draw_badge(count, urgent, str(count) if count else "")

    @objc.python_method
    def draw_badge(self, count: int, urgent: bool, badge: str) -> dict:
        """Peint l'élément de la barre et décrit ce qui a été affiché."""
        button = self.status_item.button()
        if self.cfg.badge_style == "avatar":
            photo = self.avatars.image(self.person_for(self.snapshot.identity).avatar, BAR_SIZE)
            if photo is not None:
                # L'anneau ne dit quelque chose que si le cycle tourne : pendant une lecture le
                # compteur animé prend le relais, et en panne le décompte n'a plus de sens.
                ring = self.cfg.show_refresh_ring and not self.health and not self.fetching
                base = _with_ring(photo, self.progress()) if ring else photo
                span = RING_SIZE if ring else BAR_SIZE
                portrait = (
                    _with_alert(base, span, self.level()) if self.health else _with_count(base, badge, span)
                )
                # Longueur variable : macOS ajoute 16 pt de marge à la longueur demandée,
                # donc imposer une largeur ne fait que l'élargir.
                self.status_item.setLength_(NSVariableStatusItemLength)
                button.setImage_(portrait)
                self.set_title("")
                if self.health:
                    return {
                        "rendu": "photo + avertissement",
                        "badge": "",
                        "niveau": self.level(),
                        "github": self.service[1],
                        "degrade": sorted(self.health),
                    }
                return {"rendu": "photo + pastille", "badge": badge}
        self.status_item.setLength_(NSVariableStatusItemLength)
        if self.health:
            # Sans photo, l'avertissement devient l'icône elle-même, et le nombre disparaît :
            # il n'est plus fiable tant qu'une source ne répond pas.
            self.status_item.button().setImage_(_alert_symbol(self.level(), GLYPH_BAR))
            self.set_title("")
            return {
                "rendu": "avertissement",
                "badge": "",
                "niveau": self.level(),
                "github": self.service[1],
                "degrade": sorted(self.health),
            }
        if self.snapshot.error or self.is_frozen():
            name = "exclamationmark.triangle"
        elif self.snapshot.impersonating:
            name = "person.crop.circle.fill"
        elif count == 0:
            name = "checkmark.circle"
        elif urgent:
            name = "exclamationmark.triangle.fill"
        else:
            name = "checklist"
        # Le nombre seul est la forme la plus étroite ; l'icône ne revient que s'il n'y a
        # pas de nombre à afficher, pour ne jamais laisser un élément vide (introuvable).
        with_icon = self.cfg.badge_style in ("icon", "icon_count") or not badge
        with_number = self.cfg.badge_style != "icon" and bool(badge)
        image = _symbol(name, GLYPH_BAR) if with_icon else None
        if image is None and not with_number:
            image, with_number = _symbol("checklist", GLYPH_BAR), True
        button.setImage_(image)
        text = ""
        if with_number:
            text = f" {badge}" if image is not None else badge
        self.set_title(text)
        return {"rendu": name if image is not None else "nombre seul", "badge": text.strip()}

    @objc.python_method
    def set_title(self, text: str) -> None:
        self.status_item.button().setAttributedTitle_(
            NSAttributedString.alloc().initWithString_attributes_(
                text,
                {NSFontAttributeName: NSFont.monospacedDigitSystemFontOfSize_weight_(BAR_FONT, NSFontWeightSemibold)},
            )
        )

    @objc.python_method
    def geometry(self) -> dict:
        button = self.status_item.button()
        window = button.window()
        screen = NSScreen.mainScreen().frame().size.width if NSScreen.mainScreen() else 0
        if window is None:
            return {"place_dans_la_barre": "aucune fenêtre", "largeur_ecran": screen}
        frame = window.frame()
        return {
            "x": round(frame.origin.x, 1),
            "largeur_element": round(frame.size.width, 1),
            "largeur_ecran": round(screen, 1),
            "sur_ecran": bool(window.screen() is not None) and frame.origin.x > 0,
        }

    # --- menu -----------------------------------------------------------

    def menuNeedsUpdate_(self, menu):
        self.build_menu(menu)

    def menuWillOpen_(self, menu):
        self.menu_open = True

    def menuDidClose_(self, menu):
        self.menu_open = False
        self.footer_item = None
        self.shown = None
        self.state.mark_seen(self.visible())
        self.render()

    @objc.python_method
    def contents(self) -> tuple:
        """Empreinte de ce que le menu doit montrer, hors éléments qui bougent tout seuls.

        Les durées « depuis X » en sont exclues : elles changent de minute en minute et
        provoqueraient une reconstruction, donc une perte du survol, pour rien.
        """
        return (
            self.snapshot.error,
            self.snapshot.identity,
            tuple(sorted((source, t["kind"], t.get("status")) for source, t in self.health.items())),
            tuple((item.id, item.fingerprint, item.weight, item.tag) for item in self.visible()),
        )

    @objc.python_method
    def build_menu(self, menu) -> None:
        self.shown = self.contents()
        # Plus aucune coche dans la colonne de gauche du menu principal : elle ne laisserait
        # qu'une gouttière vide devant les icônes. Le sous-menu des identités garde la sienne.
        menu.setShowsStateColumn_(False)
        menu.removeAllItems()
        self.rows = {}
        items = self.visible()
        if self.snapshot.impersonating:
            person = self.person_for(self.snapshot.identity)
            banner = self.add_action(menu, "", "viewAsSelf:")
            banner.setAttributedTitle_(
                _rich(f"Vu en tant que {person.label}", "cliquer pour revenir à ton profil", True)
            )
            banner.setImage_(_face(self.avatars, person.avatar, "person.crop.circle"))
            menu.addItem_(NSMenuItem.separatorItem())
        self.add_health(menu)
        # Bannière d'erreur seulement si la section des sources ne l'a pas déjà dit.
        if self.snapshot.error and not self.health:
            self.add_info(menu, "⚠︎ " + truncate(self.snapshot.error, 100))
        if not items and not self.snapshot.error:
            done = bool(self.snapshot.fetched_at)
            self.add_info(
                menu,
                "Rien à faire" if done else "Chargement…",
                "checkmark.circle" if done else "hourglass",
            )
        for kind in ORDER:
            self.add_group(menu, kind, [i for i in items if i.kind == kind])
        if menu.numberOfItems():
            menu.addItem_(NSMenuItem.separatorItem())
        self.add_footer(menu, summarize(items)[0])

    @objc.python_method
    def add_health(self, menu) -> None:
        """Sources dégradées : laquelle ne répond pas, depuis quand, et ce que ça fausse.

        En tête du menu, parce que tout ce qui suit doit être lu en sachant qu'il est partiel.
        """
        if not self.health:
            return
        level = self.level()
        title, _ = LEVELS[level]
        # Le triangle des lignes est centré dans la même boîte que les avatars ; celui du titre
        # de section reste à la taille du chrome, comme tous les autres titres.
        badge = _row_image(_alert_symbol(level, ALERT_SIZE))
        header = NSMenuItem.alloc().init()
        header.setAttributedTitle_(_header(f"{title} ({len(self.health)})"))
        header.setImage_(_chrome_symbol("exclamationmark.triangle.fill"))
        header.setEnabled_(False)
        menu.addItem_(header)
        # Ce que GitHub annonce de son côté : la seule façon de savoir si la panne est chez eux.
        if self.service[1]:
            official = self.row_item(
                _rich(f"GitHub annonce : {self.service[1]}", "état officiel de ses services"),
                "openStatus:",
                "service",
                _row_image(_boxed_symbol("bolt.horizontal.circle")),
            )
            menu.addItem_(official)
        for source, trouble in sorted(self.health.items()):
            label, effet = SOURCES.get(source, (source, ""))
            kind = trouble["kind"]
            code = f"HTTP {trouble['status']}" if trouble.get("status") else CODES.get(kind, kind)
            row = self.row_item(
                _rich(f"{label} : {code}", join(effet, since(trouble["since"]))),
                "openStatus:",
                source,
                badge,
            )
            row.setToolTip_(trouble["message"])
            menu.addItem_(row)
        menu.addItem_(NSMenuItem.separatorItem())

    @objc.python_method
    def add_group(self, menu, kind, items: list[Item]) -> None:
        if not items:
            return
        group = GROUPS[kind]
        if menu.numberOfItems():
            menu.addItem_(NSMenuItem.separatorItem())
        # Les sections actionnables affichent leur somme de notifications, pas leur nombre
        # de lignes : c'est ce total qui s'additionne jusqu'au badge de la barre.
        total = sum(item.weight for item in items) if group.is_action else len(items)
        header = NSMenuItem.alloc().init()
        header.setAttributedTitle_(_header(f"{group.label.upper()} ({total})"))
        header.setImage_(_chrome_symbol(group.symbol))
        header.setEnabled_(False)
        menu.addItem_(header)
        for item in items[:MAX_ROWS_PER_GROUP]:
            self.add_row(menu, item)
        if len(items) > MAX_ROWS_PER_GROUP:
            self.add_info(menu, f"{len(items) - MAX_ROWS_PER_GROUP} de plus, non affichés", "ellipsis")

    @objc.python_method
    def add_row(self, menu, item: Item) -> None:
        self.rows[item.id] = item
        count = str(item.weight) if item.weight else ""
        people = item.faces or (item.avatar,)
        face = _face(self.avatars, people, item.group.symbol, count)
        # Les variantes ⌥ et ⌘ ne portent pas le compte : ce n'est pas ce qu'elles font.
        plain = _face(self.avatars, people, item.group.symbol) if count else face
        row = self.row_item(
            _rich(item.title, item.detail, self.state.is_new(item), item.chips, item.route, item.tag),
            "openItem:",
            item.id,
            face,
        )
        row.setToolTip_(item.hint or item.title)
        menu.addItem_(row)
        menu.addItem_(
            self.row_item(
                _rich("Masquer : " + item.title, "jusqu'à la prochaine activité"),
                "dismissItem:",
                item.id,
                plain,
                NSEventModifierFlagOption,
            )
        )
        menu.addItem_(
            self.row_item(
                _rich("Copier le lien : " + item.title, item.url),
                "copyItem:",
                item.id,
                plain,
                NSEventModifierFlagCommand,
            )
        )

    @objc.python_method
    def row_item(self, title, selector: str, key: str, image=None, modifier: int | None = None):
        entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", selector, "")
        entry.setAttributedTitle_(title)
        entry.setTarget_(self)
        entry.setRepresentedObject_(key)
        if image is not None:
            entry.setImage_(image)
        if modifier is not None:
            entry.setKeyEquivalentModifierMask_(modifier)
            entry.setAlternate_(True)
        return entry

    @objc.python_method
    def add_info(self, menu, text: str, symbol: str = ""):
        info = NSMenuItem.alloc().init()
        info.setAttributedTitle_(_note(text))
        if symbol:
            info.setImage_(_chrome_symbol(symbol))
        info.setEnabled_(False)
        menu.addItem_(info)
        return info

    @objc.python_method
    def add_action(self, menu, label: str, selector: str, key: str = "", symbol: str = ""):
        entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, selector, key)
        entry.setTarget_(self)
        if symbol:
            entry.setImage_(_chrome_symbol(symbol))
        menu.addItem_(entry)
        return entry

    @objc.python_method
    def footer_text(self, action_count: int) -> str:
        if self.fetching:
            state = "actualisation…"
        elif self.snapshot.fetched_at is None:
            state = "en attente"
        else:
            left = self.countdown()
            state = f"prochaine dans {countdown(left)}" if left > 0 else "actualisation imminente"
        quota = f"quota {self.snapshot.rate_remaining}" if self.snapshot.rate_remaining is not None else ""
        slowed = "rythme réduit, quota bas" if self.interval() > max(5, self.cfg.refresh_seconds) else ""
        frozen = f"⚠︎ figé depuis {countdown(int(self.frozen_for()))}" if self.is_frozen() else ""
        return " · ".join(p for p in (state, f"{action_count} notification(s)", quota, slowed, frozen) if p)

    @objc.python_method
    def refresh_title(self, action_count: int):
        """« Actualiser », suivi de son état en gris sur la même ligne.

        Deux lignes pour une seule information n'avaient pas de sens : le bouton et ce qu'il va
        faire tiennent ensemble, l'état gardant sa taille et sa couleur discrètes.
        """
        title = NSMutableAttributedString.alloc().init()
        title.appendAttributedString_(_run("Actualiser", 13.0))
        title.appendAttributedString_(
            _run("   " + self.footer_text(action_count), META_FONT, color=NSColor.secondaryLabelColor())
        )
        return title

    @objc.python_method
    def update_footer(self) -> None:
        """Rafraîchit le décompte sans reconstruire le menu ouvert."""
        if self.footer_item is not None:
            self.footer_item.setAttributedTitle_(self.refresh_title(summarize(self.visible())[0]))

    @objc.python_method
    def add_footer(self, menu, action_count: int) -> None:
        for note in self.snapshot.truncated:
            self.add_info(menu, f"limite atteinte : {note}", "exclamationmark.triangle")
        if self.snapshot.impersonating:
            self.add_info(menu, "mentions indisponibles à la place d'un collègue", "exclamationmark.triangle")
        self.footer_item = self.add_action(menu, "", "refresh:", "r", "arrow.clockwise")
        self.footer_item.setAttributedTitle_(self.refresh_title(action_count))
        hidden = len(self.state.dismissed_here())
        if hidden:
            self.add_action(
                menu, f"Réafficher {hidden} élément(s) masqué(s)", "restore:", symbol="arrow.uturn.backward"
            )
        menu.addItem_(NSMenuItem.separatorItem())
        self.add_view_as(menu)
        if self.bundle_program():
            # Coche en fin de libellé : la colonne de gauche porte déjà l'icône de la ligne.
            started = launchagent.is_enabled()
            self.add_action(
                menu, "Lancer au démarrage" + ("  ✓" if started else ""), "toggleLogin:", symbol="power"
            )
        self.add_action(menu, "Réglages et mode d'emploi", "openHelp:", symbol="gearshape")
        self.add_action(menu, "Quitter GitTodo", "quitApp:", "q", "xmark.circle")

    @objc.python_method
    def add_view_as(self, menu) -> None:
        """Sous-menu de bascule d'identité : lecture seule, avec le token courant."""
        parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Voir en tant que", None, "")
        parent.setImage_(_chrome_symbol("eye"))
        submenu = NSMenu.alloc().init()
        mine = self.add_action(submenu, f"Moi (@{self.snapshot.viewer or '…'})", "viewAsSelf:")
        mine.setState_(0 if self.snapshot.impersonating else 1)
        mine.setImage_(_face(self.avatars, self.person_for(self.snapshot.viewer).avatar, "person.crop.circle"))
        others = [p for p in self.snapshot.people if p.login != self.snapshot.viewer]
        if others:
            submenu.addItem_(NSMenuItem.separatorItem())
        # Les gens classés par action la plus récente sur les dépôts ; les inactifs après
        # un séparateur, pour qu'ils ne noient pas ceux avec qui tu travailles en ce moment.
        quiet = True
        for person in others:
            if quiet and person.last_seen is None and any(p.last_seen for p in others):
                submenu.addItem_(NSMenuItem.separatorItem())
                quiet = False
            suffix = f"  —  {ago(person.last_seen)}" if person.last_seen else ""
            entry = self.add_action(submenu, person.label + suffix, "viewAs:")
            entry.setRepresentedObject_(person.login)
            entry.setState_(1 if person.login == self.snapshot.identity else 0)
            entry.setImage_(_face(self.avatars, person.avatar, "person.crop.circle"))
        parent.setSubmenu_(submenu)
        menu.addItem_(parent)

    @objc.python_method
    def bundle_program(self) -> str | None:
        """Chemin de l'exécutable seulement si on tourne bien depuis GitTodo.app."""
        bundle = NSBundle.mainBundle()
        if str(bundle.bundleIdentifier() or "") != BUNDLE_ID:
            return None
        return str(bundle.executablePath() or "") or None

    # --- actions -------------------------------------------------------

    @objc.python_method
    def row_of(self, sender) -> Item | None:
        return self.rows.get(str(sender.representedObject() or ""))

    def openItem_(self, sender):
        if item := self.row_of(sender):
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(item.url))
            self.state.mark_seen([item])
            self.render()

    def dismissItem_(self, sender):
        if item := self.row_of(sender):
            self.state.dismiss(item)
            self.render()

    def copyItem_(self, sender):
        if item := self.row_of(sender):
            board = NSPasteboard.generalPasteboard()
            board.clearContents()
            board.setString_forType_(item.url, NSPasteboardTypeString)

    def viewAs_(self, sender):
        login = str(sender.representedObject() or "")
        self.set_identity(login if login and login != self.snapshot.viewer else None)

    def viewAsSelf_(self, sender):
        self.set_identity(None)

    def refresh_(self, sender):
        self.notifications_at = None
        # Actualisation manuelle : on veut l'état réel, pas l'avis de la sonde — elle est
        # aveugle aux réactions et aux fils résolus. Les branches sont relancées aussi :
        # c'est juste après avoir supprimé ou poussé une branche qu'on appuie là.
        self.signature = ""
        self.branches_at = None
        self.maybe_refresh_branches()
        self.start_fetch(spinner=True)

    def restore_(self, sender):
        self.state.restore_all()
        self.render()

    def openStatus_(self, sender):
        """L'état des services GitHub : la seule page qui dit si la panne est chez eux."""
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_("https://www.githubstatus.com"))

    def toggleLogin_(self, sender):
        program = self.bundle_program()
        if not program:
            return
        if launchagent.is_enabled():
            launchagent.disable()
        else:
            launchagent.enable(program)

    def openHelp_(self, sender):
        """Réglages modifiables et mode d'emploi, avec les valeurs réelles de l'installation."""
        self.help_window = manual.panel(
            {
                "identity": self.snapshot.identity or self.snapshot.viewer,
                "viewer": self.snapshot.viewer,
                "scope": self.cfg.scope,
                "token": self.client.token_origin,
            }
        )
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.help_window.window.makeKeyAndOrderFront_(None)

    def quitApp_(self, sender):
        NSApplication.sharedApplication().terminate_(self)


def run() -> None:
    if not acquire_single_instance():
        print("GitTodo tourne déjà (une seule icône à la fois).", file=sys.stderr)
        return
    application = NSApplication.sharedApplication()
    delegate = GitTodoApp.alloc().init()
    application.setDelegate_(delegate)
    AppHelper.runEventLoop(installInterrupt=True)
