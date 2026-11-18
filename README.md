# Klipper

Følger GTA 6-strømmer live, finner de beste øyeblikkene automatisk, lager stående klipp (9:16) med teksting, og laster dem opp til TikTok, Instagram og YouTube etter at du har godkjent dem.

```
live-strøm ──> opptak i biter ──> finn øyeblikk ──> 9:16 + teksting ──> godkjenningskø ──> TikTok / Instagram / YouTube
 (streamlink)   (10 min om gangen)  (chat + lyd)      (ffmpeg, whisper)   (du ser gjennom)    (Upload-Post)
```

## To regler som er bygget inn

1. **Bare streamere med tillatelse.** En streamer blir ikke tatt opp før `tillatelse.gitt: true` og `tillatelse.bevis` er fylt ut i `config.yaml`. Å klippe andres strømmer uten avtale er brudd på opphavsretten, og kreditering eller korte klipp endrer ikke på det. Få tillatelsen skriftlig, eller bruk streamerens eget klippeprogram.
2. **Ingenting publiseres uten at du har godkjent det.** Før du godkjenner et klipp må du krysse av for at det følger Rockstar sine regler: ingen spoilere eller mellomsekvenser fra historien, ikke lekket materiale, ikke juks eller mod-menyer.

Merk også at Rockstar sin toleranse for videoer er formulert for ikke-kommersiell bruk, at Take-Two kan kreve innhold fjernet, og at YouTube kan nekte inntektsgivende visning for «gjenbrukt innhold» selv når du har tillatelse fra streameren.

## Kom i gang

Du trenger Python 3.10 eller nyere og [ffmpeg](https://ffmpeg.org/download.html).

```bash
git clone https://github.com/Cresco55/Prosjekt1.1.1.git
cd Prosjekt1.1.1
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[teksting]"       # uten [teksting] lages klipp uten teksting

cp config.example.yaml config.yaml # Windows: copy
cp .env.example .env
```

1. Åpne `config.yaml` og legg inn streamerne du har avtale med.
2. Lag en konto på [upload-post.com](https://upload-post.com), koble til TikTok, Instagram og YouTube der, og legg API-nøkkelen og profilnavnet i `.env`.
3. Sjekk oppsettet:

```bash
klipper sjekk
```

4. Start:

```bash
klipper start
```

Programmet følger nå streamerne. Når en av dem går live, tas strømmen opp, og nye klipp dukker opp på <http://127.0.0.1:8600> etter hvert. Der ser du gjennom dem, retter tittel og beskrivelse, og godkjenner eller avviser.

## Kommandoer

| Kommando | Hva den gjør |
| --- | --- |
| `klipper sjekk` | Sjekker at alt er installert og viser hvem som klippes |
| `klipper start` | Følger streamerne live, klipper, og åpner godkjenningskøen |
| `klipper ko` | Åpner bare godkjenningskøen |
| `klipper behandle video.mp4 --streamer navn` | Klipper en video du allerede har på disk |

## Slik velges øyeblikkene

Hvert sekund av opptaket får poeng fra to signaler:

- **Chat** (Twitch): hvor fort meldingene kommer, med ekstra vekt på reaksjoner som LUL, KEKW, POG og «clip».
- **Lyd**: hvor høyt det er, altså skrik, latter og eksplosjoner.

Begge måles mot det som er normalt i akkurat det opptaket. Topper over `deteksjon.terskel` blir klipp, som starter 22 sekunder før toppen og slutter 10 sekunder etter. For Kick og YouTube brukes bare lyd. Alt kan justeres i `config.yaml`.

## Begrensninger

- Et øyeblikk som ligger akkurat i overgangen mellom to opptaksbiter kan bli kuttet kort.
- Titler og beskrivelser lages fra en mal. Du retter dem i køen før publisering.
- Teksting på første klipp tar litt tid, fordi språkmodellen lastes ned én gang.

## Tester

```bash
pip install -e ".[dev]"
pytest
```

## Bygger på

Se [NOTICE.md](NOTICE.md).
