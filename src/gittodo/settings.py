"""Formulaire des réglages : un contrôle par champ de `Config`, et un enregistrement.

Le type du contrôle est déduit de celui du champ dans la dataclasse, pour qu'ajouter une option
ne demande qu'une ligne ici. Les libellés et leur phrase d'explication vivent au même endroit
que le champ qu'ils décrivent, plutôt que dans un texte séparé qui se démoderait.
"""

from __future__ import annotations

from dataclasses import MISSING, fields, replace

import objc
from Cocoa import (
    NSAnimationContext,
    NSAttributedString,
    NSBezelStyleInline,
    NSBox,
    NSBoxCustom,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSEventModifierFlagCommand,
    NSFont,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSImage,
    NSImageView,
    NSImageSymbolConfiguration,
    NSLineBreakByWordWrapping,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSMakeSize,
    NSMutableAttributedString,
    NSNumberFormatter,
    NSObject,
    NSPopUpButton,
    NSSecureTextField,
    NSTextField,
    NSView,
    NSViewMinXMargin,
    NSViewWidthSizable,
    NSWindow,
)

from .config import Config

# Un titre par famille, dans l'ordre où on les lit : ce qui coûte du quota, puis ce qu'on
# regarde, puis ce qu'on affiche, puis la barre, puis l'accès. Chaque champ porte son libellé,
# sa phrase d'explication et un exemple montré en filigrane quand il est vide.
FORM: tuple[tuple[str, tuple[tuple[str, str, str, str], ...]], ...] = (
    (
        "Rythme",
        (
            ("refresh_seconds", "Cycle", "intervalle du cycle, sonde comprise", ""),
            ("full_refresh_seconds", "Requête complète", "délai maximum entre deux requêtes complètes", ""),
            ("max_per_search", "PR par recherche", "chaque PR lue coûte du quota", ""),
            ("branch_refresh_seconds", "Balayage des branches", "il dure une vingtaine de secondes", ""),
            ("mention_refresh_seconds", "Boîte des non-lues", "jusqu'à dix appels REST pour la parcourir", ""),
            ("closed_refresh_seconds", "Suivi des clôturées", "deux recherches, mesurées à 3 points", ""),
        ),
    ),
    (
        "Périmètre",
        (
            (
                "scope",
                "Qualificateurs de recherche",
                "ajoutés à chaque recherche GitHub ; vide pour tous les dépôts accessibles",
                "org:mon-organisation, repo:autre-org/depot",
            ),
            ("branch_repos", "Dépôts en plus", "balayés pour les branches, au format org/dépôt", "mon-org/mon-depot"),
            ("view_as", "Voir en tant que", "login GitHub observé ; vide pour toi", "prenom-nom"),
            ("direct_review_requests_only", "Reviews nominatives seules", "sans les demandes faites à une équipe", ""),
            ("include_mentions", "Lire les mentions", "interroge la boîte des non-lues", ""),
        ),
    ),
    (
        "Contenu du menu",
        (
            ("show_waiting", "Sections « pour information »", "", ""),
            ("show_branches", "Sections de branches", "", ""),
            ("drafts_are_actionable", "Drafts actionnables", "y remonter la CI rouge et l'absence de reviewer", ""),
            (
                "ignored_authors",
                "Auteurs ignorés",
                "logins de bots et de comptes dont les messages n'attendent jamais de réponse",
                "linear, dependabot",
            ),
            ("acknowledge_reactions", "Réactions qui valent réponse", "aucune cochée désactive la règle", ""),
            ("show_closed", "Suivi des PR clôturées", "messages restés sans réponse, et historique", ""),
            ("closed_days", "Fenêtre des clôturées", "profondeur du suivi des PR sorties du périmètre", ""),
            ("closed_history_rows", "Lignes d'historique", "nombre de clôtures affichées, les plus récentes", ""),
        ),
    ),
    (
        "Barre des menus",
        (
            ("badge_style", "Format de l'élément", "du plus large au plus étroit quand la barre est saturée", ""),
            ("hide_when_zero", "Masquer quand il n'y a rien à faire", "", ""),
            (
                "show_refresh_ring",
                "Anneau du prochain cycle",
                "il se remplit autour de la photo jusqu'à la prochaine lecture",
                "",
            ),
        ),
    ),
    (
        "Accès",
        (
            ("gh_path", "Chemin de gh", "chemin absolu, s'il n'est pas trouvé tout seul", "/opt/homebrew/bin/gh"),
            (
                "token_command",
                "Commande du token",
                "elle doit imprimer un token, et remplace gh",
                "gh auth token",
            ),
        ),
    ),
)

