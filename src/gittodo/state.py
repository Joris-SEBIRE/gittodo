"""État local : ce qui a été vu, ce qui a été masqué. Rien n'est écrit sur GitHub.

Les clés sont préfixées par l'identité observée, pour que le mode « voir en tant que »
ne touche pas aux éléments masqués de son propre profil (préfixe vide = soi-même).
"""

from __future__ import annotations

import fcntl
import json
import time

from .config import STATE_PATH
from .models import Item

LOCK_PATH = STATE_PATH.parent / "gittodo.lock"
STATUS_PATH = STATE_PATH.parent / "status.json"
SEPARATOR = "|"
_lock_handle = None


ERRORS_PATH = STATE_PATH.parent / "errors.log"


# Au-delà de cette taille le journal est basculé : une panne qui se répète toutes les vingt
# secondes le fait grossir sans fin, et un journal de plusieurs mégaoctets ne se lit plus.
ERRORS_MAX_BYTES = 1_000_000


def log_error(message: str) -> None:
    """Trace des pannes silencieuses : sans ça, un worker mort est indétectable."""
    try:
        ERRORS_PATH.parent.mkdir(parents=True, exist_ok=True)
        if ERRORS_PATH.exists() and ERRORS_PATH.stat().st_size > ERRORS_MAX_BYTES:
            ERRORS_PATH.replace(ERRORS_PATH.with_suffix(".log.1"))
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with ERRORS_PATH.open("a") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


def write_status(status: dict) -> None:
    """Reflète ce qu'affiche la barre des menus, pour pouvoir diagnostiquer sans la voir."""
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps(status, indent=1, ensure_ascii=False) + "\n")
    except OSError:
        pass


def acquire_single_instance(attempts: int = 4, delay: float = 0.5) -> bool:
    """Empêche deux barres de menus concurrentes (le verrou tombe à la mort du process).

    Plusieurs essais : au redémarrage, l'instance précédente peut n'être pas encore morte.
    """
    global _lock_handle
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_PATH, "w")
    for remaining in range(attempts, 0, -1):
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if remaining > 1:
                time.sleep(delay)
            continue
        _lock_handle = handle
        return True
    handle.close()
    return False


class State:
    def __init__(self) -> None:
        raw: dict = {}
        if STATE_PATH.exists():
            try:
                raw = json.loads(STATE_PATH.read_text() or "{}")
            except (json.JSONDecodeError, OSError):
                raw = {}
        # {clé: empreinte} — l'entrée expire dès que l'élément bouge sur GitHub.
        self.dismissed: dict[str, str] = dict(raw.get("dismissed") or {})
        self.seen: dict[str, str] = dict(raw.get("seen") or {})
        # Lignes du suivi des PR clôturées déjà ouvertes : elles cessent alors de compter dans
        # la pastille violette, tout en restant dans la liste pour y revenir.
        self.opened: dict[str, str] = dict(raw.get("opened") or {})
        self.scope = ""

    def save(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(
                {"dismissed": self.dismissed, "seen": self.seen, "opened": self.opened}, indent=1
            )
        )

    def key(self, item: Item) -> str:
        return f"{self.scope}{SEPARATOR}{item.id}" if self.scope else item.id

    def in_scope(self, key: str) -> bool:
        if self.scope:
            return key.startswith(f"{self.scope}{SEPARATOR}")
        return SEPARATOR not in key

    def is_dismissed(self, item: Item) -> bool:
        return self.dismissed.get(self.key(item)) == item.fingerprint

    def is_new(self, item: Item) -> bool:
        return self.seen.get(self.key(item)) != item.fingerprint

    def dismiss(self, item: Item) -> None:
        self.dismissed[self.key(item)] = item.fingerprint
        self.seen[self.key(item)] = item.fingerprint
        self.save()

    def is_opened(self, item: Item) -> bool:
        return self.opened.get(self.key(item)) == item.fingerprint

    def mark_opened(self, items: list[Item]) -> None:
        for item in items:
            self.opened[self.key(item)] = item.fingerprint
        self.save()

    def knows_closed(self) -> bool:
        """A-t-on déjà vu passer du suivi de clôtures pour cette identité ?

        Sert à ne pas servir un mois d'historique comme autant de nouveautés au premier
        lancement : ce qui précède la première observation est marqué ouvert d'office.
        """
        return any(self.in_scope(key) for key in self.opened)

    def dismissed_here(self) -> list[str]:
        return [key for key in self.dismissed if self.in_scope(key)]

    def restore_all(self) -> None:
        for key in self.dismissed_here():
            del self.dismissed[key]
        self.save()

    def mark_seen(self, items: list[Item]) -> None:
        for item in items:
            self.seen[self.key(item)] = item.fingerprint
        self.save()

    def prune(self, items: list[Item]) -> None:
        """Oublie les éléments disparus, sans toucher aux autres identités."""
        alive = {self.key(item) for item in items}

        def keep(store: dict[str, str]) -> dict[str, str]:
            return {k: v for k, v in store.items() if not self.in_scope(k) or k in alive}

        self.dismissed = keep(self.dismissed)
        self.seen = keep(self.seen)
        self.opened = keep(self.opened)

    def visible(self, items: list[Item]) -> list[Item]:
        return [item for item in items if not self.is_dismissed(item)]
