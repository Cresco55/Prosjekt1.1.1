"""Innstillinger og listen over streamere.

Den viktigste regelen i hele systemet ligger her: en streamer blir bare tatt
opp når ``tillatelse.gitt`` er sann og det står hvor tillatelsen er
dokumentert. Alt annet i programmet går gjennom ``Config.tillatte()``.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PLATTFORMER = ("tiktok", "instagram", "youtube")
_URL_MAL = {
    "twitch": "https://www.twitch.tv/{navn}",
    "kick": "https://kick.com/{navn}",
    "youtube": "https://www.youtube.com/@{navn}/live",
}
_NAVN = re.compile(r"^[A-Za-z0-9_.\-]{2,50}$")


class ConfigError(ValueError):
    pass


@dataclass
class Streamer:
    navn: str
    plattform: str = "twitch"
    tillatelse_gitt: bool = False
    tillatelse_bevis: str = ""
    tillatelse_dato: str = ""
    kreditering: str = ""

    @property
    def url(self) -> str:
        return _URL_MAL[self.plattform].format(navn=self.navn)

    @property
    def har_tillatelse(self) -> bool:
        return bool(self.tillatelse_gitt and self.tillatelse_bevis.strip())

    @property
    def kredit_tekst(self) -> str:
        if self.kreditering:
            return self.kreditering
        vert = {"twitch": "twitch.tv/", "kick": "kick.com/", "youtube": "youtube.com/@"}
        return vert[self.plattform] + self.navn


@dataclass
class Deteksjon:
    terskel: float = 4.0          # hvor mye over normalen et øyeblikk må være
    vekt_chat: float = 1.0
    vekt_lyd: float = 0.7
    sekunder_for: float = 22.0    # klippet starter så lenge før toppen
    sekunder_etter: float = 10.0  # og slutter så lenge etter
    min_avstand: float = 45.0     # minst så langt mellom to klipp
    maks_per_time: int = 6


@dataclass
class Video:
    layout: str = "blur"          # "blur" (hele bildet, uskarp bakgrunn) eller "crop"
    teksting: bool = True
    whisper_modell: str = "small"
    sprak: str | None = None      # None = finn språket automatisk
    segment_sekunder: int = 600
    behold_opptak: bool = False   # råopptak slettes når de er klippet


@dataclass
class Publisering:
    plattformer: list[str] = field(default_factory=lambda: list(PLATTFORMER))
    tittel_mal: str = "{streamer} | GTA 6"
    beskrivelse_mal: str = "Klipp fra {kreditering} (delt med tillatelse) #gta6 #gtavi #gaming"


@dataclass
class Config:
    streamere: list[Streamer]
    datamappe: Path
    deteksjon: Deteksjon = field(default_factory=Deteksjon)
    video: Video = field(default_factory=Video)
    publisering: Publisering = field(default_factory=Publisering)

    def tillatte(self) -> list[Streamer]:
        return [s for s in self.streamere if s.har_tillatelse]

    def uten_tillatelse(self) -> list[Streamer]:
        return [s for s in self.streamere if not s.har_tillatelse]

    def finn(self, navn: str) -> Streamer:
        for s in self.streamere:
            if s.navn.lower() == navn.lower():
                return s
        raise ConfigError(f"Streameren «{navn}» står ikke i config.yaml.")

    def krev_tillatelse(self, navn: str) -> Streamer:
        s = self.finn(navn)
        if not s.har_tillatelse:
            raise ConfigError(
                f"«{s.navn}» mangler dokumentert tillatelse. Sett tillatelse.gitt: true "
                "og fyll inn tillatelse.bevis i config.yaml før denne streameren kan klippes."
            )
        return s

    @property
    def db_sti(self) -> Path:
        return self.datamappe / "klipper.db"


def _dataclass_fra(cls, data: dict | None, sted: str):
    data = data or {}
    kjente = set(cls.__dataclass_fields__)
    ukjente = set(data) - kjente
    if ukjente:
        raise ConfigError(f"Ukjent innstilling under {sted}: {', '.join(sorted(ukjente))}")
    return cls(**data)


def les_streamer(rad: dict) -> Streamer:
    if not isinstance(rad, dict) or not rad.get("navn"):
        raise ConfigError("Hver streamer må ha et navn.")
    navn = str(rad["navn"]).strip().lstrip("@")
    if not _NAVN.match(navn):
        raise ConfigError(f"Ugyldig kanalnavn: «{navn}»")
    plattform = str(rad.get("plattform", "twitch")).lower()
    if plattform not in _URL_MAL:
        raise ConfigError(f"Ukjent plattform for {navn}: {plattform}")
    t = rad.get("tillatelse") or {}
    return Streamer(
        navn=navn,
        plattform=plattform,
        tillatelse_gitt=t.get("gitt") is True,
        tillatelse_bevis=str(t.get("bevis") or ""),
        tillatelse_dato=str(t.get("dato") or ""),
        kreditering=str(rad.get("kreditering") or ""),
    )


def last(sti: str | os.PathLike = "config.yaml") -> Config:
    sti = Path(sti)
    if not sti.exists():
        raise ConfigError(f"Fant ikke {sti}. Kopier config.example.yaml til config.yaml og fyll inn.")
    data = yaml.safe_load(sti.read_text(encoding="utf-8")) or {}
    streamere = [les_streamer(r) for r in data.get("streamere") or []]
    pub = _dataclass_fra(Publisering, data.get("publisering"), "publisering")
    ukjente = [p for p in pub.plattformer if p not in PLATTFORMER]
    if ukjente:
        raise ConfigError(f"Ukjent plattform under publisering: {', '.join(ukjente)}")
    video = _dataclass_fra(Video, data.get("video"), "video")
    if video.layout not in ("blur", "crop"):
        raise ConfigError("video.layout må være «blur» eller «crop».")
    datamappe = Path(data.get("datamappe") or "data")
    if not datamappe.is_absolute():
        datamappe = (sti.parent / datamappe).resolve()
    return Config(
        streamere=streamere,
        datamappe=datamappe,
        deteksjon=_dataclass_fra(Deteksjon, data.get("deteksjon"), "deteksjon"),
        video=video,
        publisering=pub,
    )
