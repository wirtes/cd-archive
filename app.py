#!/usr/bin/env python3
import json
import sqlite3
import sys
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "cd_catalog.sqlite"
STATIC_DIR = ROOT / "web"
sys.path.insert(0, str(ROOT))
from scripts.build_database import enrich_album_from_service_url, load_dotenv


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
        if parsed.path.startswith("/api/albums/") and parsed.path.endswith("/music-service-url"):
            self.handle_music_service_url(parsed)
        else:
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def db(self):
        conn = sqlite3.connect(DB_PATH)
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
        self.send_json({"total": total, "limit": limit, "offset": offset, "albums": [dict(row) for row in rows]})

    def handle_album_detail(self, parsed):
        try:
            album_id = int(parsed.path.rsplit("/", 1)[1])
        except ValueError:
            self.send_json({"error": "Invalid album id"}, HTTPStatus.BAD_REQUEST)
            return

        with self.db() as conn:
            album = conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
            if not album:
                self.send_json({"error": "Album not found"}, HTTPStatus.NOT_FOUND)
                return
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
            artist_info = conn.execute(
                """
                SELECT name, lookup_status, lookup_error, fetched_at, lastfm_mbid,
                       lastfm_url, bio_summary, bio_content, image_url, local_image_url
                FROM artists
                WHERE name = ?
                """,
                (album["artist"],),
            ).fetchone()
        self.send_json(
            {
                "album": dict(album),
                "artist": dict(artist_info) if artist_info else None,
                "musicbrainz": dict(metadata) if metadata else None,
                "tracks": [dict(row) for row in tracks],
                "genres": [dict(row) for row in genres],
                "cover_art": [dict(row) for row in cover_art],
                "external": [dict(row) for row in external],
                "services": [dict(row) for row in services],
            }
        )

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
    server = ThreadingHTTPServer(("127.0.0.1", 8000), CatalogHandler)
    print("Serving CD Archive at http://127.0.0.1:8000")
    server.serve_forever()


if __name__ == "__main__":
    main()