# Le token ne vit pas dans `Config` : il n'a rien à faire dans un fichier de réglages en clair.
# Le champ existe quand même ici, parce que c'est là qu'on le cherche, et ce qu'on y colle part
# dans le trousseau.
SECRET = "api_token"
SECRET_TITLE = "Token GitHub"
SECRET_LABEL = "Coller un token"
SECRET_HINT = (
    "il part dans le trousseau macOS, jamais dans le fichier de réglages · "
    "⌘V fonctionne ici · laisser vide ne touche pas au token en place"
)

CHOICES = {"badge_style": ("avatar", "count", "icon_count", "icon")}
# Les réactions de GitHub sont une liste fermée : autant la montrer entière et laisser cocher,
# plutôt que de faire saisir des noms au risque d'une faute de frappe silencieuse.
CHECKLIST = {
    "acknowledge_reactions": (
        ("THUMBS_UP", "👍"),
        ("THUMBS_DOWN", "👎"),
        ("LAUGH", "😄"),
        ("HOORAY", "🎉"),
        ("CONFUSED", "😕"),
        ("HEART", "❤️"),
        ("ROCKET", "🚀"),
        ("EYES", "👀"),
    )
}
CHECKLIST_COLUMNS = 2
# Unité déduite du suffixe du champ : elle est déjà dans son nom, la recopier à la main dans le
# libellé la laisserait se contredire le jour où l'une des deux change.
UNITS = (("_seconds", "s"), ("_days", "j"), ("_rows", "lignes"))
# Une commande shell se lit avec des espaces ; les autres listes sont des énumérations.
SPACED = {"token_command"}

# Deux emplacements réservés à droite de chaque ligne : enregistrer, puis revenir à la valeur
# d'origine. Le premier porte tour à tour le bouton et la marque de confirmation, qui ne
# coexistent jamais — une ligne est soit modifiée, soit enregistrée.
RESET_SIZE = 22.0
SAVE_SIZE = 22.0
SLOT_GAP = 4.0
# Taille des glyphes de bout de ligne : même gabarit pour les deux boutons, l'action une
# graisse au-dessus du retour en arrière ; la coche un cran plus grande, elle est seule.
SLOT_GLYPH = 12.0
MARK_GLYPH = 14.0
# Durées de la confirmation : la marque paraît vite, le trait vert s'efface lentement. C'est ce
# décalage qui se lit comme « c'est pris en compte » plutôt que comme un clignotement.
MARK_IN = 0.22
FLASH_OUT = 0.55
FLASH_HEIGHT = 2.0
# Longueur du rappel avant coupe : au-delà, il sort de sa ligne sans le dire.
RECORD_MAX = 64
LABEL_FONT = 12.0
HINT_FONT = 10.5
ROW_GAP = 14.0
TITLE_GAP = 22.0
FIELD_HEIGHT = 22.0
MARGIN = 20.0


# Raccourcis d'édition, que macOS ne route pas tout seul dans une app sans menu.
EDIT_KEYS = {"x": "cut_", "c": "copy_", "v": "paste_", "a": "selectAll_", "z": "undo_"}


class EditableWindow(NSWindow):
    """Fenêtre qui route elle-même les raccourcis d'édition.

    GitTodo est une app d'accessoire (`LSUIElement`) : elle n'a pas de barre de menus, donc pas
    de menu Édition, et macOS n'envoie ni ⌘V ni ⌘C ni ⌘A au champ qui a le focus. Sans ce
    routage, un token doit être retapé à la main.
    """

    def performKeyEquivalent_(self, event):
        # Envoyé directement au champ qui a le focus : passer par l'application supposerait une
        # fenêtre principale et un menu, que cette app n'a pas.
        if event.modifierFlags() & NSEventModifierFlagCommand:
            action = EDIT_KEYS.get((event.charactersIgnoringModifiers() or "").lower())
            handler = getattr(self.firstResponder(), action, None) if action else None
            if handler is not None:
                handler(None)
                return True
        return objc.super(EditableWindow, self).performKeyEquivalent_(event)


