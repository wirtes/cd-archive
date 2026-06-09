#!/usr/bin/env python3
import json
import sqlite3
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "cd_catalog.sqlite"
STATIC_DIR = ROOT / "web"


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
        else:
            super().do_GET()

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

    def handle_albums(self, parsed):
        params = parse_qs(parsed.query)
        q = (params.get("q", [""])[0] or "").strip()
        tag = (params.get("tag", [""])[0] or "").strip()
        artist = (params.get("artist", [""])[0] or "").strip()
        hide_na = (params.get("hide_na", ["0"])[0] or "0") == "1"
        enriched = params.get("enriched", ["all"])[0]
        limit = min(max(int(params.get("limit", ["50"])[0] or 50), 1), 200)
        offset = max(int(params.get("offset", ["0"])[0] or 0), 0)

        where = []
        values = []
        if q:
            where.append("(albums.artist LIKE ? OR albums.album_name LIKE ? OR albums.catalog_number LIKE ? OR musicbrainz_metadata.title LIKE ?)")
            needle = f"%{q}%"
            values.extend([needle, needle, needle, needle])
        if tag:
            where.append(
                """
                EXISTS (
                    SELECT 1
                    FROM album_genres
                    WHERE album_genres.album_id = albums.id
                      AND album_genres.name = ?
                )
                """
            )
            values.append(tag)
        if artist:
            where.append("(albums.artist = ? OR musicbrainz_metadata.artist_credit = ?)")
            values.extend([artist, artist])
        if hide_na:
            where.append("NOT (LOWER(TRIM(albums.artist)) = 'n/a' AND LOWER(TRIM(albums.album_name)) = 'n/a')")
        if enriched == "yes":
            where.append("musicbrainz_metadata.album_id IS NOT NULL")
        elif enriched == "no":
            where.append("musicbrainz_metadata.album_id IS NULL")

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with self.db() as conn:
            total = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM albums
                LEFT JOIN musicbrainz_metadata ON musicbrainz_metadata.album_id = albums.id
                {where_sql}
                """,
                values,
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT
                    albums.id, albums.row_number, albums.catalog_number, albums.media_format, albums.artist,
                    albums.album_name, albums.version_number, albums.case_broken,
                    musicbrainz_metadata.lookup_status, musicbrainz_metadata.mb_release_id,
                    musicbrainz_metadata.title AS mb_title,
                    musicbrainz_metadata.artist_credit AS mb_artist,
                    musicbrainz_metadata.date AS mb_date,
                    musicbrainz_metadata.label_names,
                    musicbrainz_metadata.track_count,
                    musicbrainz_metadata.mb_url,
                    (
                        SELECT GROUP_CONCAT(provider, ', ')
                        FROM album_service_status
                        WHERE album_service_status.album_id = albums.id
                          AND album_service_status.found = 1
                    ) AS matched_services
                FROM albums
                LEFT JOIN musicbrainz_metadata ON musicbrainz_metadata.album_id = albums.id
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
                ORDER BY provider
                """,
                (album_id,),
            ).fetchall()
            services = conn.execute(
                """
                SELECT provider, lookup_status, found, fetched_at, external_id,
                       title, url, lookup_error
                FROM album_service_status
                WHERE album_id = ?
                ORDER BY provider
                """,
                (album_id,),
            ).fetchall()
        self.send_json(
            {
                "album": dict(album),
                "musicbrainz": dict(metadata) if metadata else None,
                "tracks": [dict(row) for row in tracks],
                "genres": [dict(row) for row in genres],
                "cover_art": [dict(row) for row in cover_art],
                "external": [dict(row) for row in external],
                "services": [dict(row) for row in services],
            }
        )

    def handle_stats(self):
        with self.db() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS albums,
                    COUNT(musicbrainz_metadata.album_id) AS enriched,
                    SUM(CASE WHEN musicbrainz_metadata.lookup_status = 'matched' THEN 1 ELSE 0 END) AS matched,
                    SUM(CASE WHEN albums.case_broken = 'Yes' THEN 1 ELSE 0 END) AS broken_cases,
                    (SELECT COUNT(*) FROM tracks) AS tracks,
                    (SELECT COUNT(*) FROM album_genres) AS genres,
                    (SELECT COUNT(*) FROM cover_art) AS covers,
                    (SELECT COUNT(*) FROM album_service_status WHERE found = 1) AS service_matches
                FROM albums
                LEFT JOIN musicbrainz_metadata ON musicbrainz_metadata.album_id = albums.id
                """
            ).fetchone()
        self.send_json(dict(row))


def main():
    if not DB_PATH.exists():
        raise SystemExit(f"Database does not exist yet: {DB_PATH}\nRun scripts/build_database.py first.")
    server = ThreadingHTTPServer(("127.0.0.1", 8000), CatalogHandler)
    print("Serving CD Archive at http://127.0.0.1:8000")
    server.serve_forever()


if __name__ == "__main__":
    main()
