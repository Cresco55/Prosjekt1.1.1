"""Ta opp live-strømmer, i biter, mens de går.

Én tråd per streamer med tillatelse. Tråden prøver å åpne strømmen med
streamlink; er kanalen offline, venter den og prøver igjen. Når strømmen går,
skrives den til biter på noen minutter (ffmpeg, uten omkoding), og hver
ferdige bit sendes videre til klipping mens strømmen fortsatt er live.

Oppsettet følger samme idé som 0jc1/py-autovod (streamlink i en løkke).
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
from calendar import timegm
from pathlib import Path

from .chat import ChatLogger
from .config import Config, Streamer

log = logging.getLogger(__name__)

FILNAVN = "%Y%m%dT%H%M%SZ"


def start_tid(segment: Path) -> float:
    """Epoch-tiden et segment startet, lest fra filnavnet (UTC)."""
    return float(timegm(time.strptime(Path(segment).stem, FILNAVN)))


def ferdige_segmenter(mappe: Path, opptak_pagar: bool) -> list[Path]:
    """Segmenter som er skrevet ferdig og ikke behandlet. Det nyeste skrives fortsatt til mens opptaket går."""
    alle = sorted(p for p in Path(mappe).glob("*.ts"))
    if opptak_pagar and alle:
        alle = alle[:-1]
    return [p for p in alle if not p.with_suffix(".ferdig").exists()]


def streamlink_kommando(streamer: Streamer) -> list[str]:
    # Kjøres som modul, så den blir funnet også når det virtuelle miljøet ikke er aktivert.
    kommando = [sys.executable, "-m", "streamlink", "--stdout", "--loglevel", "warning"]
    if streamer.plattform == "twitch":
        kommando.append("--twitch-disable-ads")
    return kommando + [streamer.url, "best"]


class Opptaker(threading.Thread):
    def __init__(self, cfg: Config, streamer: Streamer, ko: "queue.Queue", ventetid: int = 60):
        super().__init__(daemon=True, name=f"opptak-{streamer.navn}")
        if not streamer.har_tillatelse:
            raise PermissionError(f"{streamer.navn} mangler dokumentert tillatelse.")
        self.cfg, self.streamer, self.ko, self.ventetid = cfg, streamer, ko, ventetid
        self.mappe = cfg.datamappe / "opptak" / streamer.navn
        self.chatfil = self.mappe / "chat.jsonl"
        self.stopp = threading.Event()
        self._meldt: set[Path] = set()

    def run(self) -> None:
        self.mappe.mkdir(parents=True, exist_ok=True)
        if self.streamer.plattform == "twitch":
            ChatLogger(self.streamer.navn, self.chatfil).start()
        self._meld(opptak_pagar=False)  # biter som ble liggende fra forrige kjøring
        while not self.stopp.is_set():
            try:
                self._ta_opp()
            except Exception:
                log.exception("Opptak av %s feilet", self.streamer.navn)
            self._meld(opptak_pagar=False)
            self.stopp.wait(self.ventetid)

    def _meld(self, opptak_pagar: bool) -> None:
        for segment in ferdige_segmenter(self.mappe, opptak_pagar):
            if segment not in self._meldt:
                self._meldt.add(segment)
                self.ko.put((self.streamer, segment, self.chatfil))

    def _ta_opp(self) -> None:
        miljo = dict(os.environ, TZ="UTC")
        kilde = subprocess.Popen(streamlink_kommando(self.streamer),
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        skriver = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-i", "pipe:0", "-c", "copy", "-f", "segment",
             "-segment_time", str(self.cfg.video.segment_sekunder), "-reset_timestamps", "1",
             "-strftime", "1", str(self.mappe / f"{FILNAVN}.ts")],
            stdin=kilde.stdout, stderr=subprocess.DEVNULL, env=miljo)
        kilde.stdout.close()
        startet = time.time()
        live_meldt = False
        try:
            while skriver.poll() is None and not self.stopp.is_set():
                if not live_meldt and any(self.mappe.glob("*.ts")) and time.time() - startet > 15:
                    log.info("%s er live. Tar opp.", self.streamer.navn)
                    live_meldt = True
                self._meld(opptak_pagar=True)
                time.sleep(5)
        finally:
            for p in (kilde, skriver):
                if p.poll() is None:
                    p.terminate()
            for p in (kilde, skriver):
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    p.kill()
        if live_meldt:
            log.info("Strømmen til %s er slutt.", self.streamer.navn)
