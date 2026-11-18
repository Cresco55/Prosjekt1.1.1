"""Finn de beste øyeblikkene i et opptak.

To signaler slås sammen, sekund for sekund:

* chat: hvor mange meldinger som kommer, med ekstra vekt på typiske
  reaksjoner (LUL, KEKW, POG, «clip it» ...). Chatten reagerer noen sekunder
  etter det som skjedde, så signalet flyttes litt bakover.
* lyd: hvor høyt det er (skrik, latter, eksplosjoner).

Hvert signal måles mot sin egen normal i opptaket (robust z-skår), så en
kanal med mye chat og en med lite chat behandles likt. Ideen er hentet fra
nirvagold/stream-clipper; koden her er skrevet på nytt.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

CHAT_FORSINKELSE = 6  # sekunder fra noe skjer til chatten reagerer

_HYPE = re.compile(
    r"\b(lulw?|kekw|omegalul|pog(champ|gers|u)?|lma+o+|lmfao|(ha){2,}h?|"
    r"wtf|omg|clip(ped)?|no way|holy|insane|w+|gg|rip|monkas)\b|[😂🤣💀]",
    re.IGNORECASE,
)


@dataclass
class Hoydepunkt:
    start: float
    slutt: float
    topp: float
    poeng: float
    grunner: list[str] = field(default_factory=list)

    @property
    def lengde(self) -> float:
        return self.slutt - self.start


# --------------------------------------------------------------------------- #
# Signaler
# --------------------------------------------------------------------------- #
def les_chat(sti: Path) -> list[dict]:
    """Les chatloggen (én JSON per linje med ``t`` = epoch-sekunder og ``msg``)."""
    ut = []
    sti = Path(sti)
    if not sti.exists():
        return ut
    with sti.open(encoding="utf-8", errors="replace") as f:
        for linje in f:
            try:
                rad = json.loads(linje)
                ut.append({"t": float(rad["t"]), "msg": str(rad.get("msg", ""))})
            except (ValueError, KeyError, TypeError):
                continue
    return ut


def chat_signal(meldinger: list[dict], start_tid: float, varighet: float) -> np.ndarray:
    """Chataktivitet per sekund av opptaket. ``start_tid`` er epoch for opptakets start."""
    n = max(1, int(np.ceil(varighet)))
    signal = np.zeros(n)
    for m in meldinger:
        i = int(m["t"] - start_tid - CHAT_FORSINKELSE)
        if 0 <= i < n:
            signal[i] += 2.0 if _HYPE.search(m["msg"]) else 1.0
    return signal


def lyd_signal(video: Path, rate: int = 8000) -> np.ndarray:
    """Lydnivå (RMS) per sekund, lest ut med ffmpeg."""
    kommando = ["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1",
                "-ar", str(rate), "-f", "s16le", "-"]
    data = subprocess.run(kommando, capture_output=True, check=False).stdout
    prover = np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16).astype(np.float32)
    sekunder = len(prover) // rate
    if sekunder == 0:
        return np.zeros(0)
    blokker = prover[: sekunder * rate].reshape(sekunder, rate)
    return np.sqrt((blokker ** 2).mean(axis=1))


def glatt(signal: np.ndarray, vindu: int) -> np.ndarray:
    if len(signal) == 0 or vindu <= 1:
        return signal.astype(float)
    kjerne = np.ones(vindu) / vindu
    return np.convolve(signal, kjerne, mode="same")


def z_skar(signal: np.ndarray) -> np.ndarray:
    """Hvor langt over normalen hvert sekund er. Robust mot at toppene selv drar opp snittet."""
    if len(signal) == 0:
        return signal.astype(float)
    median = float(np.median(signal))
    spredning = float(np.median(np.abs(signal - median))) * 1.4826
    if spredning < 1e-9:
        spredning = float(signal.std())
    if spredning < 1e-9:
        return np.zeros(len(signal))
    return (signal - median) / spredning


# --------------------------------------------------------------------------- #
# Valg av øyeblikk
# --------------------------------------------------------------------------- #
def finn_hoydepunkter(
    chat: np.ndarray | None,
    lyd: np.ndarray | None,
    varighet: float,
    *,
    terskel: float = 4.0,
    vekt_chat: float = 1.0,
    vekt_lyd: float = 0.7,
    sekunder_for: float = 22.0,
    sekunder_etter: float = 10.0,
    min_avstand: float = 45.0,
    maks_per_time: int = 6,
) -> list[Hoydepunkt]:
    n = max(1, int(np.ceil(varighet)))

    def tilpass(s):
        if s is None or len(s) == 0 or not np.any(s):
            return None
        ut = np.zeros(n)
        ut[: min(n, len(s))] = s[:n]
        return ut

    chat, lyd = tilpass(chat), tilpass(lyd)
    if chat is None and lyd is None:
        return []

    z_chat = np.clip(z_skar(glatt(chat, 8)), 0, 12) if chat is not None else np.zeros(n)
    z_lyd = np.clip(z_skar(glatt(lyd, 4)), 0, 12) if lyd is not None else np.zeros(n)
    # Med bare ett signal får det full vekt, ellers ville terskelen blitt urimelig høy.
    if chat is None:
        vekt_chat, vekt_lyd = 0.0, 1.0
    elif lyd is None:
        vekt_chat, vekt_lyd = 1.0, 0.0
    samlet = (vekt_chat * z_chat + vekt_lyd * z_lyd) / max(vekt_chat, vekt_lyd, 1e-9)

    maks = max(1, int(round(maks_per_time * max(varighet, 600) / 3600)))
    valgt: list[Hoydepunkt] = []
    for i in np.argsort(samlet)[::-1]:
        poeng = float(samlet[i])
        if poeng < terskel or len(valgt) >= maks:
            break
        if any(abs(i - h.topp) < min_avstand for h in valgt):
            continue
        start = max(0.0, float(i) - sekunder_for)
        slutt = min(float(varighet), float(i) + sekunder_etter)
        if slutt - start < 8:
            continue
        grunner = []
        if z_chat[i] >= 1.5:
            grunner.append(f"chatten tok av ({z_chat[i]:.1f}x over normalen)")
        if z_lyd[i] >= 1.5:
            grunner.append(f"høy lyd ({z_lyd[i]:.1f}x over normalen)")
        valgt.append(Hoydepunkt(start, slutt, float(i), round(poeng, 2), grunner))
    return sorted(valgt, key=lambda h: h.start)


def varighet_av(video: Path) -> float:
    ut = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True, check=False).stdout.strip()
    try:
        return float(ut)
    except ValueError:
        return 0.0
