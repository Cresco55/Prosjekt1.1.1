"""Køen av klipp, lagret i SQLite.

Et klipp går gjennom: venter -> godkjent -> publiserer -> publisert (eller
feilet), eller venter -> avvist. Ingenting publiseres uten å ha vært godkjent.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SKJEMA = """
CREATE TABLE IF NOT EXISTS klipp (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    streamer TEXT NOT NULL,
    kilde TEXT NOT NULL,
    start REAL NOT NULL,
    slutt REAL NOT NULL,
    poeng REAL NOT NULL,
    grunner TEXT NOT NULL DEFAULT '[]',
    fil TEXT NOT NULL,
    tittel TEXT NOT NULL DEFAULT '',
    beskrivelse TEXT NOT NULL DEFAULT '',
    transkripsjon TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'venter',
    feil TEXT NOT NULL DEFAULT '',
    svar TEXT NOT NULL DEFAULT '',
    opprettet REAL NOT NULL,
    godkjent REAL,
    publisert REAL
);
CREATE INDEX IF NOT EXISTS klipp_status ON klipp(status);
"""

STATUSER = ("venter", "godkjent", "publiserer", "publisert", "feilet", "avvist")


def koble(sti: Path) -> sqlite3.Connection:
    sti = Path(sti)
    sti.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(sti, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SKJEMA)
    return con


def legg_til(con, *, streamer, kilde, start, slutt, poeng, grunner, fil,
             tittel="", beskrivelse="", transkripsjon="") -> int:
    with con:
        cur = con.execute(
            "INSERT INTO klipp (streamer, kilde, start, slutt, poeng, grunner, fil, tittel,"
            " beskrivelse, transkripsjon, opprettet) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (streamer, str(kilde), start, slutt, poeng, json.dumps(grunner, ensure_ascii=False),
             str(fil), tittel, beskrivelse, transkripsjon, time.time()),
        )
    return cur.lastrowid


def hent(con, klipp_id: int):
    return con.execute("SELECT * FROM klipp WHERE id = ?", (klipp_id,)).fetchone()


def liste(con, status: str | None = None, grense: int = 200):
    if status:
        return con.execute(
            "SELECT * FROM klipp WHERE status = ? ORDER BY poeng DESC, id DESC LIMIT ?",
            (status, grense)).fetchall()
    return con.execute("SELECT * FROM klipp ORDER BY id DESC LIMIT ?", (grense,)).fetchall()


def antall(con) -> dict:
    rader = con.execute("SELECT status, COUNT(*) AS n FROM klipp GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rader}


def oppdater_tekst(con, klipp_id: int, tittel: str, beskrivelse: str) -> None:
    with con:
        con.execute("UPDATE klipp SET tittel = ?, beskrivelse = ? WHERE id = ?",
                    (tittel, beskrivelse, klipp_id))


def bytt_status(con, klipp_id: int, fra: tuple[str, ...], til: str, *, feil="", svar="") -> bool:
    """Flytt et klipp til ``til`` bare hvis det står i en av ``fra``. Sann hvis det ble flyttet."""
    assert til in STATUSER
    ekstra = ""
    if til == "godkjent":
        ekstra = ", godkjent = %f" % time.time()
    elif til == "publisert":
        ekstra = ", publisert = %f" % time.time()
    merker = ",".join("?" * len(fra))
    with con:
        cur = con.execute(
            f"UPDATE klipp SET status = ?, feil = ?, svar = ?{ekstra} "
            f"WHERE id = ? AND status IN ({merker})",
            (til, feil, svar, klipp_id, *fra),
        )
    return cur.rowcount == 1
