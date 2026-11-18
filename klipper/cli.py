"""Kommandolinjen: ``klipper sjekk | start | behandle | ko``."""
from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import queue
import shutil
import sys
import threading
from pathlib import Path

from . import capture, db, pipeline, publish
from .config import Config, ConfigError, last

log = logging.getLogger("klipper")


def les_env(sti: Path) -> None:
    """Les NAVN=verdi-linjer fra .env uten å overskrive det som alt er satt."""
    if not sti.exists():
        return
    for linje in sti.read_text(encoding="utf-8").splitlines():
        linje = linje.strip()
        if linje and not linje.startswith("#") and "=" in linje:
            navn, verdi = linje.split("=", 1)
            os.environ.setdefault(navn.strip(), verdi.strip().strip("'\""))


def sjekk(cfg: Config) -> int:
    ok = True
    funn = {v: bool(shutil.which(v)) for v in ("ffmpeg", "ffprobe")}
    funn["streamlink"] = importlib.util.find_spec("streamlink") is not None
    for verktoy, funnet in funn.items():
        print(f"{'OK    ' if funnet else 'MANGLER'} {verktoy}")
        ok = ok and funnet
    try:
        import faster_whisper  # noqa: F401
        print("OK     teksting (faster-whisper)")
    except ImportError:
        print("INFO   faster-whisper er ikke installert. Klipp lages uten teksting.")
    print(f"{'OK    ' if publish.er_satt_opp() else 'MANGLER'} opplasting (Upload-Post-nøkler i .env)")
    print()
    for s in cfg.tillatte():
        print(f"Klippes:       {s.navn} ({s.plattform}), tillatelse: {s.tillatelse_bevis}")
    for s in cfg.uten_tillatelse():
        print(f"Klippes IKKE:  {s.navn} ({s.plattform}), mangler dokumentert tillatelse")
    if not cfg.tillatte():
        print("Ingen streamere med tillatelse ennå. Legg dem inn i config.yaml.")
    return 0 if ok else 1


def arbeider(cfg: Config, ko: "queue.Queue") -> None:
    con = db.koble(cfg.db_sti)
    while True:
        streamer, segment, chatfil = ko.get()
        try:
            ider = pipeline.behandle(cfg, con, streamer, segment, chatfil=chatfil,
                                     start_tid=capture.start_tid(segment))
            if ider:
                log.info("%d nye klipp venter på godkjenning.", len(ider))
        except Exception:
            log.exception("Klarte ikke å behandle %s", segment)
        finally:
            segment.with_suffix(".ferdig").touch()
            if not cfg.video.behold_opptak:
                segment.unlink(missing_ok=True)


def start(cfg: Config, port: int) -> int:
    from .web import lag_app
    streamere = cfg.tillatte()
    for s in cfg.uten_tillatelse():
        log.warning("Hopper over %s: mangler dokumentert tillatelse.", s.navn)
    if not streamere:
        log.warning("Ingen streamere med tillatelse i config.yaml. Bare køen startes.")
    ko: queue.Queue = queue.Queue()
    threading.Thread(target=arbeider, args=(cfg, ko), daemon=True, name="arbeider").start()
    for s in streamere:
        capture.Opptaker(cfg, s, ko).start()
        log.info("Følger %s (%s).", s.navn, s.url)
    log.info("Godkjenningskøen: http://127.0.0.1:%d", port)
    lag_app(cfg).run(host="127.0.0.1", port=port, threaded=True)
    return 0


def behandle(cfg: Config, args) -> int:
    streamer = cfg.krev_tillatelse(args.streamer)
    con = db.koble(cfg.db_sti)
    ider = pipeline.behandle(cfg, con, streamer, Path(args.video),
                             chatfil=Path(args.chat) if args.chat else None,
                             start_tid=args.chat_start)
    print(f"{len(ider)} klipp lagt i køen. Kjør «klipper ko» for å se gjennom dem.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="klipper", description="Automatisk klipping av GTA 6-strømmer.")
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="kommando", required=True)
    sub.add_parser("sjekk", help="Sjekk oppsettet og vis hvem som klippes.")
    s = sub.add_parser("start", help="Følg streamerne live, klipp, og åpne godkjenningskøen.")
    s.add_argument("--port", type=int, default=8600)
    k = sub.add_parser("ko", help="Åpne bare godkjenningskøen.")
    k.add_argument("--port", type=int, default=8600)
    b = sub.add_parser("behandle", help="Klipp en video du allerede har på disk.")
    b.add_argument("video")
    b.add_argument("--streamer", required=True)
    b.add_argument("--chat", help="Chatlogg (jsonl) som hører til videoen.")
    b.add_argument("--chat-start", type=float, help="Epoch-tiden videoen startet.")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    les_env(Path(args.config).resolve().parent / ".env")
    try:
        cfg = last(args.config)
        if args.kommando == "sjekk":
            return sjekk(cfg)
        if args.kommando == "start":
            return start(cfg, args.port)
        if args.kommando == "ko":
            from .web import lag_app
            print(f"Godkjenningskøen: http://127.0.0.1:{args.port}")
            lag_app(cfg).run(host="127.0.0.1", port=args.port, threaded=True)
            return 0
        return behandle(cfg, args)
    except (ConfigError, PermissionError) as e:
        print(f"Feil: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
