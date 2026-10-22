"""Le son d'un nouvel élément dans les compteurs.

Un son se décide sur une transition, d'une lecture à la suivante, et jamais sur un état : la
première lecture ne sonne pas, ni celle qui suit un passage à une autre identité. Un élément se
reconnaît à son sujet, la PR et non sa ligne : une PR qui change de section ou de couleur —
mergée, fil résolu — reste le même élément, et ne sonne pas. Elle ne sonne que si elle compte
davantage qu'avant, en rouge ou en tout, avec une ligne jamais vue : une ligne qui s'efface le
temps d'un recalcul chez GitHub puis revient à l'identique se tait.

Un message, lui, se reconnaît à son adresse : chaque nouveau message qui m'attend sonne, même sur
une PR déjà comptée, et même quand il prend la place d'une autre raison de compter (un conflit,
une review demandée) sans changer le nombre.
"""

from __future__ import annotations

from .models import Item


class Bell:
    def __init__(self) -> None:
        # Par sujet, ce qu'il comptait à la lecture précédente : en rouge, puis en tout.
        self.counts: dict[str, tuple[int, int]] = {}
        # Par sujet, les empreintes de ligne déjà vues compter : un retour à l'identique se tait.
        self.seen: dict[str, set[str]] = {}
        # Messages déjà entendus, par leur adresse.
        self.heard: set[str] = set()
        self.identity: str | None = None

    def feed(self, items: list[Item], identity: str) -> bool:
        """Cette lecture fait-elle entrer un nouvel élément dans un compteur ?"""
        counts: dict[str, tuple[int, int]] = {}
        prints: dict[str, set[str]] = {}
        for item in items:
            if not item.weight:
                continue
            subject = item.url.split("#")[0]
            red, total = counts.get(subject, (0, 0))
            counts[subject] = (red + (0 if item.closed else item.weight), total + item.weight)
            prints.setdefault(subject, set()).add(item.fingerprint)
        heard = {url for item in items if item.weight for url in item.messages}
        ring = identity == self.identity and (
            bool(heard - self.heard)
            or any(
                (red > self.counts.get(subject, (0, 0))[0] or total > self.counts.get(subject, (0, 0))[1])
                and bool(prints[subject] - self.seen.get(subject, set()))
                for subject, (red, total) in counts.items()
            )
        )
        if identity != self.identity:
            self.seen, self.heard, self.identity = {}, set(), identity
        self.heard |= heard
        for subject, fingerprints in prints.items():
            self.seen.setdefault(subject, set()).update(fingerprints)
        self.counts = counts
        return ring
