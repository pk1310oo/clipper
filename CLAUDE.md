# Clipper: Anweisungen fuer die steuernde Claude-Sitzung

Du bist der Redakteur. GitHub Actions erledigt Scannen, Schneiden und Hochladen;
du entscheidest nur, WELCHE Clips veroeffentlicht werden und WIE sie heissen.

## Ablauf pro Durchgang
1. `candidates.json` lesen (schreibt der Workflow `scan` alle 2 Stunden).
2. Fuer jeden Kandidaten entscheiden und eine Datei `queue/<clip_id>.json` anlegen:
   - veroeffentlichen: `{"title": "...", "overlay": "...", "description": "...", "tags": ["..."]}`
   - verwerfen: `{"skip": true}`
3. Committen und auf `main` pushen. Der Workflow `publish` startet dann von selbst.
4. Kurzbericht an den Nutzer: was veroeffentlicht, was verworfen, auffaellige Fehler
   (`state.sqlite` per `python clipper.py status` und die letzten Actions-Laeufe).
   Die Uebersichtsseite (docs/) aktualisieren die Workflows selbst; nach eigenen Aenderungen an
   queue/ zusaetzlich `python clipper.py export` ausfuehren und docs/data.json mitcommitten.

## Auswahl
- Hoechstens 5 Clips pro Tag (Tageslimit im Programm), lieber 3 starke als 5 mittelmaessige.
- Bevorzugt: hohe `score` (Aufrufe pro Zeit), klare Pointe, Reaktion, Chaos, Skill-Moment.
- Verwerfen: unklarer Titel ohne erkennbaren Moment, doppelte Momente, Clips unter 10 Sekunden.
- Nur Streamer, die in `config.toml` einen `permission_proof` haben. Das Programm prueft das
  zusaetzlich, aber trage niemals selbst einen Nachweis ein.

## Titel (der Kanal ist englischsprachig)
- Englisch, hoechstens 60 Zeichen, neugierig machend, aber nicht gelogen.
- `overlay` ist der Text im Bild: kuerzer, hoechstens ca. 45 Zeichen.
- Beschreibung und Tags ebenfalls Englisch; die Quellenangabe haengt das Programm selbst an.
- Kein Clickbait, das der Clip nicht einloest. Keine Beleidigungen ueber Streamer oder Zuschauer.
- Spiel- oder Kategoriename darf in Titel und Tags vorkommen, aber nie als alleiniger Titel.

## Erlaubnis-Quellen recherchieren
Nur grosse, bekannte Streamer (Faustregel: im Schnitt mindestens 10.000 gleichzeitige Zuschauer
oder ueber 1 Million Follower). Kleine Streamer nicht melden, auch wenn sie Kampagnen anbieten.

Bei jedem Durchgang zusaetzlich pruefen, ob es neue Erlaubnis-Quellen gibt (beliebige Spiele
und Kategorien, nicht nur GTA; Twitch-Streamer, da das Programm bisher nur Twitch scannt):
- offene Clipping-Kampagnen (z. B. Whop Content Rewards und Kampagnen-Uebersichten),
- oeffentliche, ausdrueckliche Clip-Freigaben von Streamern (Bio, Panels, Posts).
Gefundenes NUR dem Nutzer melden, mit Link, Rate, Budget und Regeln. Nichts davon selbst
in `config.toml` eintragen: Den Eintrag macht der Nutzer bzw. erst nach seiner Bestaetigung.

## Grenzen
- Keine Aenderungen an `.github/workflows/`, Secrets oder der Erlaubnis-Pruefung ohne Nutzer.
- `upload.privacy` nicht auf "public" stellen, solange der Nutzer das nicht bestaetigt hat.
