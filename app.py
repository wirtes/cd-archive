#!/usr/bin/env python3
import base64
import datetime as dt
import json
import os
import sqlite3
import sys
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "cd_catalog.sqlite"
STATIC_DIR = ROOT / "web"
COVER_DIR = STATIC_DIR / "covers"
sys.path.insert(0, str(ROOT))
from scripts.build_database import create_schema, enrich_album_from_discogs_url, enrich_album_from_service_url, load_dotenv


DEFAULT_PORT = 8190


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


def utc_now():
    return dt.datetime.now(dt.UTC).isoformat()


def ensure_database_schema():
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(albums)").fetchall()}
        if "compilation" not in columns:
            conn.execute("ALTER TABLE albums ADD COLUMN compilation INTEGER NOT NULL DEFAULT 0")
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
               track_number, title, length_ms, recording_id
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
            WHEN 'musicbrainz' THEN 1
            WHEN 'discogs' THEN 2
            WHEN 'lastfm' THEN 3
            ELSE 4
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
            WHEN 'musicbrainz' THEN 1
            WHEN 'discogs' THEN 2
            WHEN 'lastfm' THEN 3
            ELSE 4
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

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/albums":
            self.handle_albums(parsed)
        elif parsed.path.startswith("/api/albums/"):
            self.handle_album_detail(parsed)
        elif parsed.path == "/api/stats":
            self.handle_stats()
        elif parsed.path == "/api/tags":
            self.handle_tags()
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/music-service-preview":
            self.handle_music_service_preview()
        elif parsed.path == "/api/albums":
            self.handle_create_album()
        elif parsed.path.startswith("/api/albums/") and parsed.path.endswith("/music-service-url"):
            self.handle_music_service_url(parsed)
        else:
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_PUT(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/albums/"):
            self.handle_update_album(parsed)
        else:
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        parsed = urlparse(self.path)
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

    def handle_albums(self, parsed):
        params = parse_qs(parsed.query)
        q = (params.get("q", [""])[0] or "").strip()
        tag = (params.get("tag", [""])[0] or "").strip()
        artist = (params.get("artist", [""])[0] or "").strip()
        label = (params.get("label", [""])[0] or "").strip()
        hide_na = (params.get("hide_na", ["0"])[0] or "0") == "1"
        enriched = params.get("enriched", ["all"])[0]
        limit = min(max(int(params.get("limit", ["50"])[0] or 50), 1), 200)
        offset = max(int(params.get("offset", ["0"])[0] or 0), 0)

        where = []
        values = []
        if q:
            where.append(
                """
                (
                    albums.artist LIKE ? OR albums.album_name LIKE ? OR albums.catalog_number LIKE ?
                    OR albums.label LIKE ? OR albums.format LIKE ? OR albums.country LIKE ?
                    OR albums.released LIKE ? OR albums.genre LIKE ?
                    OR EXISTS (
                        SELECT 1
                        FROM external_metadata
                        WHERE external_metadata.album_id = albums.id
                          AND (external_metadata.title LIKE ? OR external_metadata.artist LIKE ?)
                    )
                )
                """
            )
            needle = f"%{q}%"
            values.extend([needle, needle, needle, needle, needle, needle, needle, needle, needle, needle])
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
                        FROM album_service_status
                        WHERE album_service_status.album_id = albums.id
                          AND album_service_status.found = 1
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
            result = enrich_album_from_discogs_url(conn, album_id, service_url, refresh_cache=False)
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

    def handle_create_album(self):
        try:
            payload = self.read_json_body()
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid request."}, HTTPStatus.BAD_REQUEST)
            return
        form = payload.get("album") or {}
        service_url = clean_text(payload.get("music_service_url"))
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
        try:
            with self.db() as conn:
                if not conn.execute("SELECT 1 FROM albums WHERE id = ?", (album_id,)).fetchone():
                    self.send_json({"error": "Album not found"}, HTTPStatus.NOT_FOUND)
                    return
                self.update_album_fields(conn, album_id, form)
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
    server = ThreadingHTTPServer(("127.0.0.1", port), CatalogHandler)
    print(f"Serving CD Archive at http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
