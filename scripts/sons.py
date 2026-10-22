"""Fabrique src/gittodo/nouveau.wav : une note douce de vibraphone, jouée quand un compteur gagne un élément.

La note additionne trois partiels, chacun avec sa propre extinction, sous un léger trémolo, puis
passe dans un peu d'espace (trois échos amortis). Rejouer ce script après avoir touché au timbre.
"""

import math
import struct
import wave
from pathlib import Path

RATE = 44100
# Partiels du vibraphone : rapport à la fondamentale, amplitude, constante d'extinction (s).
VIBRA = ((1.0, 1.0, 1.1), (4.0, 0.22, 0.3), (10.0, 0.05, 0.08))
FREQ = 880.0  # la5
TREMOLO = 0.35  # profondeur, à 5,5 Hz
SPACE = 0.15
PEAK = 0.42  # crête du fichier : « léger », bien en dessous du plein volume

buf = [0.0] * (4 * RATE)  # quatre secondes : la note s'éteint bien avant
for k in range(len(buf)):
    t = k / RATE
    env = min(1.0, t / 0.002) * (1 - TREMOLO * (0.5 - 0.5 * math.cos(2 * math.pi * 5.5 * t)))
    buf[k] = env * sum(a * math.exp(-t / tau) * math.sin(2 * math.pi * FREQ * r * t) for r, a, tau in VIBRA)
buf += [0.0] * int(0.4 * RATE)
for delay, fade in ((0.031, 0.5), (0.047, 0.4), (0.071, 0.3)):
    d = int(delay * RATE)
    for i in range(d, len(buf)):
        buf[i] += SPACE * fade * buf[i - d]
top = max(abs(s) for s in buf)
while abs(buf[-1]) < 2e-3 * top:
    buf.pop()
fade = int(0.02 * RATE)
for k in range(fade):
    buf[-1 - k] *= k / fade
with wave.open(str(Path(__file__).parent.parent / "src/gittodo/nouveau.wav"), "wb") as out:
    out.setnchannels(1)
    out.setsampwidth(2)
    out.setframerate(RATE)
    out.writeframes(b"".join(struct.pack("<h", int(32767 * PEAK * s / top)) for s in buf))
