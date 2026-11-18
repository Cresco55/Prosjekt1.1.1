"""Godkjenningskøen: en liten nettside der du ser gjennom klippene før de publiseres.

Kjører bare lokalt (127.0.0.1). Et klipp publiseres først når du har krysset
av for at det følger Rockstar sine regler og trykket «Godkjenn og publiser».
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from flask import Flask, abort, redirect, render_template_string, request, send_file, url_for

from . import db, publish
from .config import Config

SIDE = """<!doctype html>
<html lang="nb"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Klippekø</title>
<style>
:root { color-scheme: light dark; --kant: #8884; --dempet: #888; --ok: #1a7f37; --nei: #b42318; }
body { font: 15px/1.45 system-ui, sans-serif; margin: 0 auto; max-width: 1100px; padding: 16px; }
h1 { font-size: 22px; margin: 8px 0 4px; }
nav { display: flex; gap: 8px; flex-wrap: wrap; margin: 12px 0 20px; }
nav a { padding: 6px 12px; border: 1px solid var(--kant); border-radius: 999px; text-decoration: none; color: inherit; }
nav a.valgt { background: #8883; font-weight: 600; }
.varsel { border: 1px solid var(--kant); border-left: 4px solid #d9822b; padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; }
.kort { display: grid; grid-template-columns: 240px 1fr; gap: 18px; border: 1px solid var(--kant); border-radius: 10px; padding: 14px; margin-bottom: 16px; }
@media (max-width: 640px) { .kort { grid-template-columns: 1fr; } }
video { width: 100%; border-radius: 8px; background: #000; aspect-ratio: 9/16; }
label { display: block; font-weight: 600; margin-top: 10px; }
input[type=text], textarea { font-weight: 400; margin-top: 4px; width: 100%; box-sizing: border-box; padding: 8px; font-family: inherit; font-size: inherit; border: 1px solid var(--kant); border-radius: 6px; background: transparent; color: inherit; }
.sjekk { font-weight: 400; display: flex; gap: 8px; align-items: flex-start; }
.dempet { color: var(--dempet); font-size: 13px; }
.knapper { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
button { font: inherit; padding: 8px 14px; border-radius: 6px; border: 1px solid var(--kant); cursor: pointer; background: transparent; color: inherit; }
button.ok { background: var(--ok); color: #fff; border-color: var(--ok); }
button.nei { color: var(--nei); }
.feil { color: var(--nei); }
</style></head><body>
<h1>Klippekø</h1>
<div class="dempet">Ingenting publiseres før du har godkjent det her.</div>
<nav>{% for s, navn in faner %}
<a href="{{ url_for('ko', status=s) }}" class="{{ 'valgt' if s == status }}">{{ navn }} ({{ antall.get(s, 0) }})</a>
{% endfor %}</nav>
{% if not oppsett %}<div class="varsel">Opplasting er ikke satt opp ennå. Legg inn UPLOAD_POST_API_KEY og UPLOAD_POST_USER i .env, så kan godkjente klipp publiseres.</div>{% endif %}
{% for k in klipp %}
<div class="kort">
  <video src="{{ url_for('video', klipp_id=k['id']) }}" controls preload="metadata"></video>
  <form method="post" action="{{ url_for('handling', klipp_id=k['id']) }}">
    <div><strong>{{ k['streamer'] }}</strong> <span class="dempet">· poeng {{ '%.1f' % k['poeng'] }} · {{ '%.0f' % (k['slutt'] - k['start']) }} sek · {{ k['kilde'] }}</span></div>
    <div class="dempet">{{ grunner[k['id']] }}</div>
    {% if k['status'] in ('venter', 'feilet', 'godkjent') %}
    <label>Tittel <input type="text" name="tittel" value="{{ k['tittel'] }}" maxlength="100"></label>
    <label>Beskrivelse <textarea name="beskrivelse" rows="3">{{ k['beskrivelse'] }}</textarea></label>
    {% else %}
    <p><strong>{{ k['tittel'] }}</strong><br>{{ k['beskrivelse'] }}</p>
    {% endif %}
    {% if k['transkripsjon'] %}<p class="dempet">«{{ k['transkripsjon'] }}»</p>{% endif %}
    {% if k['feil'] %}<p class="feil">{{ k['feil'] }}</p>{% endif %}
    {% if k['status'] in ('venter', 'feilet') %}
    <label class="sjekk"><input type="checkbox" name="regler" value="1">
      <span>Jeg har sett klippet. Det inneholder ingen spoilere eller mellomsekvenser fra historien, ikke lekket materiale og ikke juks eller mod-menyer.</span></label>
    <div class="knapper">
      <button class="ok" name="valg" value="godkjenn">Godkjenn og publiser til {{ plattformer }}</button>
      <button name="valg" value="lagre">Lagre tekst</button>
      <button class="nei" name="valg" value="avvis">Avvis</button>
    </div>
    {% elif k['status'] == 'avvist' %}
    <div class="knapper"><button name="valg" value="angre">Legg tilbake i køen</button></div>
    {% endif %}
  </form>
</div>
{% else %}<p class="dempet">Ingen klipp her akkurat nå.</p>{% endfor %}
{% if melding %}<p class="feil">{{ melding }}</p>{% endif %}
</body></html>"""

NAVN = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube"}
FANER = [("venter", "Venter"), ("publiserer", "Publiserer"), ("publisert", "Publisert"),
         ("feilet", "Feilet"), ("avvist", "Avvist")]


def lag_app(cfg: Config, publiser_i_bakgrunnen: bool = True) -> Flask:
    app = Flask(__name__)
    con = db.koble(cfg.db_sti)
    klippmappe = (cfg.datamappe / "klipp").resolve()

    @app.get("/")
    def ko():
        status = request.args.get("status", "venter")
        if status not in db.STATUSER:
            abort(404)
        klipp = db.liste(con, status)
        grunner = {k["id"]: ", ".join(json.loads(k["grunner"])) or "over terskelen samlet"
                   for k in klipp}
        return render_template_string(
            SIDE, klipp=klipp, status=status, antall=db.antall(con), faner=FANER,
            grunner=grunner, oppsett=publish.er_satt_opp(), melding=request.args.get("melding"),
            plattformer=", ".join(NAVN.get(p, p) for p in cfg.publisering.plattformer))

    @app.get("/video/<int:klipp_id>")
    def video(klipp_id):
        k = db.hent(con, klipp_id)
        if not k:
            abort(404)
        fil = Path(k["fil"]).resolve()
        if klippmappe not in fil.parents or not fil.exists():
            abort(404)
        return send_file(fil, mimetype="video/mp4", conditional=True)

    @app.post("/klipp/<int:klipp_id>")
    def handling(klipp_id):
        # Bare skjemaet på denne siden får endre køen, ikke andre nettsider i nettleseren.
        opphav = request.headers.get("Origin")
        if opphav and opphav.split("://", 1)[-1] != request.host:
            abort(403)
        k = db.hent(con, klipp_id)
        if not k:
            abort(404)
        valg = request.form.get("valg")
        tilbake = lambda **kw: redirect(url_for("ko", status=k["status"], **kw))
        if valg in ("godkjenn", "lagre") and "tittel" in request.form:
            db.oppdater_tekst(con, klipp_id, request.form["tittel"].strip()[:100],
                              request.form.get("beskrivelse", "").strip()[:2000])
        if valg == "godkjenn":
            if request.form.get("regler") != "1":
                return tilbake(melding="Kryss av for at klippet følger reglene før du godkjenner.")
            if not publish.er_satt_opp():
                return tilbake(melding="Opplasting er ikke satt opp ennå, så klippet ble ikke godkjent.")
            if db.bytt_status(con, klipp_id, ("venter", "feilet"), "godkjent"):
                if publiser_i_bakgrunnen:
                    threading.Thread(target=publish.publiser_klipp, daemon=True,
                                     args=(con, klipp_id, cfg.publisering.plattformer)).start()
                else:
                    publish.publiser_klipp(con, klipp_id, cfg.publisering.plattformer)
        elif valg == "avvis":
            db.bytt_status(con, klipp_id, ("venter", "feilet"), "avvist")
        elif valg == "angre":
            db.bytt_status(con, klipp_id, ("avvist",), "venter")
        return tilbake()

    return app
