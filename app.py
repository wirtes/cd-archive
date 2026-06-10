#!/usr/bin/env python3
import base64
import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import sqlite3
import sys
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse


ROOT = Path(__file__).resolve().parent
ENV_PATH = Path(os.environ.get("ENV_PATH", ROOT / ".env")).expanduser()


def preload_dotenv(path=ENV_PATH):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_path(name, default):
    return Path(os.environ.get(name, default)).expanduser()


preload_dotenv()

DB_PATH = env_path("DATABASE_PATH", ROOT / "data" / "cd_catalog.sqlite")
STATIC_DIR = ROOT / "web"
COVER_DIR = env_path("COVER_DIR", STATIC_DIR / "covers")
ARTIST_IMAGE_DIR = env_path("ARTIST_IMAGE_DIR", STATIC_DIR / "artist-images")
sys.path.insert(0, str(ROOT))
from scripts.build_database import (
    apple_track_rows,
    apply_apple_track_explicitness,
    cached_json,
    create_schema,
    discogs_cover_url,
    discogs_is_compilation,
    enrich_album_from_discogs_url,
    enrich_album_from_service_url,
    first_joined,
    is_various_artist,
    load_dotenv,
    value_from_mapping_or_string,
)


DEFAULT_PORT = 8190
DEFAULT_HOST = "0.0.0.0"
SESSION_COOKIE = "cd_archive_session"
LOGIN_PATH = "/login.html"
PUBLIC_PATHS = {
    LOGIN_PATH,
    "/login.css",
    "/login.js",
    "/api/login",
    "/images/1190-logo-reversed-300x180.png",
}


def normalize_format(value):
    cleaned = "".join(char for char in (value or "").casefold() if char.isalnum())
    aliases = {
        "cd": "cd",
        "cdaudio": "cd",
        "compactdisc": "cd",
        "compactdiscda": "cd",
        "vinyl": "vinyl",
        "lp": "vinyl",
        "12vinyl": "vinyl",
        "10vinyl": "vinyl",
        "7vinyl": "vinyl",
        "cassette": "cassette",
        "tape": "cassette",
        "digital": "digital",
        "file": "digital",
    }
    return aliases.get(cleaned, cleaned)


def split_format_values(value):
    if not value:
        return []
    return [part.strip() for part in str(value).replace(";", ",").split(",") if part.strip()]


def collect_discogs_formats(raw_json):
    if not raw_json:
        return []
    try:
        raw = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    formats = []
    candidates = [raw, raw.get("detail"), raw.get("selected")]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for item in candidate.get("formats") or []:
            if not isinstance(item, dict):
                continue
            formats.append(item.get("name"))
            formats.extend(item.get("descriptions") or [])
        format_value = candidate.get("format")
        if isinstance(format_value, list):
            formats.extend(format_value)
        elif format_value:
            formats.append(format_value)
    return [value for value in formats if value]


def api_formats_for_album(conn, album_id):
    values = []
    row = conn.execute("SELECT format FROM musicbrainz_metadata WHERE album_id = ?", (album_id,)).fetchone()
    if row:
        values.extend(split_format_values(row["format"]))
    values.extend(
        row["medium_format"]
        for row in conn.execute(
            "SELECT DISTINCT medium_format FROM tracks WHERE album_id = ? AND medium_format IS NOT NULL AND medium_format != ''",
            (album_id,),
        ).fetchall()
    )
    for row in conn.execute(
        """
        SELECT raw_json
        FROM external_metadata
        WHERE album_id = ? AND provider = 'discogs' AND lookup_status = 'matched'
        """,
        (album_id,),
    ).fetchall():
        values.extend(collect_discogs_formats(row["raw_json"]))

    output = []
    seen = set()
    for value in values:
        cleaned = str(value or "").strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            output.append(cleaned)
    return output


def format_matches_api(catalog_format, api_formats):
    normalized_catalog = normalize_format(catalog_format)
    if not normalized_catalog or not api_formats:
        return True
    return normalized_catalog in {normalize_format(value) for value in api_formats}


ALBUM_INSERT_COLUMNS = (
    "row_number",
    "timestamp",
    "catalog_number",
    "media_format",
    "artist",
    "album_name",
    "label",
    "format",
    "compilation",
    "country",
    "released",
    "genre",
    "field_sources",
    "version_number",
    "case_broken",
    "label_number_missing",
    "notes",
    "rateyourmusic",
    "other",
    "source_json",
)


def clean_text(value):
    return str(value or "").strip()


def normalize_artist_name(value):
    import re

    return re.sub(r"\s+", " ", re.sub(r"\s*\(\d+\)\s*$", "", value or "")).strip()


def is_various_artist(value):
    normalized = normalize_artist_name(value).casefold().replace(".", "")
    return normalized in {"v/a", "va", "various", "various artists"}


def discogs_release_url(payload):
    uri = clean_text(payload.get("uri"))
    if uri.startswith("http"):
        return uri
    discogs_id = payload.get("id")
    return f"https://www.discogs.com/release/{discogs_id}" if discogs_id else ""


def discogs_artist(payload):
    artist_names = [value_from_mapping_or_string(artist, "name") for artist in payload.get("artists") or []]
    artist = first_joined([name for name in artist_names if name])
    if discogs_is_compilation(payload) or is_various_artist(artist):
        return "Various Artists"
    return artist


def discogs_label(payload):
    labels = [value_from_mapping_or_string(label, "name") for label in payload.get("labels") or []]
    return first_joined([label for label in labels if label])