class Flipped(NSView):
    """Vue à l'origine en haut à gauche : la mise en page se lit dans le sens de lecture."""

    def isFlipped(self):
        return True


def _types() -> dict:
    return {f.name: f.type for f in fields(Config)}


def _unit(name: str) -> str:
    return next((unit for suffix, unit in UNITS if name.endswith(suffix)), "")


def _separator(name: str, value) -> str:
    """Comment séparer les éléments d'une liste, ajouté d'office à l'explication du champ.

    Déduit du type plutôt qu'écrit à la main : un champ de liste ne peut pas se retrouver sans
    son format, et le format affiché ne peut pas contredire celui que l'app lit.
    """
    if name in CHECKLIST or not (isinstance(value, list) or "list" in str(_types().get(name, ""))):
        return ""
    return "séparés par des espaces" if name in SPACED else "séparés par des virgules"


def _default(name: str):
    """Valeur d'origine du champ, lue sur la dataclasse : une seule définition pour les deux."""
    for declared in fields(Config):
        if declared.name == name:
            if declared.default_factory is not MISSING:
                return declared.default_factory()
            return None if declared.default is MISSING else declared.default
    return None


def _glyph(name: str, size: float, weight):
    """Symbole système à la taille et à la graisse voulues, prêt à être teinté par sa vue.

    La configuration efface la taille posée avant elle : on la règle donc d'abord, et la taille
    de mise en page ensuite. Rendu en gabarit, pour suivre le thème clair ou sombre.
    """
    icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if icon is None:
        return None
    icon = icon.imageWithSymbolConfiguration_(
        NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, weight)
    )
    icon.setTemplate_(True)
    icon.setSize_(NSMakeSize(size, size))
    return icon


def _numbers() -> NSNumberFormatter:
    """Formateur entier : une lettre tapée dans une durée n'y entre pas."""
    formatter = NSNumberFormatter.alloc().init()
    formatter.setAllowsFloats_(False)
    formatter.setMinimum_(1)
    formatter.setMaximum_(86400)
    return formatter


def _blank(value) -> bool:
    """Absence, sous ses trois écritures : `null`, liste vide, chaîne vide."""
    return value is None or value == [] or value == ""


def _same(left, right) -> bool:
    """Égalité telle qu'on la voit à l'écran : deux champs vides sont identiques.

    Sans cela, un champ `null` relu en liste vide se déclarerait modifié à l'ouverture, et le
    bouton d'enregistrement apparaîtrait sans que personne n'ait rien touché.
    """
    return (_blank(left) and _blank(right)) or left == right


def _to_text(name: str, value) -> str:
    if isinstance(value, list):
        return (" " if name in SPACED else ", ").join(str(v) for v in value)
    return "" if value is None else str(value)


def _from_text(name: str, text: str, current):
    """Retour à la valeur typée ; une saisie invalide laisse l'ancienne en place."""
    text = text.strip()
    if isinstance(current, list) or "list" in str(_types().get(name, "")):
        parts = text.split() if name in SPACED else [p.strip() for p in text.split(",")]
        kept = [p for p in parts if p]
        return kept if kept or not isinstance(current, list) else []
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return max(0, int(text))
        except ValueError:
            return current
    return text or None if current is None or isinstance(current, str) else text


