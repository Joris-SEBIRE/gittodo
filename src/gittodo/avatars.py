"""Photos de profil : téléchargement en tâche de fond, cache disque, rendu circulaire."""

from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from pathlib import Path

from Cocoa import (
    NSBezierPath,
    NSCompositingOperationSourceOver,
    NSImage,
    NSMakeRect,
    NSMakeSize,
    NSZeroRect,
)

CACHE_DIR = Path.home() / "Library" / "Caches" / "GitTodo" / "avatars"
MAX_AGE = 14 * 86400
SIZE = 22.0
# Barre des menus : 22 pt de haut, une icône de 18 pt y est centrée confortablement.
BAR_SIZE = 18.0


class Avatars:
    """Le téléchargement se fait depuis le thread de fetch, le rendu depuis le thread UI."""

    def __init__(self) -> None:
        self.rendered: dict[tuple[str, float], object] = {}

    def path_for(self, url: str) -> Path:
        return CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".img")

    def prefetch(self, urls: set[str]) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for url in urls:
            target = self.path_for(url)
            if target.exists() and (time.time() - target.stat().st_mtime) < MAX_AGE:
                continue
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "GitTodo"})
                with urllib.request.urlopen(request, timeout=10) as response:
                    data = response.read()
            except (urllib.error.URLError, OSError, ValueError):
                continue
            if not data:
                continue
            temporary = target.with_suffix(".part")
            temporary.write_bytes(data)
            temporary.replace(target)
            for key in [k for k in self.rendered if k[0] == url]:
                del self.rendered[key]

    def image(self, url: str, size: float = SIZE):
        """Image ronde, ou None si la photo n'est pas encore en cache."""
        if not url:
            return None
        if (url, size) in self.rendered:
            return self.rendered[(url, size)]
        source = NSImage.alloc().initWithContentsOfFile_(str(self.path_for(url)))
        if source is None:
            return None
        box = NSMakeRect(0, 0, size, size)
        circle = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
        circle.lockFocus()
        NSBezierPath.bezierPathWithOvalInRect_(box).addClip()
        source.drawInRect_fromRect_operation_fraction_(box, NSZeroRect, NSCompositingOperationSourceOver, 1.0)
        circle.unlockFocus()
        self.rendered[(url, size)] = circle
        return circle
