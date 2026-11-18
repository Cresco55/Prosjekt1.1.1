"""Klipp ut et øyeblikk og gjør det om til en stående video (1080x1920) med teksting.

Teksting lages med faster-whisper hvis det er installert (``pip install
"klipper[teksting]"``). Uten det lages klippet uten teksting. Kreditering av
streameren brennes alltid inn øverst i bildet.
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

BREDDE, HOYDE = 1080, 1920

ASS_HODE = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {BREDDE}
PlayResY: {HOYDE}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Tekst,Inter,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,430,1
Style: Kreditt,Inter,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,8,40,40,70,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

FILTER = {
    "blur": (
        "[0:v]split[a][b];"
        f"[a]scale={BREDDE}:{HOYDE}:force_original_aspect_ratio=increase,"
        f"crop={BREDDE}:{HOYDE},boxblur=24:4,eq=brightness=-0.12[bg];"
        f"[b]scale={BREDDE}:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2"
    ),
    "crop": f"[0:v]scale=-2:{HOYDE},crop={BREDDE}:{HOYDE}",
}


def _tid(sekunder: float) -> str:
    sekunder = max(0.0, sekunder)
    hs = int(round(sekunder * 100))
    return f"{hs // 360000}:{hs // 6000 % 60:02d}:{hs // 100 % 60:02d}.{hs % 100:02d}"


def _rens(tekst: str) -> str:
    return tekst.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def grupper_ord(ordliste: list[dict], maks_ord: int = 3, maks_sek: float = 1.6) -> list[dict]:
    """Slå ord sammen til korte tekstlinjer som er lette å lese på mobil."""
    linjer, gjeldende = [], []
    for o in ordliste:
        if gjeldende and (len(gjeldende) >= maks_ord
                          or o["slutt"] - gjeldende[0]["start"] > maks_sek
                          or o["start"] - gjeldende[-1]["slutt"] > 0.7):
            linjer.append(gjeldende)
            gjeldende = []
        gjeldende.append(o)
    if gjeldende:
        linjer.append(gjeldende)
    return [{"start": g[0]["start"], "slutt": g[-1]["slutt"],
             "tekst": " ".join(o["ord"].strip() for o in g)} for g in linjer]


def lag_ass(ordliste: list[dict], kreditt: str, lengde: float, forskyvning: float = 0.0) -> str:
    """ASS-tekst for klippet. ``forskyvning`` legges til alle tider (se ``_sok``)."""
    f = forskyvning
    rader = [f"Dialogue: 0,{_tid(f)},{_tid(f + lengde)},Kreditt,,0,0,0,,{_rens(kreditt)}"]
    for linje in grupper_ord(ordliste):
        if linje["slutt"] <= linje["start"] or linje["start"] >= lengde:
            continue
        rader.append(f"Dialogue: 1,{_tid(f + linje['start'])},{_tid(f + min(linje['slutt'], lengde))},"
                     f"Tekst,,0,0,0,,{_rens(linje['tekst']).upper()}")
    return ASS_HODE + "\n".join(rader) + "\n"


FORSPRANG = 20.0


def _sok(video: Path, start: float, lengde: float) -> tuple[list[str], float]:
    """ffmpeg-argumenter som treffer ``start`` nøyaktig, og forspranget som ble brukt.

    I .ts-opptak lander et hopp på nærmeste nøkkelbilde, som kan ligge flere
    sekunder unna. Derfor hoppes det til litt før, og resten spoles nøyaktig.
    """
    forsprang = min(FORSPRANG, start)
    return (["-ss", f"{start - forsprang:.2f}", "-i", str(Path(video).resolve()),
             "-ss", f"{forsprang:.2f}", "-t", f"{lengde:.2f}"], forsprang)


_modeller: dict = {}


def transkriber(video: Path, start: float, lengde: float, modell: str = "small",
                sprak: str | None = None) -> list[dict]:
    """Ord med tidspunkt (sekunder fra klippets start). Tom liste hvis whisper mangler."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log.warning("faster-whisper er ikke installert, så klippet lages uten teksting.")
        return []
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "lyd.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", *_sok(video, start, lengde)[0],
                        "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
        if modell not in _modeller:
            _modeller[modell] = WhisperModel(modell, compute_type="int8")
        segmenter, _ = _modeller[modell].transcribe(
            str(wav), language=sprak, word_timestamps=True, vad_filter=True)
        return [{"start": float(o.start), "slutt": float(o.end), "ord": o.word}
                for s in segmenter for o in (s.words or [])]


def lag_klipp(video: Path, start: float, slutt: float, utfil: Path, *, kreditt: str,
              layout: str = "blur", ordliste: list[dict] | None = None) -> Path:
    """Skriv det ferdige stående klippet til ``utfil``."""
    utfil = Path(utfil).resolve()
    utfil.parent.mkdir(parents=True, exist_ok=True)
    lengde = slutt - start
    ass = utfil.with_suffix(".ass")
    sok, forsprang = _sok(video, start, lengde)
    ass.write_text(lag_ass(ordliste or [], kreditt, lengde, forsprang), encoding="utf-8")
    # ffmpeg kjøres fra utmappen, så filternavnet slipper spesialtegn fra stien.
    kommando = [
        "ffmpeg", "-v", "error", "-y", *sok,
        "-filter_complex", f"{FILTER[layout]},ass={ass.name}[v]",
        "-map", "[v]", "-map", "0:a?", "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", utfil.name,
    ]
    resultat = subprocess.run(kommando, cwd=utfil.parent, capture_output=True, text=True)
    ass.unlink(missing_ok=True)
    if resultat.returncode != 0 or not utfil.exists():
        raise RuntimeError(f"ffmpeg klarte ikke å lage klippet: {resultat.stderr.strip()[-500:]}")
    return utfil
