"""Formulaire des réglages : un contrôle par champ de `Config`, et un enregistrement.

Le type du contrôle est déduit de celui du champ dans la dataclasse, pour qu'ajouter une option
ne demande qu'une ligne ici. Les libellés et leur phrase d'explication vivent au même endroit
que le champ qu'ils décrivent, plutôt que dans un texte séparé qui se démoderait.
"""

from __future__ import annotations

from dataclasses import MISSING, fields, replace

import objc
from Cocoa import (
    NSAttributedString,
    NSBezelStyleAccessoryBarAction,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSFont,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSImage,
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

# Place réservée à droite de chaque contrôle pour le retour à la valeur par défaut.
RESET_SIZE = 22.0
LABEL_FONT = 12.0
HINT_FONT = 10.5
ROW_GAP = 14.0
TITLE_GAP = 22.0
FIELD_HEIGHT = 22.0
MARGIN = 20.0


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
    """Contrôles des réglages, plus l'état « modifié » qui commande le bouton."""

    def initWithConfig_origin_onDirty_(self, cfg, origin, on_dirty):
        self = objc.super(SettingsForm, self).init()
        if self is None:
            return None
        self.cfg = cfg
        self.origin = origin
        self.on_dirty = on_dirty
        self.widgets = {}
        self.resets = {}
        self.integers = set()
        self.secret = None
        self.view = None
        return self

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
                usable = inner - RESET_SIZE - 6.0
                if name in CHECKLIST:
                    view.addSubview_(_caption(label, "", NSMakeRect(MARGIN, y, inner, 15.0)))
                    y += 16.0
                    top = y
                    control = self._checklist(view, name, NSMakeRect(MARGIN, y, usable, FIELD_HEIGHT), value)
                    rows_count = -(-len(CHECKLIST[name]) // CHECKLIST_COLUMNS)
                    y += rows_count * FIELD_HEIGHT + 4.0
                elif isinstance(value, bool):
                    top = y
                    control = self._check(label, NSMakeRect(MARGIN, y, usable, FIELD_HEIGHT), value)
                    view.addSubview_(control)
                    y += FIELD_HEIGHT
                else:
                    view.addSubview_(_caption(label, _unit(name), NSMakeRect(MARGIN, y, inner, 15.0)))
                    y += 16.0
                    top = y
                    box = NSMakeRect(MARGIN, y, usable, FIELD_HEIGHT + 2)
                    control = (
                        self._choice(name, box, value)
                        if name in CHOICES
                        else self._text(name, box, value, example)
                    )
                    view.addSubview_(control)
                    y += FIELD_HEIGHT + 4.0
                self.widgets[name] = control
                reset = self._reset(name, NSMakeRect(MARGIN + inner - RESET_SIZE, top, RESET_SIZE, RESET_SIZE))
                view.addSubview_(reset)
                self.resets[name] = reset
                told = " · ".join(part for part in (hint, _separator(name, value)) if part)
                if told:
                    height = _hint_height(told, inner)
                    view.addSubview_(_hint(told, NSMakeRect(MARGIN, y, inner, height)))
                    y += height
                y += ROW_GAP
            y += TITLE_GAP - ROW_GAP
        view.addSubview_(_title(SECRET_TITLE, NSMakeRect(MARGIN, y, inner, 16.0)))
        y += 16.0 + 6.0
        view.addSubview_(_caption(SECRET_LABEL, "", NSMakeRect(MARGIN, y, inner, 15.0)))
        y += 16.0
        self.secret = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(MARGIN, y, inner, FIELD_HEIGHT + 2))
        self.secret.setPlaceholderString_(self.origin or "aucun token trouvé")
        self.secret.setFont_(NSFont.monospacedSystemFontOfSize_weight_(11.0, NSFontWeightRegular))
        self.secret.setDelegate_(self)
        self.secret.setIdentifier_(SECRET)
        self.secret.setAutoresizingMask_(NSViewWidthSizable)
        view.addSubview_(self.secret)
        y += FIELD_HEIGHT + 4.0
        height = _hint_height(SECRET_HINT, inner)
        view.addSubview_(_hint(SECRET_HINT, NSMakeRect(MARGIN, y, inner, height)))
        y += height + ROW_GAP
        view.setFrame_(NSMakeRect(0, 0, width, y + MARGIN))
        self.view = view
        self.refresh_resets()
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
        icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_("arrow.counterclockwise", None)
        if icon is not None:
            icon.setTemplate_(True)
            icon.setSize_(NSMakeSize(11.0, 11.0))
            button.setImage_(icon)
        button.setBezelStyle_(NSBezelStyleAccessoryBarAction)
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

    @objc.python_method
    def announce(self) -> None:
        """Un seul point de sortie après toute modification : bouton d'enregistrement et retours."""
        self.refresh_resets()
        self.on_dirty(self.changed())

    @objc.python_method
    def refresh_resets(self) -> None:
        current = self.collect()
        for name, button in self.resets.items():
            button.setHidden_(_same(getattr(current, name), _default(name)))

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
        """Y a-t-il quelque chose à enregistrer ? Un token collé en fait partie.

        Sans lui, coller un token ne faisait apparaître aucun bouton : rien ne disait qu'il
        restait un geste à faire, ni comment le faire.
        """
        current = self.collect()
        return bool(self.typed_key()) or any(
            not _same(getattr(current, name), getattr(self.cfg, name)) for name in self.authored()
        )

    @objc.python_method
    def commit(self) -> Config:
        """Écrit le fichier, puis prend la configuration enregistrée pour nouvelle référence.

        Les champs sans contrôle sont relus du fichier au moment d'écrire, jamais gardés de
        l'ouverture : ils se changent aussi depuis le menu, et une fenêtre restée ouverte les
        ferait revenir en arrière en enregistrant tout le reste.
        """
        described = self.collect()
        saved = replace(Config.load(), **{name: getattr(described, name) for name in self.authored()})
        saved.save()
        self.cfg = saved
        for name in self.widgets:
            self.apply(name, getattr(saved, name))
        self.refresh_resets()
        return saved


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


SAVE_WIDTH = 116.0


def save_button(target, action: str):
    """Bouton d'enregistrement, montré seulement quand il y a quelque chose à enregistrer.

    Avec son libellé : une icône seule laissait chercher où valider, et rien ne disait qu'il
    n'apparaît qu'en cas de modification.
    """
    button = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, SAVE_WIDTH, 24))
    icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_("square.and.arrow.down", None)
    if icon is not None:
        icon.setTemplate_(True)
        icon.setSize_(NSMakeSize(13.0, 13.0))
        button.setImage_(icon)
    button.setTitle_("Enregistrer")
    button.setFont_(NSFont.systemFontOfSize_weight_(11.5, NSFontWeightSemibold))
    button.setBezelStyle_(NSBezelStyleAccessoryBarAction)
    button.setTarget_(target)
    button.setAction_(action)
    button.setKeyEquivalent_("s")
    button.setToolTip_("Enregistrer les réglages (⌘S)")
    button.setHidden_(True)
    return button
