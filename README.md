# clipper

Findet Twitch-Clips freigegebener Streamer, schneidet sie hochkant (1080x1920)
und laedt sie auf YouTube hoch. Claude waehlt die Clips aus und schreibt die Titel
(siehe `CLAUDE.md`), GitHub Actions erledigt den Rest.

## Ablauf
| Workflow | Wann | Was |
|---|---|---|
| `scan` | alle 2 Stunden | neue Clips suchen, `candidates.json` aktualisieren |
| `publish` | bei neuen Dateien in `queue/`, taeglich | herunterladen, schneiden, hochladen |
| `auth-youtube` | einmalig von Hand | YouTube-Kanal anmelden |

## Einrichtung
1. Secrets anlegen unter Settings > Secrets and variables > Actions:
   `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`
   und `TOKEN_KEY` (ein langes Zufallspasswort, mindestens 24 Zeichen).
2. Actions > `auth-youtube` > Run workflow. Im Log stehen ein Code und eine Adresse:
   Adresse oeffnen, mit dem Konto des YouTube-Kanals anmelden, Code eingeben.
   Der Schluessel landet verschluesselt als `youtube-token.enc` im Repository.
3. In `config.toml` je Streamer mit Erlaubnis einen `[[streamer]]`-Block eintragen,
   mit `permission_proof` (z. B. "E-Mail vom 20.11.2026").

## Hinweise
- Uploads sind privat, bis Google das API-Projekt geprueft hat.
- Ohne `permission_proof` wird ein Streamer nie verarbeitet.