def discogs_barcodes(payload):
    values = []
    for identifier in payload.get("identifiers") or []:
        if not isinstance(identifier, dict):
            continue
        if str(identifier.get("type") or "").casefold() in {"barcode", "upc"}:
            value = clean_text(identifier.get("value"))
            if value:
                values.append(value)
    return values


def discogs_mobile_album(payload):
    artist = discogs_artist(payload)
    return {
        "timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "catalog_number": "",
        "artist": artist,
        "album_name": clean_text(payload.get("title")),
        "version_number": "",
        "case_broken": "No",
        "label_number_missing": "",
        "label": discogs_label(payload),
        "format": "CD",
        "compilation": discogs_is_compilation(payload) or is_various_artist(artist),
        "country": clean_text(payload.get("country")),
        "released": clean_text(payload.get("released")),
        "genre": ", ".join([item for item in (payload.get("genres") or []) if item]),
        "notes": "",
        "other": "",
    }


def utc_now():
    return dt.datetime.now(dt.UTC).isoformat()


def password_hash(password):
    return hashlib.sha256(str(password or "").encode("utf-8")).hexdigest()


def ensure_database_schema():
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                is_editor INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_events (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_events_user_id ON scan_events(username, id)")
        load_dotenv()
        default_user = os.environ.get("APP_USERNAME", "admin")
        default_password = os.environ.get("APP_PASSWORD", "radio1190")
        if default_user and default_password:
            conn.execute(
                """
                INSERT INTO users (username, password_hash, is_admin, is_editor, created_at)
                VALUES (?, ?, 1, 1, ?)
                ON CONFLICT(username) DO UPDATE SET
                    password_hash = excluded.password_hash,
                    is_admin = CASE WHEN users.is_admin = 1 THEN 1 ELSE excluded.is_admin END,
                    is_editor = CASE WHEN users.is_editor = 1 THEN 1 ELSE excluded.is_editor END
                """,
                (default_user, password_hash(default_password), utc_now()),
            )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(albums)").fetchall()}
        if "compilation" not in columns:
            conn.execute("ALTER TABLE albums ADD COLUMN compilation INTEGER NOT NULL DEFAULT 0")
        track_columns = {row[1] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()}
        if "explicit" not in track_columns:
            conn.execute("ALTER TABLE tracks ADD COLUMN explicit INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            """
            UPDATE albums
            SET format = COALESCE(NULLIF(TRIM(format), ''), NULLIF(TRIM(media_format), ''), 'CD'),
                media_format = COALESCE(NULLIF(TRIM(format), ''), NULLIF(TRIM(media_format), ''), 'CD')
            """
        )
        conn.execute(
            """
            UPDATE albums
            SET artist = 'Various Artists',
                compilation = 1
            WHERE LOWER(REPLACE(TRIM(artist), '.', '')) IN ('v/a', 'va', 'various', 'various artists')
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_album_bundle(conn, album_id):
    album = conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
    if not album:
        return None
    metadata = conn.execute("SELECT * FROM musicbrainz_metadata WHERE album_id = ?", (album_id,)).fetchone()
    tracks = conn.execute(
        """
        SELECT medium_position, medium_title, medium_format, track_position,
               track_number, title, length_ms, explicit, recording_id
        FROM tracks
        WHERE album_id = ?
        ORDER BY medium_position, track_position, id
        """,
        (album_id,),
    ).fetchall()
    genres = conn.execute(
        """
        SELECT source, name, count
        FROM album_genres
        WHERE album_id = ?
        ORDER BY source, COALESCE(count, 0) DESC, name
        """,
        (album_id,),
    ).fetchall()
    cover_art = conn.execute(
        """
        SELECT source, image_id, types, is_front, is_back, approved,
               image_url, thumbnail_small, thumbnail_large, local_image_url, comment
        FROM cover_art
        WHERE album_id = ?
        ORDER BY is_front DESC, approved DESC, id
        """,
        (album_id,),
    ).fetchall()
    external = conn.execute(
        """
        SELECT provider, lookup_status, lookup_error, fetched_at, external_id,
               url, title, artist, genres, styles, track_count, cover_url
        FROM external_metadata
        WHERE album_id = ?
        ORDER BY CASE provider
            WHEN 'apple_itunes' THEN 1
            WHEN 'discogs' THEN 2
            WHEN 'lastfm' THEN 3
            WHEN 'musicbrainz' THEN 4
            ELSE 5
        END, provider
        """,
        (album_id,),
    ).fetchall()
    services = conn.execute(
        """
        SELECT provider, lookup_status, found, fetched_at, external_id,
               title, url, lookup_error
        FROM album_service_status
        WHERE album_id = ?
        ORDER BY CASE provider
            WHEN 'apple_itunes' THEN 1
            WHEN 'discogs' THEN 2
            WHEN 'lastfm' THEN 3
            WHEN 'musicbrainz' THEN 4
            ELSE 5
        END, provider
        """,
        (album_id,),
    ).fetchall()
    artist_info = None
    if not album["compilation"] and not is_various_artist(album["artist"]):
        artist_info = conn.execute(
            """
            SELECT name, lookup_status, lookup_error, fetched_at, lastfm_mbid,
                   lastfm_url, bio_summary, bio_content, image_url, local_image_url
            FROM artists
            WHERE name = ?
            """,
            (album["artist"],),
        ).fetchone()
    return {
        "album": dict(album),
        "artist": dict(artist_info) if artist_info else None,
        "musicbrainz": dict(metadata) if metadata else None,
        "tracks": [dict(row) for row in tracks],
        "genres": [dict(row) for row in genres],
        "cover_art": [dict(row) for row in cover_art],
        "external": [dict(row) for row in external],
        "services": [dict(row) for row in services],
    }


class CatalogHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def send_persistent_file(self, root, url_prefix, parsed):
        relative = unquote(parsed.path.removeprefix(url_prefix)).lstrip("/")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return True
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return True
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        data = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True

    def session_cookie(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                return value
        return ""

    def token_hash(self, token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def authenticated_user(self):
        token = self.session_cookie()
        if not token:
            return None
        with self.db() as conn:
            row = conn.execute(
                """
                SELECT auth_sessions.username, users.is_admin, users.is_editor
                FROM auth_sessions
                JOIN users ON users.username = auth_sessions.username
                WHERE auth_sessions.token_hash = ?
                """,
                (self.token_hash(token),),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE auth_sessions SET last_seen = ? WHERE token_hash = ?",
                (utc_now(), self.token_hash(token)),
            )
            conn.commit()
            return {"username": row["username"], "is_admin": bool(row["is_admin"]), "is_editor": bool(row["is_editor"])}

    def is_public_path(self, parsed):
        return parsed.path in PUBLIC_PATHS

    def require_auth(self, parsed):
        user = self.authenticated_user()
        if user:
            self.current_user = user["username"]
            self.current_roles = {"admin": user["is_admin"], "editor": user["is_editor"]}
            return True
        if parsed.path.startswith("/api/"):
            self.send_json({"error": "Authentication required."}, HTTPStatus.UNAUTHORIZED)
        else:
            target = quote(self.path, safe="")
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", f"{LOGIN_PATH}?next={target}")
            self.end_headers()
        return False

    def require_admin(self):
        if getattr(self, "current_roles", {}).get("admin"):
            return True
        self.send_json({"error": "Administrator role required."}, HTTPStatus.FORBIDDEN)
        return False

    def require_editor(self):
        roles = getattr(self, "current_roles", {})
        if roles.get("admin") or roles.get("editor"):
            return True
        self.send_json({"error": "Editor role required."}, HTTPStatus.FORBIDDEN)
        return False

    def send_no_content(self, status=HTTPStatus.NO_CONTENT):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if not self.is_public_path(parsed) and not self.require_auth(parsed):
            return
        if parsed.path.startswith("/covers/"):
            self.send_persistent_file(COVER_DIR, "/covers/", parsed)
            return
        if parsed.path.startswith("/artist-images/"):
            self.send_persistent_file(ARTIST_IMAGE_DIR, "/artist-images/", parsed)
            return
        if parsed.path == "/api/albums":
            self.handle_albums(parsed)
        elif parsed.path.startswith("/api/albums/"):
            self.handle_album_detail(parsed)
        elif parsed.path == "/api/stats":
            self.handle_stats()
        elif parsed.path == "/api/tags":
            self.handle_tags()
        elif parsed.path == "/api/session":
            self.send_json({"username": self.current_user, "roles": getattr(self, "current_roles", {})})
        elif parsed.path == "/api/scan-events":
            self.handle_scan_events(parsed)
        elif parsed.path == "/api/users":
            self.handle_users()
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/login":
            self.handle_login()
            return
        if not self.is_public_path(parsed) and not self.require_auth(parsed):
            return
        if parsed.path == "/api/logout":
            self.handle_logout()
        elif parsed.path == "/api/music-service-preview":
            self.handle_music_service_preview()
        elif parsed.path == "/api/music-service-match-preview":
            self.handle_music_service_match_preview()
        elif parsed.path == "/api/discogs-barcode-preview":
            self.handle_discogs_barcode_preview()
        elif parsed.path == "/api/scan-events":
            self.handle_create_scan_event()
        elif parsed.path == "/api/users":
            self.handle_create_user()
        elif parsed.path == "/api/albums":
            self.handle_create_album()
        elif parsed.path.startswith("/api/albums/") and parsed.path.endswith("/music-service-url"):
            self.handle_music_service_url(parsed)
        else:
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_PUT(self):
        parsed = urlparse(self.path)
        if not self.require_auth(parsed):
            return
        if parsed.path.startswith("/api/albums/"):
            self.handle_update_album(parsed)
        else:
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if not self.require_auth(parsed):
            return
        if parsed.path.startswith("/api/albums/"):
            self.handle_delete_album(parsed)
        else:
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def db(self):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def configured_credentials(self):
        load_dotenv()
        return (
            os.environ.get("APP_USERNAME", "admin"),
            os.environ.get("APP_PASSWORD", "radio1190"),
        )

    def handle_login(self):
        try:
            payload = self.read_json_body()
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid request."}, HTTPStatus.BAD_REQUEST)
            return
        username = clean_text(payload.get("username"))
        password = str(payload.get("password") or "")
        with self.db() as conn:
            row = conn.execute(
                "SELECT username, password_hash, is_admin, is_editor FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if not row or not hmac.compare_digest(row["password_hash"], password_hash(password)):
                self.send_json({"error": "Invalid username or password."}, HTTPStatus.UNAUTHORIZED)
                return
            token = secrets.token_urlsafe(32)
            conn.execute(
                """
                INSERT INTO auth_sessions (token_hash, username, created_at, last_seen)
                VALUES (?, ?, ?, ?)
                """,
                (self.token_hash(token), username, utc_now(), utc_now()),
            )
            conn.commit()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")
        body = json.dumps({"username": username, "roles": {"admin": bool(row["is_admin"]), "editor": bool(row["is_editor"])}}, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_logout(self):
        token = self.session_cookie()
        if token:
            with self.db() as conn:
                conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (self.token_hash(token),))
                conn.commit()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
        body = b'{"ok": true}'
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_create_scan_event(self):
        try:
            payload = self.read_json_body()
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid request."}, HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(payload.get("release"), dict):
            self.send_json({"error": "Release payload is required."}, HTTPStatus.BAD_REQUEST)
            return
        event_payload = {
            "release": payload["release"],
            "source": clean_text(payload.get("source")) or "mobile",
        }
        with self.db() as conn:
            cursor = conn.execute(
                "INSERT INTO scan_events (username, created_at, payload_json) VALUES (?, ?, ?)",
                (self.current_user, utc_now(), json.dumps(event_payload, ensure_ascii=False)),
            )
            conn.commit()
            event_id = cursor.lastrowid
        self.send_json({"id": event_id})

    def handle_scan_events(self, parsed):
        params = parse_qs(parsed.query)
        if params.get("latest", ["0"])[0] in {"1", "true", "yes"}:
            with self.db() as conn:
                row = conn.execute(
                    "SELECT COALESCE(MAX(id), 0) AS latest_id FROM scan_events WHERE username = ?",
                    (self.current_user,),
                ).fetchone()
            self.send_json({"latest_id": row["latest_id"] if row else 0, "events": []})
            return
        after = int(params.get("after", ["0"])[0] or 0)
        with self.db() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, payload_json
                FROM scan_events
                WHERE username = ? AND id > ?
                ORDER BY id
                LIMIT 10
                """,
                (self.current_user, after),
            ).fetchall()
        events = []
        for row in rows:
            events.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    **json.loads(row["payload_json"]),
                }
            )
        self.send_json({"events": events})

    def handle_users(self):
        if not self.require_admin():
            return
        with self.db() as conn:
            rows = conn.execute(
                "SELECT username, is_admin, is_editor, created_at FROM users ORDER BY username"
            ).fetchall()
        self.send_json(
            {
                "users": [
                    {
                        "username": row["username"],
                        "is_admin": bool(row["is_admin"]),
                        "is_editor": bool(row["is_editor"]),
                        "created_at": row["created_at"],
                    }
                    for row in rows
                ]
            }
        )

    def handle_create_user(self):
        if not self.require_admin():
            return
        try:
            payload = self.read_json_body()
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid request."}, HTTPStatus.BAD_REQUEST)
            return
        username = clean_text(payload.get("username"))
        password = str(payload.get("password") or "")
        is_admin = 1 if payload.get("is_admin") else 0
        is_editor = 1 if payload.get("is_editor") else 0
        if not username or not password:
            self.send_json({"error": "Username and password are required."}, HTTPStatus.BAD_REQUEST)
            return
        with self.db() as conn:
            conn.execute(
                """
                INSERT INTO users (username, password_hash, is_admin, is_editor, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    password_hash = excluded.password_hash,
                    is_admin = excluded.is_admin,
                    is_editor = excluded.is_editor
                """,
                (username, password_hash(password), is_admin, is_editor, utc_now()),
            )
            conn.commit()
        self.send_json({"username": username, "is_admin": bool(is_admin), "is_editor": bool(is_editor)}, HTTPStatus.CREATED)

    def handle_albums(self, parsed):
        params = parse_qs(parsed.query)
        q = (params.get("q", [""])[0] or "").strip()
        tag = (params.get("tag", [""])[0] or "").strip()
        artist = (params.get("artist", [""])[0] or "").strip()
        label = (params.get("label", [""])[0] or "").strip()
        hide_na = (params.get("hide_na", ["0"])[0] or "0") == "1"
        search_tracks = (params.get("search_tracks", ["0"])[0] or "0") == "1"
        enriched = params.get("enriched", ["all"])[0]
        limit = min(max(int(params.get("limit", ["50"])[0] or 50), 1), 200)
        offset = max(int(params.get("offset", ["0"])[0] or 0), 0)

        where = []
        values = []
        if q:
            search_parts = [
                """
                albums.artist LIKE ? OR albums.album_name LIKE ? OR albums.catalog_number LIKE ?
                OR albums.label LIKE ? OR albums.format LIKE ? OR albums.country LIKE ?
                OR albums.released LIKE ? OR albums.genre LIKE ?
                OR EXISTS (
                    SELECT 1
                    FROM external_metadata
                    WHERE external_metadata.album_id = albums.id
                      AND (external_metadata.title LIKE ? OR external_metadata.artist LIKE ?)
                )
                """
            ]
            needle = f"%{q}%"
            search_values = [needle, needle, needle, needle, needle, needle, needle, needle, needle, needle]
            if search_tracks:
                search_parts.append(
                    """
                    EXISTS (
                        SELECT 1
                        FROM tracks
                        WHERE tracks.album_id = albums.id
                          AND tracks.title LIKE ?
                    )
                    """
                )
                search_values.append(needle)
            where.append(
                f"""
                (
                    {" OR ".join(search_parts)}
                )
                """
            )
            values.extend(search_values)
        if tag:
            where.append(
                """
                (
                    EXISTS (
                        SELECT 1
                        FROM album_genres
                        WHERE album_genres.album_id = albums.id
                          AND LOWER(TRIM(album_genres.name)) = LOWER(TRIM(?))
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM external_metadata, json_each(external_metadata.genres)
                        WHERE external_metadata.album_id = albums.id
                          AND json_valid(external_metadata.genres)
                          AND LOWER(TRIM(json_each.value)) = LOWER(TRIM(?))
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM external_metadata, json_each(external_metadata.styles)
                        WHERE external_metadata.album_id = albums.id
                          AND json_valid(external_metadata.styles)
                          AND LOWER(TRIM(json_each.value)) = LOWER(TRIM(?))
                    )
                )
                """
            )
            values.extend([tag, tag, tag])
        if artist:
            where.append(
                """
                (
                    albums.artist = ?
                    OR EXISTS (
                        SELECT 1
                        FROM external_metadata
                        WHERE external_metadata.album_id = albums.id
                          AND external_metadata.artist = ?
                    )
                )
                """
            )
            values.extend([artist, artist])
        if label:
            where.append("albums.label = ?")
            values.append(label)
        if hide_na:
            where.append("NOT (LOWER(TRIM(albums.artist)) = 'n/a' AND LOWER(TRIM(albums.album_name)) = 'n/a')")
        if enriched == "yes":
            where.append("EXISTS (SELECT 1 FROM album_service_status WHERE album_service_status.album_id = albums.id)")
        elif enriched == "no":
            where.append("NOT EXISTS (SELECT 1 FROM album_service_status WHERE album_service_status.album_id = albums.id)")

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with self.db() as conn:
            total = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM albums
                {where_sql}
                """,
                values,
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT
                    albums.id, albums.row_number, albums.catalog_number, albums.media_format, albums.artist,
                    albums.album_name, albums.label, albums.format, albums.country, albums.released,
                    albums.genre, albums.field_sources, albums.case_broken,
                    (
                        SELECT GROUP_CONCAT(provider, ', ')
                        FROM (
                            SELECT provider
                            FROM album_service_status
                            WHERE album_service_status.album_id = albums.id
                              AND album_service_status.found = 1
                            ORDER BY CASE provider
                                WHEN 'apple_itunes' THEN 1
                                WHEN 'discogs' THEN 2
                                WHEN 'lastfm' THEN 3
                                WHEN 'musicbrainz' THEN 4
                                ELSE 5
                            END, provider
                        )
                    ) AS matched_services
                FROM albums
                {where_sql}
                ORDER BY albums.row_number
                LIMIT ? OFFSET ?
                """,
                values + [limit, offset],
            ).fetchall()
            albums = []
            for row in rows:
                album = dict(row)
                api_formats = api_formats_for_album(conn, album["id"])
                album["api_formats"] = api_formats
                album["format_matches_api"] = format_matches_api(album.get("format") or album.get("media_format"), api_formats)
                albums.append(album)
        self.send_json({"total": total, "limit": limit, "offset": offset, "albums": albums})

    def handle_album_detail(self, parsed):
        try:
            album_id = int(parsed.path.rsplit("/", 1)[1])
        except ValueError:
            self.send_json({"error": "Invalid album id"}, HTTPStatus.BAD_REQUEST)
            return

        with self.db() as conn:
            payload = get_album_bundle(conn, album_id)
            if not payload:
                self.send_json({"error": "Album not found"}, HTTPStatus.NOT_FOUND)
                return
        self.send_json(payload)

    def album_insert_values(self, row_number, form):
        format_value = clean_text(form.get("format")) or "CD"
        source_json = {
            "added_via": "web_form",
            "timestamp": clean_text(form.get("timestamp")),
            "catalog_number": clean_text(form.get("catalog_number")),
            "artist": clean_text(form.get("artist")),
            "album_name": clean_text(form.get("album_name")),
            "version_number": clean_text(form.get("version_number")),
            "case_broken": clean_text(form.get("case_broken")),
            "label_number_missing": clean_text(form.get("label_number_missing")),
            "notes": clean_text(form.get("notes")),
            "rateyourmusic": clean_text(form.get("rateyourmusic")),
            "other": clean_text(form.get("other")),
        }
        compilation = 1 if form.get("compilation") in (True, "true", "1", "on", 1) else 0
        return (
            row_number,
            clean_text(form.get("timestamp")) or utc_now(),
            clean_text(form.get("catalog_number")),
            format_value,
            clean_text(form.get("artist")),
            clean_text(form.get("album_name")),
            clean_text(form.get("label")),
            format_value,
            compilation,
            clean_text(form.get("country")),
            clean_text(form.get("released")),
            clean_text(form.get("genre")),
            json.dumps({}, ensure_ascii=False),
            clean_text(form.get("version_number")),
            clean_text(form.get("case_broken")),
            clean_text(form.get("label_number_missing")),
            clean_text(form.get("notes")),
            clean_text(form.get("rateyourmusic")),
            clean_text(form.get("other")),
            json.dumps(source_json, ensure_ascii=False),
        )

    def insert_album(self, conn, form, row_number=None):
        if row_number is None:
            row_number = conn.execute("SELECT COALESCE(MAX(row_number), 0) + 1 FROM albums").fetchone()[0]
        placeholders = ", ".join("?" for _ in ALBUM_INSERT_COLUMNS)
        conn.execute(
            f"INSERT INTO albums ({', '.join(ALBUM_INSERT_COLUMNS)}) VALUES ({placeholders})",
            self.album_insert_values(row_number, form),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def apply_album_identity(self, conn, album_id, artist, album_name):
        row = conn.execute("SELECT artist, album_name, compilation FROM albums WHERE id = ?", (album_id,)).fetchone()
        if not row:
            return
        existing_artist = clean_text(row["artist"])
        if is_various_artist(existing_artist) or is_various_artist(artist):
            resolved_artist = "Various Artists"
        else:
            resolved_artist = existing_artist or clean_text(artist)
        resolved_album = clean_text(row["album_name"]) or clean_text(album_name)
        compilation = 1 if row["compilation"] or is_various_artist(resolved_artist) else 0
        if resolved_artist != clean_text(row["artist"]) or resolved_album != clean_text(row["album_name"]) or compilation != row["compilation"]:
            conn.execute(
                "UPDATE albums SET artist = ?, album_name = ?, compilation = ? WHERE id = ?",
                (resolved_artist, resolved_album, compilation, album_id),
            )

    def handle_music_service_preview(self):
        try:
            payload = self.read_json_body()
            service_url = clean_text(payload.get("url"))
            if not service_url:
                self.send_json({"error": "Load from Music Service URL is required."}, HTTPStatus.BAD_REQUEST)
                return
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid request."}, HTTPStatus.BAD_REQUEST)
            return

        load_dotenv()
        try:
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            create_schema(conn)
            album_id = self.insert_album(
                conn,
                {
                    "artist": payload.get("artist"),
                    "album_name": payload.get("album_name"),
                    "format": payload.get("format") or "CD",
                    "compilation": payload.get("compilation"),
                },
                row_number=1,
            )
            result = enrich_album_from_discogs_url(conn, album_id, service_url, refresh_cache=False, include_related=False)
            self.apply_album_identity(conn, album_id, result.get("artist"), result.get("album_name"))
            bundle = get_album_bundle(conn, album_id)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        finally:
            try:
                conn.close()
            except UnboundLocalError:
                pass
        self.send_json({"result": result, **(bundle or {})})

    def handle_music_service_match_preview(self):
        try:
            payload = self.read_json_body()
            service_url = clean_text(payload.get("url"))
            if not service_url:
                self.send_json({"error": "Match to this Album URL is required."}, HTTPStatus.BAD_REQUEST)
                return
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid request."}, HTTPStatus.BAD_REQUEST)
            return

        load_dotenv()
        try:
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            create_schema(conn)
            album_id = self.insert_album(conn, payload.get("album") or {}, row_number=1)
            result = enrich_album_from_service_url(conn, album_id, service_url, refresh_cache=False)
            self.apply_album_identity(conn, album_id, result.get("artist"), result.get("album_name"))
            bundle = get_album_bundle(conn, album_id)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        finally:
            try:
                conn.close()
            except UnboundLocalError:
                pass
        self.send_json({"result": result, **(bundle or {})})

    def handle_discogs_barcode_preview(self):
        try:
            payload = self.read_json_body()
            barcode = "".join(char for char in clean_text(payload.get("barcode")) if char.isdigit())
            if not barcode:
                self.send_json({"error": "UPC barcode is required."}, HTTPStatus.BAD_REQUEST)
                return
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid request."}, HTTPStatus.BAD_REQUEST)
            return

        load_dotenv()
        token = os.environ.get("DISCOGS_TOKEN", "").strip()
        if not token:
            self.send_json({"error": "Set DISCOGS_TOKEN to search Discogs by barcode."}, HTTPStatus.BAD_REQUEST)
            return

        headers = {"Authorization": f"Discogs token={token}"}
        search_url = f"https://api.discogs.com/database/search?{urlencode({'barcode': barcode, 'type': 'release', 'per_page': '5'})}"
        try:
            with self.db() as conn:
                search_payload, _, search_error = cached_json(
                    conn,
                    "discogs",
                    f"barcode-search:{barcode}",
                    search_url,
                    headers=headers,
                    refresh_cache=False,
                )
                results = (search_payload or {}).get("results") or []
                selected = next((item for item in results if item.get("type") == "release" and item.get("id")), None) or next(
                    (item for item in results if item.get("id")),
                    None,
                )
                if search_error or not selected:
                    self.send_json({"error": search_error or f"No Discogs release found for UPC {barcode}."}, HTTPStatus.NOT_FOUND)
                    return
                release_id = selected.get("id")
                detail_url = f"https://api.discogs.com/releases/{release_id}"
                detail_payload, _, detail_error = cached_json(
                    conn,
                    "discogs",
                    f"release:{release_id}",
                    detail_url,
                    headers=headers,
                    refresh_cache=False,
                )
                if detail_error or not detail_payload:
                    self.send_json({"error": detail_error or "Discogs release detail was not found."}, HTTPStatus.NOT_FOUND)
                    return
                conn.commit()
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        cover_url = discogs_cover_url(detail_payload, selected)
        self.send_json(
            {
                "barcode": barcode,
                "release_url": discogs_release_url(detail_payload),
                "cover_url": cover_url,
                "track_count": len(detail_payload.get("tracklist") or []),
                "barcodes": discogs_barcodes(detail_payload),
                "album": discogs_mobile_album(detail_payload),
                "discogs": {
                    "id": detail_payload.get("id"),
                    "title": detail_payload.get("title"),
                    "artists_sort": detail_payload.get("artists_sort"),
                    "uri": detail_payload.get("uri"),
                },
            }
        )

    def update_album_fields(self, conn, album_id, form):
        format_value = clean_text(form.get("format")) or "CD"
        compilation = 1 if form.get("compilation") in (True, "true", "1", "on", 1) else 0
        conn.execute(
            """
            UPDATE albums
            SET timestamp = ?, catalog_number = ?, media_format = ?, artist = ?, album_name = ?,
                label = ?, format = ?, compilation = ?, country = ?, released = ?, genre = ?,
                version_number = ?, case_broken = ?, label_number_missing = ?, notes = ?,
                rateyourmusic = ?, other = ?
            WHERE id = ?
            """,
            (
                clean_text(form.get("timestamp")) or utc_now(),
                clean_text(form.get("catalog_number")),
                format_value,
                clean_text(form.get("artist")),
                clean_text(form.get("album_name")),
                clean_text(form.get("label")),
                format_value,
                compilation,
                clean_text(form.get("country")),
                clean_text(form.get("released")),
                clean_text(form.get("genre")),
                clean_text(form.get("version_number")),
                clean_text(form.get("case_broken")),
                clean_text(form.get("label_number_missing")),
                clean_text(form.get("notes")),
                clean_text(form.get("rateyourmusic")),
                clean_text(form.get("other")),
                album_id,
            ),
        )

    def save_uploaded_cover(self, conn, album_id, cover_data_url):
        if not cover_data_url:
            return
        header, _, encoded = cover_data_url.partition(",")
        if not encoded or "base64" not in header:
            raise ValueError("Cover image upload is invalid.")
        mime = header.split(";", 1)[0].replace("data:", "").strip().lower()
        extensions = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
        extension = extensions.get(mime)
        if not extension:
            raise ValueError("Cover image must be a JPEG, PNG, or WebP file.")
        image_bytes = base64.b64decode(encoded, validate=True)
        if len(image_bytes) > 8 * 1024 * 1024:
            raise ValueError("Cover image must be smaller than 8 MB.")
        COVER_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"user-{album_id}.{extension}"
        path = COVER_DIR / filename
        path.write_bytes(image_bytes)
        local_url = f"/covers/{filename}"
        conn.execute(
            "DELETE FROM cover_art WHERE album_id = ? AND source = 'user_upload'",
            (album_id,),
        )
        conn.execute(
            """
            INSERT INTO cover_art (
                album_id, source, image_id, types, is_front, is_back, approved,
                image_url, thumbnail_small, thumbnail_large, local_image_url, comment, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                album_id,
                "user_upload",
                filename,
                json.dumps(["Front"], ensure_ascii=False),
                1,
                0,
                1,
                local_url,
                local_url,
                local_url,
                local_url,
                "Uploaded from Add Album form",
                json.dumps({"mime": mime}, ensure_ascii=False),
            ),
        )

    def parse_tracks_payload(self, payload):
        rows = []
        for index, item in enumerate(payload if isinstance(payload, list) else [], start=1):
            title = clean_text((item or {}).get("title"))
            track_number = clean_text((item or {}).get("track_number")) or str(index)
            if not title:
                continue
            rows.append(
                {
                    "medium_position": 1,
                    "medium_title": "",
                    "medium_format": "",
                    "track_position": index,
                    "track_number": track_number,
                    "title": title,
                    "length_ms": None,
                    "explicit": 1 if (item or {}).get("explicit") in (True, "true", "1", "on", 1) else 0,
                    "recording_id": clean_text((item or {}).get("recording_id")) or f"user:{index}",
                }
            )
        return rows

    def replace_album_tracks(self, conn, album_id, tracks):
        rows = self.parse_tracks_payload(tracks)
        conn.execute("DELETE FROM tracks WHERE album_id = ?", (album_id,))
        if not rows:
            return
        conn.executemany(
            """
            INSERT INTO tracks (
                album_id, medium_position, medium_title, medium_format, track_position,
                track_number, title, length_ms, explicit, recording_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    album_id,
                    row["medium_position"],
                    row["medium_title"],
                    row["medium_format"],
                    row["track_position"],
                    row["track_number"],
                    row["title"],
                    row["length_ms"],
                    row["explicit"],
                    row["recording_id"],
                )
                for row in rows
            ],
        )

    def apply_cached_apple_explicitness(self, conn, album_id, replace_existing=False):
        row = conn.execute(
            """
            SELECT raw_json
            FROM external_metadata
            WHERE album_id = ? AND provider = 'apple_itunes' AND lookup_status = 'matched'
            """,
            (album_id,),
        ).fetchone()
        if not row or not row["raw_json"]:
            return 0
        try:
            payload = json.loads(row["raw_json"])
        except json.JSONDecodeError:
            return 0
        return apply_apple_track_explicitness(
            conn,
            album_id,
            apple_track_rows(payload.get("lookup") or {}),
            replace_if_empty=replace_existing,
            replace_existing=replace_existing,
        )

    def handle_create_album(self):
        if not self.require_editor():
            return
        try:
            payload = self.read_json_body()
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid request."}, HTTPStatus.BAD_REQUEST)
            return
        form = payload.get("album") or {}
        service_url = clean_text(payload.get("music_service_url"))
        if not clean_text(form.get("catalog_number")):
            self.send_json({"error": "1190_ID is required."}, HTTPStatus.BAD_REQUEST)
            return
        if not clean_text(form.get("artist")) and not clean_text(form.get("album_name")) and not service_url:
            self.send_json({"error": "Artist, album name, or Music Service URL is required."}, HTTPStatus.BAD_REQUEST)
            return

        load_dotenv()
        try:
            with self.db() as conn:
                album_id = self.insert_album(conn, form)
                if service_url:
                    result = enrich_album_from_discogs_url(conn, album_id, service_url, refresh_cache=False)
                    self.apply_album_identity(conn, album_id, result.get("artist"), result.get("album_name"))
                if "tracks" in payload:
                    self.replace_album_tracks(conn, album_id, payload.get("tracks"))
                    self.apply_cached_apple_explicitness(conn, album_id, replace_existing=bool(service_url))
                self.save_uploaded_cover(conn, album_id, payload.get("cover_data_url"))
                conn.commit()
                bundle = get_album_bundle(conn, album_id)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_json({"album_id": album_id, **(bundle or {})}, HTTPStatus.CREATED)

    def parse_album_id(self, parsed):
        try:
            return int(parsed.path.rsplit("/", 1)[1])
        except ValueError:
            return None

    def handle_update_album(self, parsed):
        if not self.require_editor():
            return
        album_id = self.parse_album_id(parsed)
        if album_id is None:
            self.send_json({"error": "Invalid album id"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = self.read_json_body()
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid request."}, HTTPStatus.BAD_REQUEST)
            return
        form = payload.get("album") or {}
        service_url = clean_text(payload.get("music_service_url"))
        if not clean_text(form.get("catalog_number")):
            self.send_json({"error": "1190_ID is required."}, HTTPStatus.BAD_REQUEST)
            return
        try:
            if service_url:
                load_dotenv()
            with self.db() as conn:
                if not conn.execute("SELECT 1 FROM albums WHERE id = ?", (album_id,)).fetchone():
                    self.send_json({"error": "Album not found"}, HTTPStatus.NOT_FOUND)
                    return
                self.update_album_fields(conn, album_id, form)
                if service_url:
                    result = enrich_album_from_service_url(conn, album_id, service_url, refresh_cache=False)
                    self.apply_album_identity(conn, album_id, result.get("artist"), result.get("album_name"))
                if "tracks" in payload:
                    self.replace_album_tracks(conn, album_id, payload.get("tracks"))
                    self.apply_cached_apple_explicitness(conn, album_id, replace_existing=bool(service_url))
                self.save_uploaded_cover(conn, album_id, payload.get("cover_data_url"))
                conn.commit()
                bundle = get_album_bundle(conn, album_id)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_json({"album_id": album_id, **(bundle or {})})

    def handle_delete_album(self, parsed):
        if not self.require_editor():
            return
        album_id = self.parse_album_id(parsed)
        if album_id is None:
            self.send_json({"error": "Invalid album id"}, HTTPStatus.BAD_REQUEST)
            return
        with self.db() as conn:
            cursor = conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
            conn.commit()
        if cursor.rowcount == 0:
            self.send_json({"error": "Album not found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_json({"deleted": True, "album_id": album_id})

    def handle_music_service_url(self, parsed):
        if not self.require_editor():
            return
        try:
            album_id = int(parsed.path.split("/")[3])
            payload = self.read_json_body()
            service_url = (payload.get("url") or "").strip()
            if not service_url:
                self.send_json({"error": "Music Service URL is required."}, HTTPStatus.BAD_REQUEST)
                return
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "Invalid request."}, HTTPStatus.BAD_REQUEST)
            return

        load_dotenv()
        try:
            with self.db() as conn:
                result = enrich_album_from_service_url(conn, album_id, service_url, refresh_cache=False)
                self.apply_album_identity(conn, album_id, result.get("artist"), result.get("album_name"))
                conn.commit()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_json(result)

    def handle_stats(self):
        with self.db() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS albums,
                    (SELECT COUNT(DISTINCT album_id) FROM album_service_status) AS enriched,
                    (SELECT COUNT(DISTINCT album_id) FROM album_service_status WHERE found = 1) AS matched,
                    SUM(CASE WHEN albums.case_broken = 'Yes' THEN 1 ELSE 0 END) AS broken_cases,
                    (SELECT COUNT(*) FROM tracks) AS tracks,
                    (SELECT COUNT(*) FROM album_genres) AS genres,
                    (SELECT COUNT(*) FROM cover_art) AS covers,
                    (SELECT COUNT(*) FROM album_service_status WHERE found = 1) AS service_matches
                FROM albums
                """
            ).fetchone()
        self.send_json(dict(row))

    def handle_tags(self):
        with self.db() as conn:
            rows = conn.execute(
                """
                WITH tag_sources(album_id, tag) AS (
                    SELECT album_id, name
                    FROM album_genres
                    UNION ALL
                    SELECT external_metadata.album_id, json_each.value
                    FROM external_metadata, json_each(external_metadata.genres)
                    WHERE external_metadata.lookup_status = 'matched'
                      AND json_valid(external_metadata.genres)
                    UNION ALL
                    SELECT external_metadata.album_id, json_each.value
                    FROM external_metadata, json_each(external_metadata.styles)
                    WHERE external_metadata.lookup_status = 'matched'
                      AND json_valid(external_metadata.styles)
                ),
                normalized_tags AS (
                    SELECT DISTINCT album_id, LOWER(TRIM(tag)) AS name
                    FROM tag_sources
                    WHERE TRIM(tag) != ''
                )
                SELECT name, COUNT(DISTINCT album_id) AS count
                FROM normalized_tags
                GROUP BY name
                ORDER BY count DESC, name
                """
            ).fetchall()
        tags = [{"name": row["name"], "count": row["count"]} for row in rows]
        self.send_json({"tags": tags})


def main():
    if not DB_PATH.exists():
        raise SystemExit(f"Database does not exist yet: {DB_PATH}\nRun scripts/build_database.py first.")
    ensure_database_schema()
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    host = os.environ.get("HOST", DEFAULT_HOST)
    server = ThreadingHTTPServer((host, port), CatalogHandler)
    print(f"Serving CD Archive at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
