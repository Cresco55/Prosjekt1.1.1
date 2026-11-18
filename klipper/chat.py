"""Logg Twitch-chatten mens strømmen går.

Leser chatten anonymt (uten konto) over Twitch sin IRC-tjeneste og skriver én
JSON-linje per melding: ``{"t": <epoch>, "user": "...", "msg": "..."}``.
Chatten brukes bare til å måle når den tar av; den publiseres aldri.
"""
from __future__ import annotations

import json
import logging
import random
import socket
import ssl
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

VERT, PORT = "irc.chat.twitch.tv", 6697


def tolk_linje(linje: str) -> dict | None:
    """``:bruker!bruker@... PRIVMSG #kanal :melding`` -> {"user", "msg"}; ellers None."""
    if linje.startswith("@"):  # hopp over tagger
        linje = linje.split(" ", 1)[1] if " " in linje else ""
    if not linje.startswith(":"):
        return None
    deler = linje.split(" ", 3)
    if len(deler) < 4 or deler[1] != "PRIVMSG":
        return None
    bruker = deler[0][1:].split("!", 1)[0]
    melding = deler[3][1:] if deler[3].startswith(":") else deler[3]
    return {"user": bruker, "msg": melding}


class ChatLogger(threading.Thread):
    def __init__(self, kanal: str, utfil: Path):
        super().__init__(daemon=True, name=f"chat-{kanal}")
        self.kanal = kanal.lower()
        self.utfil = Path(utfil)
        self.stopp = threading.Event()

    def run(self) -> None:
        self.utfil.parent.mkdir(parents=True, exist_ok=True)
        pause = 5
        while not self.stopp.is_set():
            try:
                self._lytt()
                pause = 5
            except OSError as e:
                log.warning("Chat for %s falt ut (%s). Prøver igjen om %ss.", self.kanal, e, pause)
            self.stopp.wait(pause)
            pause = min(pause * 2, 300)

    def _lytt(self) -> None:
        raa = socket.create_connection((VERT, PORT), timeout=30)
        with ssl.create_default_context().wrap_socket(raa, server_hostname=VERT) as s:
            s.settimeout(360)  # Twitch pinger omtrent hvert femte minutt
            nick = f"justinfan{random.randint(10000, 99999)}"
            s.sendall(f"NICK {nick}\r\nJOIN #{self.kanal}\r\n".encode())
            buffer = b""
            with self.utfil.open("a", encoding="utf-8") as ut:
                while not self.stopp.is_set():
                    data = s.recv(4096)
                    if not data:
                        raise OSError("forbindelsen ble lukket")
                    buffer += data
                    *linjer, buffer = buffer.split(b"\r\n")
                    for raa_linje in linjer:
                        linje = raa_linje.decode("utf-8", errors="replace")
                        if linje.startswith("PING"):
                            s.sendall(b"PONG :tmi.twitch.tv\r\n")
                            continue
                        m = tolk_linje(linje)
                        if m:
                            m["t"] = round(time.time(), 2)
                            ut.write(json.dumps(m, ensure_ascii=False) + "\n")
                    ut.flush()
