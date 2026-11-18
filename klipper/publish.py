"""Last opp godkjente klipp til TikTok, Instagram og YouTube.

Opplastingen går gjennom Upload-Post (upload-post.com), samme tjeneste som
OpenShorts bruker: én API-nøkkel, og kontoene dine kobles til én gang i
Upload-Post sitt kontrollpanel. Feltnavnene følger OpenShorts sin
``social_platforms.py`` (MIT).

Krever miljøvariablene UPLOAD_POST_API_KEY og UPLOAD_POST_USER.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import requests

from . import db

log = logging.getLogger(__name__)

API = "https://api.upload-post.com/api/upload"
YOUTUBE_TITTEL_MAKS = 100


class PubliseringsFeil(RuntimeError):
    pass


def kort_tittel(tittel: str, maks: int = YOUTUBE_TITTEL_MAKS) -> str:
    t = " ".join((tittel or "").split())
    if len(t) <= maks:
        return t
    kutt = t[:maks]
    mellomrom = kutt.rfind(" ")
    return (kutt[:mellomrom] if mellomrom >= maks // 2 else kutt).rstrip(" ,.;:-")


def skjemafelt(plattformer: list[str], tittel: str, beskrivelse: str, bruker: str) -> list[tuple]:
    beskrivelse = beskrivelse or tittel
    felt = [("user", bruker), ("title", tittel), ("async_upload", "true")]
    felt += [("platform[]", p) for p in plattformer]
    if "tiktok" in plattformer:
        felt += [("tiktok_title", beskrivelse), ("post_mode", "DIRECT_POST")]
    if "instagram" in plattformer:
        felt += [("instagram_title", beskrivelse), ("media_type", "REELS")]
    if "youtube" in plattformer:
        felt += [("youtube_title", kort_tittel(tittel)), ("youtube_description", beskrivelse),
                 ("privacyStatus", "public")]
    return felt


def er_satt_opp() -> bool:
    return bool(os.environ.get("UPLOAD_POST_API_KEY") and os.environ.get("UPLOAD_POST_USER"))


def last_opp(fil: Path, tittel: str, beskrivelse: str, plattformer: list[str]) -> dict:
    if not er_satt_opp():
        raise PubliseringsFeil(
            "UPLOAD_POST_API_KEY og UPLOAD_POST_USER er ikke satt. Se README for oppsett.")
    fil = Path(fil)
    if not fil.exists():
        raise PubliseringsFeil(f"Fant ikke videofilen {fil}")
    with fil.open("rb") as f:
        svar = requests.post(
            API,
            headers={"Authorization": f"Apikey {os.environ['UPLOAD_POST_API_KEY']}"},
            data=skjemafelt(plattformer, tittel, beskrivelse, os.environ["UPLOAD_POST_USER"]),
            files={"video": (fil.name, f, "video/mp4")},
            timeout=(10, 300),
        )
    if svar.status_code not in (200, 201, 202):
        raise PubliseringsFeil(f"Upload-Post svarte {svar.status_code}: {svar.text[:300]}")
    try:
        return svar.json()
    except ValueError:
        return {"raw": svar.text[:300]}


def publiser_klipp(con, klipp_id: int, plattformer: list[str]) -> bool:
    """Publiser ett klipp. Bare klipp som står som «godkjent» blir sendt."""
    if not db.bytt_status(con, klipp_id, ("godkjent",), "publiserer"):
        return False
    klipp = db.hent(con, klipp_id)
    try:
        svar = last_opp(klipp["fil"], klipp["tittel"], klipp["beskrivelse"], plattformer)
    except (PubliseringsFeil, requests.RequestException) as e:
        log.error("Publisering av klipp %s feilet: %s", klipp_id, e)
        db.bytt_status(con, klipp_id, ("publiserer",), "feilet", feil=str(e))
        return False
    db.bytt_status(con, klipp_id, ("publiserer",), "publisert",
                   svar=json.dumps(svar, ensure_ascii=False)[:2000])
    log.info("Klipp %s er sendt til %s.", klipp_id, ", ".join(plattformer))
    return True
