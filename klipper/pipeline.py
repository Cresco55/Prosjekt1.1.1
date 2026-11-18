"""Fra ett opptak til ferdige klipp i godkjenningskøen."""
from __future__ import annotations

import logging
from pathlib import Path

from . import db, detect, render
from .config import Config, Streamer

log = logging.getLogger(__name__)


def behandle(cfg: Config, con, streamer: Streamer, video: Path, *,
             chatfil: Path | None = None, start_tid: float | None = None) -> list[int]:
    """Finn høydepunkter i ``video``, lag klipp og legg dem i køen. Gir id-ene til klippene."""
    if not streamer.har_tillatelse:
        raise PermissionError(f"{streamer.navn} mangler dokumentert tillatelse. Klipper ikke.")
    video = Path(video)
    varighet = detect.varighet_av(video)
    if varighet < 10:
        log.info("%s er for kort til å klippe (%.0fs).", video.name, varighet)
        return []

    lyd = detect.lyd_signal(video)
    chat = None
    if chatfil and start_tid is not None:
        meldinger = [m for m in detect.les_chat(chatfil)
                     if start_tid - 60 <= m["t"] <= start_tid + varighet + 60]
        if meldinger:
            chat = detect.chat_signal(meldinger, start_tid, varighet)

    d = cfg.deteksjon
    hoydepunkter = detect.finn_hoydepunkter(
        chat, lyd, varighet, terskel=d.terskel, vekt_chat=d.vekt_chat, vekt_lyd=d.vekt_lyd,
        sekunder_for=d.sekunder_for, sekunder_etter=d.sekunder_etter,
        min_avstand=d.min_avstand, maks_per_time=d.maks_per_time)
    log.info("%s: fant %d øyeblikk i %s.", streamer.navn, len(hoydepunkter), video.name)

    ider = []
    utmappe = cfg.datamappe / "klipp" / streamer.navn
    for h in hoydepunkter:
        ordliste = []
        if cfg.video.teksting:
            try:
                ordliste = render.transkriber(video, h.start, h.lengde,
                                              cfg.video.whisper_modell, cfg.video.sprak)
            except Exception:
                log.exception("Teksting feilet, lager klippet uten.")
        utfil = utmappe / f"{video.stem}_{int(h.start):05d}.mp4"
        try:
            render.lag_klipp(video, h.start, h.slutt, utfil, kreditt=streamer.kredit_tekst,
                             layout=cfg.video.layout, ordliste=ordliste)
        except RuntimeError as e:
            log.error("%s", e)
            continue
        verdier = {"streamer": streamer.navn, "kreditering": streamer.kredit_tekst}
        ider.append(db.legg_til(
            con, streamer=streamer.navn, kilde=video.name, start=h.start, slutt=h.slutt,
            poeng=h.poeng, grunner=h.grunner, fil=utfil,
            tittel=cfg.publisering.tittel_mal.format(**verdier),
            beskrivelse=cfg.publisering.beskrivelse_mal.format(**verdier),
            transkripsjon=" ".join(o["ord"].strip() for o in ordliste)))
    return ider