class SettingsForm(NSObject):
    """Contrôles des réglages, chacun avec son état : enregistré, modifié, ou tout juste écrit.

    Une ligne se lit sans rien deviner. Tant qu'elle vaut ce qui est sur le disque, elle ne
    porte rien. Modifiée, elle montre son bouton d'enregistrement au bout du champ, et rappelle
    en dessous la valeur enregistrée : on voit d'un coup ce qu'on s'apprête à remplacer. Une
    fois écrite, un trait vert passe et une coche reste, jusqu'à la modification suivante.
    """

    def initWithConfig_origin_onMessage_(self, cfg, origin, on_message):
        self = objc.super(SettingsForm, self).init()
        if self is None:
            return None
        self.cfg = cfg
        self.origin = origin
        self.on_message = on_message
        # Posé par la fenêtre : le token n'est pas un réglage, il s'éprouve auprès de GitHub.
        self.apply_key = None
        self.widgets = {}
        self.resets = {}
        self.saves = {}
        self.marks = {}
        self.flashes = {}
        self.records = {}
        self.integers = set()
        self.written = set()
        self.secret = None
        self.view = None
        return self

    @objc.python_method
    def _slot(self, box, index: int):
        """Emplacement d'un contrôle de bout de ligne, compté depuis la droite."""
        width = SAVE_SIZE if index == 0 else RESET_SIZE
        offset = sum((SAVE_SIZE, RESET_SIZE)[step] + SLOT_GAP for step in range(index))
        # Centré sur la hauteur de la ligne : un champ de texte fait deux points de plus qu'un
        # bouton, et sans ce calage les glyphes flottent un point au-dessus de la valeur.
        middle = box.origin.y + (box.size.height - width) / 2
        return NSMakeRect(box.origin.x + box.size.width - offset - width, middle, width, width)

    @objc.python_method
    def _save(self, name: str, box):
        """Bouton d'enregistrement de la ligne, au bout du champ."""
        button = NSButton.alloc().initWithFrame_(box)
        icon = _glyph("arrow.down.to.line", SLOT_GLYPH, NSFontWeightSemibold)
        if icon is not None:
            button.setImage_(icon)
        button.setTitle_("")
        button.setBezelStyle_(NSBezelStyleInline)
        button.setIdentifier_(name)
        button.setTarget_(self)
        button.setAction_("saveOne:")
        button.setToolTip_("Enregistrer cette ligne")
        button.setAutoresizingMask_(NSViewMinXMargin)
        button.setHidden_(True)
        return button
    @objc.python_method
    def _mark(self, box):
        """Coche qui reste après un enregistrement, jusqu'à la modification suivante."""
        mark = NSImageView.alloc().initWithFrame_(box)
        icon = _glyph("checkmark.circle.fill", MARK_GLYPH, NSFontWeightRegular)
        if icon is not None:
            mark.setImage_(icon)
        mark.setContentTintColor_(NSColor.systemGreenColor())
        # Adossée à une couche : sans elle, l'animateur d'AppKit poserait l'opacité d'un coup
        # au lieu de la faire monter, et la confirmation n'aurait pas d'apparition.
        mark.setWantsLayer_(True)
        mark.setToolTip_("Enregistré")
        mark.setAutoresizingMask_(NSViewMinXMargin)
        mark.setHidden_(True)
        return mark
    @objc.python_method
    def _flash(self, box):
        """Trait vert sous le champ : l'animation qui dit que l'écriture a eu lieu."""
        strip = NSBox.alloc().initWithFrame_(box)
        strip.setBoxType_(NSBoxCustom)
        strip.setBorderWidth_(0.0)
        strip.setFillColor_(NSColor.systemGreenColor())
        strip.setCornerRadius_(FLASH_HEIGHT / 2)
        strip.setAlphaValue_(0.0)
        strip.setWantsLayer_(True)
        strip.setAutoresizingMask_(NSViewWidthSizable)
        return strip

    @objc.python_method
    def typed_key(self) -> str:
        """Le token qu'on vient de coller, s'il y en a un."""
        return str(self.secret.stringValue()).strip() if self.secret is not None else ""

    @objc.python_method
    def forget_key(self) -> None:
        """Vide le champ : le token ne traîne pas à l'écran une fois rangé."""
        if self.secret is not None:
            self.secret.setStringValue_("")

    @objc.python_method
    def build(self, width: float):
        view = Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, width, 10))
        view.setAutoresizingMask_(NSViewWidthSizable)
        inner = width - 2 * MARGIN
        y = MARGIN
        for title, rows in FORM:
            view.addSubview_(_title(title, NSMakeRect(MARGIN, y, inner, 16.0)))
            y += 16.0 + 6.0
            for name, label, hint, example in rows:
                value = getattr(self.cfg, name)
                usable = inner - SAVE_SIZE - RESET_SIZE - 2 * SLOT_GAP
                line = NSMakeRect(MARGIN, y, inner, FIELD_HEIGHT)
                if name in CHECKLIST:
                    view.addSubview_(_caption(label, "", NSMakeRect(MARGIN, y, inner, 15.0)))
                    y += 16.0
                    line = NSMakeRect(MARGIN, y, inner, FIELD_HEIGHT)
                    control = self._checklist(view, name, NSMakeRect(MARGIN, y, usable, FIELD_HEIGHT), value)
                    rows_count = -(-len(CHECKLIST[name]) // CHECKLIST_COLUMNS)
                    y += rows_count * FIELD_HEIGHT + 4.0
                elif isinstance(value, bool):
                    control = self._check(label, NSMakeRect(MARGIN, y, usable, FIELD_HEIGHT), value)
                    view.addSubview_(control)
                    y += FIELD_HEIGHT
                else:
                    view.addSubview_(_caption(label, _unit(name), NSMakeRect(MARGIN, y, inner, 15.0)))
                    y += 16.0
                    line = NSMakeRect(MARGIN, y, inner, FIELD_HEIGHT + 2)
                    box = NSMakeRect(MARGIN, y, usable, FIELD_HEIGHT + 2)
                    control = (
                        self._choice(name, box, value) if name in CHOICES else self._text(name, box, value, example)
                    )
                    view.addSubview_(control)
                    y += FIELD_HEIGHT + 4.0
                self.widgets[name] = control
                # Les trois contrôles de bout de ligne, dans l'ordre où on les lit de droite à
                # gauche : enregistrer ou coche, puis retour à la valeur d'origine.
                save = self._save(name, self._slot(line, 0))
                mark = self._mark(self._slot(line, 0))
                reset = self._reset(name, self._slot(line, 1))
                for control in (save, mark, reset):
                    view.addSubview_(control)
                self.saves[name], self.marks[name], self.resets[name] = save, mark, reset
                flash = self._flash(NSMakeRect(MARGIN, y - 2.0, usable, FLASH_HEIGHT))
                view.addSubview_(flash)
                self.flashes[name] = flash
                told = " · ".join(part for part in (hint, _separator(name, value)) if part)
                if told:
                    height = _hint_height(told, inner)
                    view.addSubview_(_hint(told, NSMakeRect(MARGIN, y, inner, height)))
                    y += height
                record = _record("", NSMakeRect(MARGIN, y, inner, 14.0))
                view.addSubview_(record)
                self.records[name] = record
                y += 14.0
                y += ROW_GAP
            y += TITLE_GAP - ROW_GAP
        view.addSubview_(_title(SECRET_TITLE, NSMakeRect(MARGIN, y, inner, 16.0)))
        y += 16.0 + 6.0
        view.addSubview_(_caption(SECRET_LABEL, "", NSMakeRect(MARGIN, y, inner, 15.0)))
        y += 16.0
        secret_line = NSMakeRect(MARGIN, y, inner, FIELD_HEIGHT + 2)
        self.secret = NSSecureTextField.alloc().initWithFrame_(
            NSMakeRect(MARGIN, y, inner - SAVE_SIZE - SLOT_GAP, FIELD_HEIGHT + 2)
        )
        self.secret.setPlaceholderString_(self.origin or "aucun token trouvé")
        self.secret.setFont_(NSFont.monospacedSystemFontOfSize_weight_(11.0, NSFontWeightRegular))
        self.secret.setDelegate_(self)
        self.secret.setIdentifier_(SECRET)
        self.secret.setAutoresizingMask_(NSViewWidthSizable)
        view.addSubview_(self.secret)
        self.saves[SECRET] = self._save(SECRET, self._slot(secret_line, 0))
        self.saves[SECRET].setToolTip_("Enregistrer et éprouver ce token")
        self.marks[SECRET] = self._mark(self._slot(secret_line, 0))
        for control in (self.saves[SECRET], self.marks[SECRET]):
            view.addSubview_(control)
        y += FIELD_HEIGHT + 4.0
        self.flashes[SECRET] = self._flash(NSMakeRect(MARGIN, y - 2.0, inner - SAVE_SIZE - SLOT_GAP, FLASH_HEIGHT))
        view.addSubview_(self.flashes[SECRET])
        height = _hint_height(SECRET_HINT, inner)
        view.addSubview_(_hint(SECRET_HINT, NSMakeRect(MARGIN, y, inner, height)))
        y += height + ROW_GAP
        view.setFrame_(NSMakeRect(0, 0, width, y + MARGIN))
        self.view = view
        self.refresh_rows()
        return view

    @objc.python_method
    def _text(self, name: str, box, value, example: str = ""):
        field = NSTextField.alloc().initWithFrame_(box)
        field.setStringValue_(_to_text(name, value))
        # Le filigrane ne se voit que sur un champ vide : c'est là qu'un exemple sert.
        field.setPlaceholderString_(example or _to_text(name, _default(name)))
        field.setFont_(NSFont.monospacedSystemFontOfSize_weight_(11.0, NSFontWeightRegular))
        field.setDelegate_(self)
        field.setIdentifier_(name)
        field.setAutoresizingMask_(NSViewWidthSizable)
        if isinstance(value, int) and not isinstance(value, bool):
            field.setFormatter_(_numbers())
            field.setAlignment_(1)  # NSTextAlignmentRight : un nombre se lit cadré à droite
            self.integers.add(name)
        return field

    @objc.python_method
    def _checklist(self, view, name: str, box, values):
        """Une case par réaction, sur deux colonnes, avec son émoji comme repère."""
        chosen = {str(v).upper() for v in (values or [])}
        entries = CHECKLIST[name]
        width = box.size.width / CHECKLIST_COLUMNS
        buttons = []
        for index, (code, glyph) in enumerate(entries):
            column, row = index % CHECKLIST_COLUMNS, index // CHECKLIST_COLUMNS
            cell = NSMakeRect(box.origin.x + column * width, box.origin.y + row * FIELD_HEIGHT, width, FIELD_HEIGHT)
            button = NSButton.alloc().initWithFrame_(cell)
            button.setButtonType_(NSButtonTypeSwitch)
            button.setTitle_(f"{glyph} {code.replace('_', ' ').lower()}")
            button.setFont_(NSFont.systemFontOfSize_weight_(11.0, NSFontWeightRegular))
            button.setState_(1 if code in chosen else 0)
            button.setTarget_(self)
            button.setAction_("touched:")
            view.addSubview_(button)
            buttons.append((code, button))
        return buttons

    @objc.python_method
    def _reset(self, name: str, box):
        """Retour à la valeur d'origine, montré seulement quand la valeur en diffère."""
        button = NSButton.alloc().initWithFrame_(box)
        icon = _glyph("arrow.uturn.backward", SLOT_GLYPH, NSFontWeightRegular)
        if icon is not None:
            button.setImage_(icon)
        button.setTitle_("")
        button.setBezelStyle_(NSBezelStyleInline)
        button.setIdentifier_(name)
        button.setTarget_(self)
        button.setAction_("reset:")
        button.setToolTip_(f"Revenir à la valeur d'origine : {_to_text(name, _default(name)) or 'vide'}")
        button.setAutoresizingMask_(NSViewMinXMargin)
        button.setHidden_(True)
        return button
    @objc.python_method
    def _choice(self, name: str, box, value):
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(box, False)
        popup.addItemsWithTitles_(list(CHOICES[name]))
        popup.selectItemWithTitle_(str(value))
        popup.setTarget_(self)
        popup.setAction_("touched:")
        return popup

    @objc.python_method
    def _check(self, label: str, box, value: bool):
        button = NSButton.alloc().initWithFrame_(box)
        button.setButtonType_(NSButtonTypeSwitch)
        button.setTitle_(label)
        button.setFont_(NSFont.systemFontOfSize_weight_(LABEL_FONT, NSFontWeightSemibold))
        button.setState_(1 if value else 0)
        button.setTarget_(self)
        button.setAction_("touched:")
        button.setAutoresizingMask_(NSViewWidthSizable)
        return button

    def touched_(self, sender):
        self.announce()

    def controlTextDidChange_(self, notification):
        field = notification.object()
        name = str(field.identifier() or "")
        if name in self.integers:
            # Le formateur ne refuse qu'à la validation : on filtre aussi à la frappe, pour
            # qu'une lettre n'apparaisse jamais dans une durée.
            digits = "".join(c for c in str(field.stringValue()) if c.isdigit())
            if digits != str(field.stringValue()):
                field.setStringValue_(digits)
        self.announce()

    def reset_(self, sender):
        name = str(sender.identifier() or "")
        self.apply(name, _default(name))
        self.announce()

    @objc.python_method
    def apply(self, name: str, value) -> None:
        widget = self.widgets[name]
        if name in CHECKLIST:
            wanted = {str(v).upper() for v in (value or [])}
            for code, button in widget:
                button.setState_(1 if code in wanted else 0)
        elif isinstance(getattr(self.cfg, name), bool):
            widget.setState_(1 if value else 0)
        elif name in CHOICES:
            widget.selectItemWithTitle_(str(value))
        else:
            widget.setStringValue_(_to_text(name, value))

    def saveOne_(self, sender):
        """Écrit la ligne, et la confirme sous les yeux.

        Ligne par ligne, et non tout d'un bloc : c'est le seul moyen de savoir ce qui vient
        d'être enregistré et ce qui reste en attente. Le fichier est relu avant d'écrire, pour
        ne pas ramener en arrière ce qui a changé ailleurs entre-temps.
        """
        name = str(sender.identifier() or "")
        if name == SECRET:
            self.save_key()
            return
        value = getattr(self.collect(), name)
        saved = replace(Config.load(), **{name: value})
        saved.save()
        self.cfg = saved
        self.written.add(name)
        self.confirm(name)
        self.refresh_rows()
        self.tell(f"{name} enregistré : {_pretty(name, value)}")

    @objc.python_method
    def save_key(self) -> None:
        """Range le token collé, l'éprouve, et dit le résultat sans quitter la ligne."""
        key = self.typed_key()
        if not key:
            return
        self.forget_key()
        ok, message = self.apply_key(key) if self.apply_key else (False, "token non testé : l'app ne tourne pas")
        if ok:
            self.written.add(SECRET)
            self.confirm(SECRET)
        self.refresh_rows()
        self.tell(message, not ok)

    @objc.python_method
    def confirm(self, name: str) -> None:
        """L'animation de validation : un trait vert qui s'efface, une coche qui paraît."""
        mark, flash = self.marks.get(name), self.flashes.get(name)
        if flash is not None:
            flash.setAlphaValue_(1.0)
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(FLASH_OUT)
            flash.animator().setAlphaValue_(0.0)
            NSAnimationContext.endGrouping()
        if mark is not None:
            mark.setHidden_(False)
            mark.setAlphaValue_(0.0)
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(MARK_IN)
            mark.animator().setAlphaValue_(1.0)
            NSAnimationContext.endGrouping()

    @objc.python_method
    def tell(self, message: str, trouble: bool = False) -> None:
        if self.on_message is not None:
            self.on_message(message, trouble)

    @objc.python_method
    def announce(self) -> None:
        """Un seul point de sortie après toute frappe : l'état de chaque ligne s'y recalcule."""
        self.refresh_rows()

    @objc.python_method
    def refresh_rows(self) -> None:
        """Remet chaque ligne dans son état : enregistrée, modifiée, ou tout juste écrite."""
        current = self.collect()
        for name in self.widgets:
            value = getattr(current, name)
            dirty = not _same(value, getattr(self.cfg, name))
            if dirty:
                # Une nouvelle modification annule la confirmation précédente : la coche ne
                # doit jamais parler d'un état qui n'est plus celui du disque.
                self.written.discard(name)
            self.saves[name].setHidden_(not dirty)
            self.marks[name].setHidden_(dirty or name not in self.written)
            self.resets[name].setHidden_(_same(value, _default(name)))
            told = f"enregistré : {_pretty(name, getattr(self.cfg, name))}" if dirty else ""
            self.records[name].setStringValue_(_short(told))
            self.records[name].setToolTip_(told or None)
        if self.secret is not None:
            typed = bool(self.typed_key())
            if typed:
                self.written.discard(SECRET)
            self.saves[SECRET].setHidden_(not typed)
            self.marks[SECRET].setHidden_(typed or SECRET not in self.written)

    @objc.python_method
    def reload(self) -> None:
        """Reprend ce qui est sur le disque : la fenêtre montre l'enregistré, pas un brouillon.

        Appelée à chaque ouverture. Une modification laissée en plan n'est pas conservée : elle
        n'a jamais été enregistrée, et la faire réapparaître laisserait croire le contraire.
        """
        self.cfg = Config.load()
        self.written.clear()
        self.forget_key()
        for name in self.widgets:
            self.apply(name, getattr(self.cfg, name))
        self.refresh_rows()

    @objc.python_method
    def collect(self) -> Config:
        """Configuration décrite par les contrôles, champs inconnus laissés tels quels."""
        values = {}
        for name, widget in self.widgets.items():
            current = getattr(self.cfg, name)
            if name in CHECKLIST:
                values[name] = [code for code, button in widget if button.state()]
            elif isinstance(current, bool):
                values[name] = bool(widget.state())
            elif name in CHOICES:
                values[name] = str(widget.titleOfSelectedItem() or current)
            else:
                values[name] = _from_text(name, str(widget.stringValue()), current)
        return replace(self.cfg, **values)

    @objc.python_method
    def authored(self) -> list:
        """Champs que ce formulaire sait exprimer : lui seul a le droit de les écrire."""
        return [*self.widgets]

    @objc.python_method
    def changed(self) -> bool:
        """Reste-t-il une ligne modifiée et non enregistrée ?"""
        current = self.collect()
        return bool(self.typed_key()) or any(
            not _same(getattr(current, name), getattr(self.cfg, name)) for name in self.authored()
        )


def _label(text: str, box, size: float, weight, colour, wraps: bool = False):
    field = NSTextField.alloc().initWithFrame_(box)
    field.setStringValue_(text)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    field.setTextColor_(colour)
    field.setAutoresizingMask_(NSViewWidthSizable)
    if wraps:
        field.cell().setWraps_(True)
        field.cell().setLineBreakMode_(NSLineBreakByWordWrapping)
    return field


def _title(text: str, box):
    return _label(text.upper(), box, 10.0, NSFontWeightSemibold, NSColor.secondaryLabelColor())


def _caption(text: str, unit: str, box):
    """Libellé du champ, suivi de son unité en gris : « Cycle (s) » se lit sans hésiter."""
    field = _label(text, box, LABEL_FONT, NSFontWeightSemibold, NSColor.labelColor())
    if unit:
        rich = NSMutableAttributedString.alloc().init()
        rich.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                text,
                {
                    NSFontAttributeName: NSFont.systemFontOfSize_weight_(LABEL_FONT, NSFontWeightSemibold),
                    NSForegroundColorAttributeName: NSColor.labelColor(),
                },
            )
        )
        rich.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                f" ({unit})",
                {
                    NSFontAttributeName: NSFont.systemFontOfSize_weight_(LABEL_FONT, NSFontWeightRegular),
                    NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
                },
            )
        )
        field.setAttributedStringValue_(rich)
    return field


