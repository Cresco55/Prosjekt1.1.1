import json
import subprocess
import time

import numpy as np
import pytest

from klipper import capture, chat, config, db, detect, pipeline, publish, render
from klipper.web import lag_app

CONFIG = """
streamere:
  - navn: MedLov
    tillatelse: {gitt: true, bevis: "E-post 18.11.2026"}
  - navn: UtenLov
  - navn: BareKryss
    tillatelse: {gitt: true}
datamappe: data
video: {teksting: false}
"""


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    return config.last(tmp_path / "config.yaml")


# ---- tillatelse -----------------------------------------------------------
def test_bare_streamere_med_bevis_er_tillatt(cfg):
    assert [s.navn for s in cfg.tillatte()] == ["MedLov"]
    assert {s.navn for s in cfg.uten_tillatelse()} == {"UtenLov", "BareKryss"}


def test_krev_tillatelse_stopper(cfg):
    assert cfg.krev_tillatelse("medlov").navn == "MedLov"
    with pytest.raises(config.ConfigError):
        cfg.krev_tillatelse("UtenLov")
    with pytest.raises(config.ConfigError):
        cfg.krev_tillatelse("finnesikke")


def test_opptak_og_klipping_nekter_uten_tillatelse(cfg, tmp_path):
    uten = cfg.finn("UtenLov")
    with pytest.raises(PermissionError):
        capture.Opptaker(cfg, uten, None)
    with pytest.raises(PermissionError):
        pipeline.behandle(cfg, db.koble(cfg.db_sti), uten, tmp_path / "x.ts")


