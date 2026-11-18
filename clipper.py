#!/usr/bin/env python3
"""Clip-Pipeline: Twitch-Clips freigegebener Streamer finden, bewerten,
hochkant schneiden und auf YouTube hochladen.

Jeder Schritt ist ein eigenes Kommando, damit die steuernde Instanz
(Claude als geplante Aufgabe oder ein Mensch) dazwischen entscheiden kann,
welcher Clip es wert ist und wie er heissen soll:

    clipper.py scan                      Kandidaten suchen und bewerten
    clipper.py fetch  <clip_id>          Clip herunterladen
    clipper.py render <clip_id> --title  9:16-Fassung erzeugen
    clipper.py upload <clip_id> --title  auf YouTube hochladen
    clipper.py publish-queue             Entscheidungen aus queue/ ausfuehren
    clipper.py status                    Uebersicht
    clipper.py auth-youtube              einmalige YouTube-Anmeldung

Nur Streamer mit eingetragenem Erlaubnis-Nachweis werden verarbeitet.
Benoetigt: Python 3.11+, ffmpeg, yt-dlp. Sonst nur Standardbibliothek.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"
OUT = ROOT / "out"
DB_PATH = ROOT / "state.sqlite"
CONFIG_PATH = ROOT / "config.toml"
SECRETS_PATH = ROOT / "secrets.toml"

YT_SCOPE = "https://www.googleapis.com/auth/youtube"
UTC = dt.timezone.utc


class Fail(Exception):
    """Fehler mit einer Meldung, die der Bediener direkt beheben kann."""


class DailyLimit(Fail):
    """Tageslimit erreicht: spaeter erneut versuchen."""


# ---------------------------------------------------------------- Grundlagen

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise Fail(f"{CONFIG_PATH.name} fehlt. config.example.toml kopieren und ausfuellen.")
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


def secret(name: str) -> str:
    """Zugangsdaten aus der Umgebung oder secrets.toml (nie aus config.toml)."""
    if os.environ.get(name):
        return os.environ[name]
    if SECRETS_PATH.exists():
        with SECRETS_PATH.open("rb") as f:
            value = tomllib.load(f).get(name)
        if value:
            return value
    raise Fail(f"Zugangsdatum {name} fehlt (Umgebungsvariable oder secrets.toml).")


def save_secret(name: str, value: str) -> None:
    lines = SECRETS_PATH.read_text().splitlines() if SECRETS_PATH.exists() else []
    lines = [l for l in lines if not l.startswith(f"{name} ")]
    lines.append(f"{name} = {json.dumps(value)}")
    SECRETS_PATH.write_text("\n".join(lines) + "\n")
    SECRETS_PATH.chmod(0o600)


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS clips (
            id TEXT PRIMARY KEY, streamer TEXT, title TEXT, url TEXT,
            views INTEGER, duration REAL, created_at TEXT, score REAL,
            status TEXT DEFAULT 'candidate', youtube_id TEXT,
            uploaded_at TEXT, first_seen TEXT)"""
    )
    return con