def _pretty(name: str, value) -> str:
    """La valeur telle qu'on la dit : une case est cochée ou non, un champ vide se dit « vide »."""
    if isinstance(value, bool):
        return "coché" if value else "décoché"
    return _to_text(name, value) or "vide"


def _short(text: str) -> str:
    """Rappel coupé à la longueur de la ligne : le tout se lit dans l'infobulle."""
    return text if len(text) <= RECORD_MAX else text[: RECORD_MAX - 1].rstrip(" ,") + "…"


def _record(text: str, box):
    """Rappel de la valeur enregistrée, sous une ligne modifiée : ce qu'on s'apprête à remplacer."""
    field = _label(text, box, 10.0, NSFontWeightSemibold, NSColor.secondaryLabelColor())
    field.setFont_(NSFont.monospacedSystemFontOfSize_weight_(10.0, NSFontWeightRegular))
    return field


def _hint(text: str, box):
    return _label(text, box, HINT_FONT, NSFontWeightRegular, NSColor.secondaryLabelColor(), wraps=True)


def _hint_height(text: str, width: float) -> float:
    """Hauteur réelle de la phrase à cette largeur, retours à la ligne compris.

    La cellule doit être configurée en repli avant d'être mesurée, sinon elle répond la hauteur
    d'une seule ligne quelle que soit la largeur, et les phrases longues sont tronquées.
    """
    measured = NSTextField.labelWithString_(text)
    measured.setFont_(NSFont.systemFontOfSize_weight_(HINT_FONT, NSFontWeightRegular))
    measured.cell().setWraps_(True)
    measured.cell().setLineBreakMode_(NSLineBreakByWordWrapping)
    span = measured.cell().cellSizeForBounds_(NSMakeRect(0, 0, width, 200.0))
    return max(14.0, span.height + 2.0)