def test_ukjent_innstilling_og_plattform(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("video: {tull: 1}", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.last(f)
    f.write_text("streamere: [{navn: abc, plattform: myspace}]", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.last(f)


def test_url(cfg):
    assert cfg.finn("MedLov").url == "https://www.twitch.tv/MedLov"
    assert cfg.finn("MedLov").kredit_tekst == "twitch.tv/MedLov"


# ---- chat og deteksjon ----------------------------------------------------
def test_tolk_chatlinje():
    m = chat.tolk_linje(":ola!ola@ola.tmi.twitch.tv PRIVMSG #kanal :KEKW no way: han klarte det")
    assert m == {"user": "ola", "msg": "KEKW no way: han klarte det"}
    assert chat.tolk_linje("PING :tmi.twitch.tv") is None
    assert chat.tolk_linje(":tmi.twitch.tv 001 justinfan1 :Welcome") is None


def test_chat_signal_vekter_reaksjoner():
    meldinger = [{"t": 1000 + 50 + detect.CHAT_FORSINKELSE, "msg": "KEKW"},
                 {"t": 1000 + 50 + detect.CHAT_FORSINKELSE, "msg": "hva skjer i dag"},
                 {"t": 5000, "msg": "utenfor"}]
    s = detect.chat_signal(meldinger, 1000, 120)
    assert len(s) == 120 and s[50] == 3.0 and s.sum() == 3.0


def test_finner_toppen_i_chat_og_lyd():
    rng = np.random.default_rng(1)
    chat_s = rng.poisson(1.0, 1200).astype(float)
    lyd_s = rng.normal(100, 5, 1200)
    chat_s[600:610] += 15
    lyd_s[598:604] += 80
    funn = detect.finn_hoydepunkter(chat_s, lyd_s, 1200)
    assert len(funn) == 1
    h = funn[0]
    assert h.start < 600 < h.slutt and 25 <= h.lengde <= 40
    assert len(h.grunner) == 2


def test_rolig_opptak_gir_ingen_klipp():
    rng = np.random.default_rng(2)
    assert detect.finn_hoydepunkter(rng.poisson(1.0, 1200).astype(float),
                                    rng.normal(100, 5, 1200), 1200) == []
    assert detect.finn_hoydepunkter(None, np.zeros(600), 600) == []


def test_min_avstand_og_maks():
    lyd_s = np.full(3600, 100.0) + np.random.default_rng(3).normal(0, 2, 3600)
    for t in range(100, 3500, 100):
        lyd_s[t:t + 5] += 200
    lyd_s[105:110] += 200  # rett ved siden av en annen topp
    funn = detect.finn_hoydepunkter(None, lyd_s, 3600, maks_per_time=4)
    assert len(funn) == 4
    topper = sorted(h.topp for h in funn)
    assert all(b - a >= 45 for a, b in zip(topper, topper[1:]))


# ---- teksting -------------------------------------------------------------
def test_ass_tekst():
    ordl = [{"start": 0.0, "slutt": 0.3, "ord": " no"}, {"start": 0.3, "slutt": 0.6, "ord": " way"},
            {"start": 0.6, "slutt": 0.9, "ord": " {bro}"}, {"start": 1.0, "slutt": 1.4, "ord": " what"},
            {"start": 5.0, "slutt": 5.4, "ord": " ok"}]
    assert [l["tekst"] for l in render.grupper_ord(ordl)] == ["no way {bro}", "what", "ok"]
    ass = render.lag_ass(ordl, "twitch.tv/MedLov", 30)
    assert "Kreditt,,0,0,0,,twitch.tv/MedLov" in ass
    assert "NO WAY (BRO)" in ass and "0:00:05.00" in ass


# ---- kø og publisering ----------------------------------------------------
def _klipp(con, fil="x.mp4"):
    return db.legg_til(con, streamer="MedLov", kilde="a.ts", start=1, slutt=30, poeng=4.2,
                       grunner=["høy lyd"], fil=fil, tittel="T", beskrivelse="B")


def test_bare_godkjente_klipp_publiseres(cfg, monkeypatch):
    con = db.koble(cfg.db_sti)
    kid = _klipp(con)
    kalt = []
    monkeypatch.setattr(publish, "last_opp", lambda *a: kalt.append(a) or {"ok": True})
    assert publish.publiser_klipp(con, kid, ["tiktok"]) is False and not kalt
    assert db.bytt_status(con, kid, ("venter",), "godkjent")
    assert publish.publiser_klipp(con, kid, ["tiktok"]) is True and len(kalt) == 1
    assert db.hent(con, kid)["status"] == "publisert"
    assert publish.publiser_klipp(con, kid, ["tiktok"]) is False and len(kalt) == 1


def test_feilet_opplasting_merkes(cfg, monkeypatch):
    con = db.koble(cfg.db_sti)
    kid = _klipp(con)
    db.bytt_status(con, kid, ("venter",), "godkjent")
    monkeypatch.delenv("UPLOAD_POST_API_KEY", raising=False)
    assert publish.publiser_klipp(con, kid, ["tiktok"]) is False
    k = db.hent(con, kid)
    assert k["status"] == "feilet" and "UPLOAD_POST_API_KEY" in k["feil"]


def test_skjemafelt():
    felt = publish.skjemafelt(["tiktok", "youtube"], "x" * 150, "besk", "meg")
    d = dict(felt)
    assert [v for k, v in felt if k == "platform[]"] == ["tiktok", "youtube"]
    assert len(d["youtube_title"]) == 100 and d["tiktok_title"] == "besk"
    assert "instagram_title" not in d


def test_godkjenning_i_nettsiden(cfg, monkeypatch):
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "k")
    monkeypatch.setenv("UPLOAD_POST_USER", "u")
    sendt = []
    monkeypatch.setattr(publish, "last_opp", lambda *a: sendt.append(a) or {"ok": True})
    con = db.koble(cfg.db_sti)
    kid = _klipp(con)
    klient = lag_app(cfg, publiser_i_bakgrunnen=False).test_client()
    assert "MedLov" in klient.get("/").get_data(as_text=True)

    # uten avkrysning for reglene skjer det ingenting
    klient.post(f"/klipp/{kid}", data={"valg": "godkjenn", "tittel": "Ny", "beskrivelse": "B"})
    assert db.hent(con, kid)["status"] == "venter" and not sendt
    # fra en annen nettside avvises forsøket
    assert klient.post(f"/klipp/{kid}", data={"valg": "godkjenn", "regler": "1"},
                       headers={"Origin": "https://ond.example"}).status_code == 403
    assert db.hent(con, kid)["status"] == "venter"

    klient.post(f"/klipp/{kid}", data={"valg": "godkjenn", "regler": "1", "tittel": "Ny", "beskrivelse": "B"})
    k = db.hent(con, kid)
    assert k["status"] == "publisert" and k["tittel"] == "Ny"
    assert sendt[0][1] == "Ny" and sendt[0][3] == ["tiktok", "instagram", "youtube"]


def test_avvis_og_angre(cfg):
    con = db.koble(cfg.db_sti)
    kid = _klipp(con)
    klient = lag_app(cfg).test_client()
    klient.post(f"/klipp/{kid}", data={"valg": "avvis"})
    assert db.hent(con, kid)["status"] == "avvist"
    klient.post(f"/klipp/{kid}", data={"valg": "angre"})
    assert db.hent(con, kid)["status"] == "venter"


# ---- opptak ---------------------------------------------------------------
def test_segmenter(tmp_path):
    for navn in ("20261118T160000Z", "20261118T161000Z", "20261118T162000Z"):
        (tmp_path / f"{navn}.ts").touch()
    (tmp_path / "20261118T160000Z.ferdig").touch()
    assert [p.stem for p in capture.ferdige_segmenter(tmp_path, True)] == ["20261118T161000Z"]
    assert len(capture.ferdige_segmenter(tmp_path, False)) == 2
    assert capture.start_tid(tmp_path / "20261118T160000Z.ts") == 1795017600.0


def test_streamlink_kommando(cfg):
    k = capture.streamlink_kommando(cfg.finn("MedLov"))
    assert k[1:3] == ["-m", "streamlink"] and "https://www.twitch.tv/MedLov" in k and "--twitch-disable-ads" in k


# ---- hele løpet med en ekte video ----------------------------------------
def test_fra_video_til_klipp_i_koen(cfg, tmp_path):
    video = tmp_path / "20261118T160000Z.ts"
    # 3 minutter testbilde med lav tone, og et høyt smell rundt 1:30
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=15:d=180",
         "-f", "lavfi", "-i",
         "sine=f=220:d=180,volume='if(between(t,88,93),1.0,0.02)':eval=frame",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(video)],
        check=True)
    start = capture.start_tid(video)
    chatfil = tmp_path / "chat.jsonl"
    with chatfil.open("w") as f:
        for i in range(0, 180, 4):
            f.write(json.dumps({"t": start + i, "user": "a", "msg": "hei"}) + "\n")
        for i in range(40):
            f.write(json.dumps({"t": start + 94 + i * 0.2, "user": "b", "msg": "OMEGALUL"}) + "\n")

    con = db.koble(cfg.db_sti)
    ider = pipeline.behandle(cfg, con, cfg.finn("MedLov"), video, chatfil=chatfil, start_tid=start)
    assert len(ider) == 1
    k = db.hent(con, ider[0])
    assert k["status"] == "venter" and k["start"] < 90 < k["slutt"]
    assert k["tittel"] == "MedLov | GTA 6" and "twitch.tv/MedLov" in k["beskrivelse"]
    ut = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", k["fil"]], capture_output=True, text=True).stdout.strip()
    assert ut == "1080,1920"
    assert 25 <= detect.varighet_av(k["fil"]) <= 40