def http(url: str, *, method: str = "GET", headers: dict | None = None,
         data: bytes | None = None, form: dict | None = None) -> tuple[dict, dict]:
    """Kleiner JSON-HTTP-Helfer. Gibt (JSON-Koerper, Antwort-Header) zurueck."""
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
            return (json.loads(body) if body else {}), dict(resp.headers)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise Fail(f"{method} {url.split('?')[0]} -> HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise Fail(f"{url.split('?')[0]} nicht erreichbar: {e.reason}") from None


def allowed_streamers(cfg: dict) -> dict[str, dict]:
    """Nur Streamer mit Erlaubnis-Nachweis. Alle anderen werden uebersprungen."""
    ok = {}
    for s in cfg.get("streamer", []):
        login = s.get("login", "").lower()
        if not login:
            continue
        if not s.get("permission_proof", "").strip():
            print(f"uebersprungen: {login} (kein permission_proof eingetragen)", file=sys.stderr)
            continue
        ok[login] = s
    return ok


# ---------------------------------------------------------------- scan

def twitch_token() -> str:
    body, _ = http("https://id.twitch.tv/oauth2/token", method="POST", form={
        "client_id": secret("TWITCH_CLIENT_ID"),
        "client_secret": secret("TWITCH_CLIENT_SECRET"),
        "grant_type": "client_credentials",
    })
    return body["access_token"]


def helix(path: str, params: list[tuple[str, str]], token: str) -> dict:
    url = f"https://api.twitch.tv/helix/{path}?{urllib.parse.urlencode(params)}"
    body, _ = http(url, headers={
        "Authorization": f"Bearer {token}", "Client-Id": secret("TWITCH_CLIENT_ID")})
    return body


def score(views: int, age_hours: float) -> float:
    """Aufrufe pro Zeit: ein junger Clip mit vielen Aufrufen schlaegt einen alten."""
    return round(views / (age_hours + 2) ** 1.2, 2)


def cmd_scan(args) -> None:
    cfg = load_config()
    scan = cfg.get("scan", {})
    streamers = allowed_streamers(cfg)
    if not streamers:
        print("Kein Streamer mit permission_proof in config.toml, nichts zu tun.")
        return
    token = twitch_token()
    users = helix("users", [("login", l) for l in streamers], token)["data"]
    game_ids = {str(g) for g in scan.get("game_ids", [])}
    now = dt.datetime.now(UTC)
    since = (now - dt.timedelta(hours=scan.get("lookback_hours", 48)))
    con = db()
    for u in users:
        clips = helix("clips", [
            ("broadcaster_id", u["id"]), ("first", "100"),
            ("started_at", since.strftime("%Y-%m-%dT%H:%M:%SZ")),
            ("ended_at", now.strftime("%Y-%m-%dT%H:%M:%SZ")),
        ], token)["data"]
        for c in clips:
            if game_ids and c.get("game_id") not in game_ids:
                continue
            if not scan.get("min_seconds", 8) <= c["duration"] <= scan.get("max_seconds", 60):
                continue
            if c["view_count"] < scan.get("min_views", 50):
                continue
            created = dt.datetime.fromisoformat(c["created_at"].replace("Z", "+00:00"))
            age_h = (now - created).total_seconds() / 3600
            con.execute(
                """INSERT INTO clips (id, streamer, title, url, views, duration,
                                      created_at, score, first_seen)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET views=excluded.views, score=excluded.score""",
                (c["id"], u["login"], c["title"], c["url"], c["view_count"],
                 c["duration"], c["created_at"], score(c["view_count"], age_h),
                 now.isoformat()))
    con.commit()
    rows = con.execute(
        "SELECT id, streamer, title, url, views, duration, created_at, score "
        "FROM clips WHERE status='candidate' ORDER BY score DESC LIMIT ?",
        (args.top,)).fetchall()
    text = json.dumps([dict(r) for r in rows], indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)


# ---------------------------------------------------------------- fetch

def clip_row(con: sqlite3.Connection, clip_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    if not row:
        raise Fail(f"Clip {clip_id} ist nicht bekannt. Erst 'scan' ausfuehren.")
    if row["streamer"] not in allowed_streamers(load_config()):
        raise Fail(f"Fuer {row['streamer']} liegt keine Erlaubnis (mehr) vor.")
    return row


def cmd_fetch(args) -> None:
    con = db()
    row = clip_row(con, args.clip_id)
    WORK.mkdir(exist_ok=True)
    target = WORK / f"{row['id']}.mp4"
    if not target.exists():
        r = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--no-playlist", "-f", "best[ext=mp4]/best",
             "-o", str(target), row["url"]], capture_output=True, text=True)
        if r.returncode != 0 or not target.exists():
            raise Fail(f"Download fehlgeschlagen: {r.stderr.strip()[-400:]}")
    con.execute("UPDATE clips SET status='fetched' WHERE id=? AND status='candidate'", (row["id"],))
    con.commit()
    print(target)


# ---------------------------------------------------------------- render

def wrap(text: str, width: int) -> str:
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return "\n".join(lines + [line])


def render(src: Path, dst: Path, title: str, credit: str, font: str, max_seconds: int) -> None:
    """Hochkant 1080x1920: unscharfer Hintergrund, Bild mittig, Titel oben, Quelle unten."""
    with tempfile.TemporaryDirectory() as tmp:
        t_file, c_file = Path(tmp) / "title.txt", Path(tmp) / "credit.txt"
        t_file.write_text(wrap(title, 22), encoding="utf-8")
        c_file.write_text(credit, encoding="utf-8")
        graph = (
            # Hintergrund klein weichzeichnen und erst danach hochskalieren: viel schneller.
            "[0:v]fps=30,split=2[a][b];"
            "[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
            "boxblur=6:2,scale=1080:1920,eq=brightness=-0.12[bg];"
            "[b]scale=1080:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
            f"drawtext=expansion=none:fontfile='{font}':textfile='{t_file}':fontsize=76:fontcolor=white:"
            "borderw=6:bordercolor=black:line_spacing=14:x=(w-text_w)/2:y=230,"
            f"drawtext=expansion=none:fontfile='{font}':textfile='{c_file}':fontsize=44:fontcolor=white:"
            "borderw=4:bordercolor=black:x=(w-text_w)/2:y=h-330,"
            "format=yuv420p[v]"
        )
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-t", str(max_seconds),
             "-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst)],
            capture_output=True, text=True)
    if r.returncode != 0:
        raise Fail(f"ffmpeg fehlgeschlagen: {r.stderr.strip()[-600:]}")


