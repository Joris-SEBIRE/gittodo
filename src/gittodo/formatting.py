"""Formatage des libellés affichés dans le menu."""

from __future__ import annotations

import re
from datetime import datetime, timezone

QUOTE_LINE = re.compile(r"^\s*>.*$", re.MULTILINE)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
WHITESPACE = re.compile(r"\s+")


# Échelle décroissante ; les mois et les années sont approximés, comme partout ailleurs.
UNITS: tuple[tuple[str, int], ...] = (
    ("an", 365 * 86400),
    ("mois", 30 * 86400),
    ("sem", 7 * 86400),
    ("j", 86400),
    ("h", 3600),
    ("min", 60),
    ("s", 1),
)
JUST_NOW = 10


def _plural(label: str, count: int) -> str:
    return f"{count} {label}s" if label == "an" and count > 1 else f"{count} {label}"


def spell(seconds: int) -> str:
    """Durée dans les deux plus grandes unités, à condition qu'elles se suivent.

    « 3 h 25 min », « 2 mois 1 sem », « 1 an 2 mois ». Si l'unité juste en dessous de la
    plus grande est nulle, elle n'est pas affichée : 2 ans et 3 jours donne « 2 ans », parce
    que « 2 ans 3 j » sauterait les mois et les semaines et se lirait mal.
    """
    seconds = max(0, int(seconds))
    for index, (label, size) in enumerate(UNITS):
        if count := seconds // size:
            parts = [_plural(label, count)]
            if index + 1 < len(UNITS):
                below, unit = UNITS[index + 1]
                if extra := (seconds - count * size) // unit:
                    parts.append(_plural(below, extra))
            return " ".join(parts)
    return ""


def ago(moment: datetime, now: datetime | None = None) -> str:
    seconds = max(0, int(((now or datetime.now(timezone.utc)) - moment).total_seconds()))
    return "à l'instant" if seconds < JUST_NOW else f"il y a {spell(seconds)}"


def since(moment: datetime, now: datetime | None = None) -> str:
    """Durée écoulée, tournée « depuis » : c'est un délai d'attente, pas une date."""
    seconds = max(0, int(((now or datetime.now(timezone.utc)) - moment).total_seconds()))
    return "à l'instant" if seconds < JUST_NOW else f"depuis {spell(seconds)}"


def countdown(seconds: int) -> str:
    return spell(seconds) if seconds > 0 else "maintenant"


def short_repo(slug: str) -> str:
    """« mon-org/backend#5044 » → « backend#5044 »."""
    return slug.split("/", 1)[-1]


def excerpt(body: str, limit: int = 90) -> str:
    """Corps d'un commentaire réduit à une ligne, sans les citations Markdown."""
    text = QUOTE_LINE.sub("", HTML_COMMENT.sub("", body))
    text = WHITESPACE.sub(" ", text).strip(" -*#`\t")
    if not text:
        return ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def location(path: str, line: int | None) -> str:
    if not path:
        return "discussion"
    name = path.rsplit("/", 1)[-1]
    return f"{name}:{line}" if line else name


def join(*parts: str) -> str:
    return " · ".join(p for p in parts if p)