def cmd_render(args) -> None:
    cfg = load_config()
    con = db()
    row = clip_row(con, args.clip_id)
    src = WORK / f"{row['id']}.mp4"
    if not src.exists():
        raise Fail("Clip ist noch nicht heruntergeladen. Erst 'fetch' ausfuehren.")
    OUT.mkdir(exist_ok=True)
    dst = OUT / f"{row['id']}.mp4"
    r_cfg = cfg.get("render", {})
    render(src, dst, args.title, f"twitch.tv/{row['streamer']}",
           r_cfg.get("font", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
           r_cfg.get("max_seconds", 59))
    con.execute("UPDATE clips SET status='rendered' WHERE id=?", (row["id"],))
    con.commit()
    print(dst)


# ---------------------------------------------------------------- YouTube

def youtube_access_token() -> str:
    body, _ = http("https://oauth2.googleapis.com/token", method="POST", form={
        "client_id": secret("YT_CLIENT_ID"), "client_secret": secret("YT_CLIENT_SECRET"),
        "refresh_token": secret("YT_REFRESH_TOKEN"), "grant_type": "refresh_token"})
    return body["access_token"]


def cmd_auth_youtube(args) -> None:
    """Geraete-Anmeldung: Code anzeigen, der Kanalinhaber bestaetigt im Browser."""
    cid, csec = secret("YT_CLIENT_ID"), secret("YT_CLIENT_SECRET")
    dev, _ = http("https://oauth2.googleapis.com/device/code", method="POST",
                  form={"client_id": cid, "scope": YT_SCOPE})
    print(f"Bitte {dev['verification_url']} oeffnen und diesen Code eingeben: {dev['user_code']}", flush=True)
    deadline = time.time() + dev["expires_in"]
    while time.time() < deadline:
        time.sleep(dev.get("interval", 5))
        try:
            tok, _ = http("https://oauth2.googleapis.com/token", method="POST", form={
                "client_id": cid, "client_secret": csec, "device_code": dev["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        except Fail as e:
            if "authorization_pending" in str(e) or "slow_down" in str(e):
                continue
            raise
        refresh = tok["refresh_token"]
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::add-mask::{refresh}")
        if args.out:
            Path(args.out).write_text(refresh + "\n")
            print(f"Angemeldet. Token steht in {args.out}.")
        else:
            save_secret("YT_REFRESH_TOKEN", refresh)
            print("Angemeldet. YT_REFRESH_TOKEN steht in secrets.toml.")
        return
    raise Fail("Anmeldung nicht rechtzeitig bestaetigt.")


def cmd_upload(args) -> None:
    cfg = load_config()
    up = cfg.get("upload", {})
    con = db()
    row = clip_row(con, args.clip_id)
    if row["youtube_id"]:
        raise Fail(f"Bereits hochgeladen: https://youtu.be/{row['youtube_id']}")
    video = OUT / f"{row['id']}.mp4"
    if not video.exists():
        raise Fail("Keine geschnittene Fassung. Erst 'render' ausfuehren.")
    today = dt.datetime.now(UTC).date().isoformat()
    done = con.execute("SELECT COUNT(*) FROM clips WHERE uploaded_at LIKE ?", (today + "%",)).fetchone()[0]
    if done >= up.get("max_per_day", 5):
        raise DailyLimit(f"Tageslimit erreicht ({done} Uploads heute).")

    credit = f"Clip von twitch.tv/{row['streamer']}, mit Erlaubnis.\nOriginal: {row['url']}"
    description = f"{args.description}\n\n{credit}".strip()
    meta = {
        "snippet": {"title": args.title[:100], "description": description,
                    "tags": args.tags, "categoryId": "20",
                    "defaultLanguage": up.get("language", "de")},
        "status": {"privacyStatus": up.get("privacy", "private"),
                   "selfDeclaredMadeForKids": False},
    }
    token = youtube_access_token()
    size = video.stat().st_size
    _, headers = http(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        method="POST", data=json.dumps(meta).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4",
                 "X-Upload-Content-Length": str(size)})
    session_url = headers.get("Location") or headers.get("location")
    if not session_url:
        raise Fail("YouTube hat keine Upload-Adresse geliefert.")
    result, _ = http(session_url, method="PUT", data=video.read_bytes(),
                     headers={"Authorization": f"Bearer {token}",
                              "Content-Type": "video/mp4", "Content-Length": str(size)})
    con.execute("UPDATE clips SET status='uploaded', youtube_id=?, uploaded_at=? WHERE id=?",
                (result["id"], dt.datetime.now(UTC).isoformat(), row["id"]))
    con.commit()
    print(json.dumps({"youtube_id": result["id"], "url": f"https://youtu.be/{result['id']}",
                      "privacy": result.get("status", {}).get("privacyStatus")}))


# ---------------------------------------------------------------- status

def cmd_skip(args) -> None:
    con = db()
    con.execute("UPDATE clips SET status='skipped' WHERE id=?", (args.clip_id,))
    con.commit()


QUEUE = ROOT / "queue"


def cmd_publish_queue(args) -> None:
    """Arbeitet queue/*.json ab. Jede Datei ist eine Entscheidung der steuernden Instanz:
    {"title": ..., "description": ..., "tags": [...]} oder {"skip": true}.
    Erledigte Dateien werden geloescht; bei Tageslimit bleibt der Rest liegen."""
    files = sorted(QUEUE.glob("*.json")) if QUEUE.exists() else []
    failed = 0
    for f in files:
        clip_id = f.stem
        try:
            job = json.loads(f.read_text(encoding="utf-8"))
            if job.get("skip"):
                cmd_skip(argparse.Namespace(clip_id=clip_id))
                print(f"{clip_id}: uebersprungen")
            else:
                title = job["title"]
                cmd_fetch(argparse.Namespace(clip_id=clip_id))
                cmd_render(argparse.Namespace(clip_id=clip_id, title=job.get("overlay", title)))
                cmd_upload(argparse.Namespace(clip_id=clip_id, title=title,
                                              description=job.get("description", ""),
                                              tags=job.get("tags", [])))
            f.unlink()
        except DailyLimit as e:
            print(f"{clip_id}: {e} Rest bleibt in der Warteschlange.")
            break
        except (Fail, KeyError, json.JSONDecodeError) as e:
            failed += 1
            print(f"{clip_id}: FEHLER {e}", file=sys.stderr)
            con = db()
            con.execute("UPDATE clips SET status='failed' WHERE id=?", (clip_id,))
            con.commit()
            f.unlink()
    if failed:
        sys.exit(f"{failed} Clip(s) fehlgeschlagen, siehe oben.")


def cmd_status(args) -> None:
    con = db()
    counts = {r["status"]: r["n"] for r in con.execute(
        "SELECT status, COUNT(*) n FROM clips GROUP BY status")}
    recent = [dict(r) for r in con.execute(
        "SELECT id, streamer, title, youtube_id, uploaded_at FROM clips "
        "WHERE status='uploaded' ORDER BY uploaded_at DESC LIMIT 10")]
    print(json.dumps({"counts": counts, "recent_uploads": recent}, indent=2, ensure_ascii=False))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("--top", type=int, default=10)
    s.add_argument("--out"); s.set_defaults(fn=cmd_scan)
    s = sub.add_parser("fetch"); s.add_argument("clip_id"); s.set_defaults(fn=cmd_fetch)
    s = sub.add_parser("render"); s.add_argument("clip_id"); s.add_argument("--title", required=True)
    s.set_defaults(fn=cmd_render)
    s = sub.add_parser("upload"); s.add_argument("clip_id"); s.add_argument("--title", required=True)
    s.add_argument("--description", default=""); s.add_argument("--tags", nargs="*", default=[])
    s.set_defaults(fn=cmd_upload)
    s = sub.add_parser("skip"); s.add_argument("clip_id"); s.set_defaults(fn=cmd_skip)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    s = sub.add_parser("auth-youtube"); s.add_argument("--out"); s.set_defaults(fn=cmd_auth_youtube)
    sub.add_parser("publish-queue").set_defaults(fn=cmd_publish_queue)
    args = p.parse_args()
    try:
        args.fn(args)
    except Fail as e:
        sys.exit(f"Fehler: {e}")


if __name__ == "__main__":
    main()
