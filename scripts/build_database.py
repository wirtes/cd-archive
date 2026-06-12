#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import html
import json
import os
import re
import sqlite3
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "CD Catalog.csv"
ENV_PATH = Path(os.environ.get("ENV_PATH", ROOT / ".env")).expanduser()


def preload_dotenv(path: Path = ENV_PATH) -> None:
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


preload_dotenv()


def env_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


DB_PATH = env_path("DATABASE_PATH", ROOT / "data" / "cd_catalog.sqlite")
COVER_DIR = env_path("COVER_DIR", ROOT / "web" / "covers")
ARTIST_IMAGE_DIR = env_path("ARTIST_IMAGE_DIR", ROOT / "web" / "artist-images")
SQLITE_BUSY_TIMEOUT_MS = 30000

USER_AGENT = "cd-archive/1.0 (local catalog enrichment; https://musicbrainz.org/doc/MusicBrainz_API)"
API_THROTTLE_SECONDS = {
    "musicbrainz": 1.1,
    "cover_art_archive": 1.1,
    "discogs": 1.1,
    "apple_itunes": 1.1,
    "lastfm": 1.1,
}
LAST_API_REQUEST_AT: dict[str, float] = {}
LASTFM_PLACEHOLDER_IMAGE_ID = "2a96cbd8b46e442fc41c2b86b821562f"


SOURCE_COLUMNS = [
    ("timestamp", "Timestamp"),
    ("catalog_number", ""),
    ("artist", "Artist?"),
    ("album_name", "Album Name?"),
    (
        "version_number",
        "Version Number? (This is usually needed to catalog, but it's difficult to find sometimes so don't worry too much.)",
    ),
    (
        "case_broken",
        "Is the CD case broken (breaks in half, CD won't stay in place, etc.)?",
    ),
    (
        "label_number_missing",
        'If you know the label number but the CD case itself is missing, please fill in "N/A" for the required questions and select this as an option.',
    ),
    ("notes", "Anything else you’d like to add or mention?"),
    ("rateyourmusic", "Rateyourmusic.com ?"),
    ("other", "Other:"),
]


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def configure_sqlite_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")


def load_dotenv(path: Path = ENV_PATH) -> None:
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


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        DROP TABLE IF EXISTS external_metadata;
        DROP TABLE IF EXISTS album_service_status;
        DROP TABLE IF EXISTS cover_art;
        DROP TABLE IF EXISTS album_genres;
        DROP TABLE IF EXISTS tracks;
        DROP TABLE IF EXISTS musicbrainz_metadata;
        DROP TABLE IF EXISTS artists;
        DROP TABLE IF EXISTS albums;

        CREATE TABLE IF NOT EXISTS api_cache (
            provider TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            url TEXT NOT NULL,
            status_code INTEGER,
            fetched_at TEXT NOT NULL,
            raw_json TEXT,
            error TEXT,
            PRIMARY KEY(provider, cache_key)
        );

        CREATE TABLE albums (
            id INTEGER PRIMARY KEY,
            row_number INTEGER NOT NULL UNIQUE,
            timestamp TEXT,
            catalog_number TEXT,
            media_format TEXT NOT NULL DEFAULT 'cd',
            artist TEXT,
            album_name TEXT,
            label TEXT,
            format TEXT,
            compilation INTEGER NOT NULL DEFAULT 0,
            country TEXT,
            released TEXT,
            genre TEXT,
            field_sources TEXT,
            version_number TEXT,
            case_broken TEXT,
            label_number_missing TEXT,
            notes TEXT,
            rateyourmusic TEXT,
            other TEXT,
            source_json TEXT NOT NULL
        );

        CREATE TABLE artists (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            lookup_status TEXT NOT NULL,
            lookup_error TEXT,
            fetched_at TEXT NOT NULL,
            lastfm_mbid TEXT,
            lastfm_url TEXT,
            bio_summary TEXT,
            bio_content TEXT,
            image_url TEXT,
            local_image_url TEXT,
            raw_json TEXT
        );

        CREATE TABLE musicbrainz_metadata (
            album_id INTEGER PRIMARY KEY,
            lookup_status TEXT NOT NULL,
            lookup_error TEXT,
            fetched_at TEXT NOT NULL,
            mb_release_id TEXT,
            title TEXT,
            artist_credit TEXT,
            date TEXT,
            country TEXT,
            status TEXT,
            barcode TEXT,
            asin TEXT,
            release_group_id TEXT,
            release_group_primary_type TEXT,
            release_group_secondary_types TEXT,
            label_names TEXT,
            catalog_numbers TEXT,
            format TEXT,
            track_count INTEGER,
            score INTEGER,
            disambiguation TEXT,
            mb_url TEXT,
            raw_json TEXT,
            FOREIGN KEY(album_id) REFERENCES albums(id) ON DELETE CASCADE
        );

        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY,
            album_id INTEGER NOT NULL,
            medium_position INTEGER,
            medium_title TEXT,
            medium_format TEXT,
            track_position INTEGER,
            track_number TEXT,
            title TEXT,
            length_ms INTEGER,
            explicit INTEGER NOT NULL DEFAULT 0,
            preview_url TEXT,
            recording_id TEXT,
            FOREIGN KEY(album_id) REFERENCES albums(id) ON DELETE CASCADE
        );

        CREATE TABLE album_genres (
            id INTEGER PRIMARY KEY,
            album_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            name TEXT NOT NULL,
            count INTEGER,
            FOREIGN KEY(album_id) REFERENCES albums(id) ON DELETE CASCADE,
            UNIQUE(album_id, source, name)
        );

        CREATE TABLE cover_art (
            id INTEGER PRIMARY KEY,
            album_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            image_id TEXT,
            types TEXT,
            is_front INTEGER NOT NULL DEFAULT 0,
            is_back INTEGER NOT NULL DEFAULT 0,
            approved INTEGER NOT NULL DEFAULT 0,
            image_url TEXT,
            thumbnail_small TEXT,
            thumbnail_large TEXT,
            local_image_url TEXT,
            comment TEXT,
            raw_json TEXT,
            FOREIGN KEY(album_id) REFERENCES albums(id) ON DELETE CASCADE
        );

        CREATE TABLE album_service_status (
            album_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            lookup_status TEXT NOT NULL,
            found INTEGER NOT NULL DEFAULT 0,
            fetched_at TEXT NOT NULL,
            external_id TEXT,
            title TEXT,
            url TEXT,
            lookup_error TEXT,
            PRIMARY KEY(album_id, provider),
            FOREIGN KEY(album_id) REFERENCES albums(id) ON DELETE CASCADE
        );

        CREATE TABLE external_metadata (
            id INTEGER PRIMARY KEY,
            album_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            lookup_status TEXT NOT NULL,
            lookup_error TEXT,
            fetched_at TEXT NOT NULL,
            external_id TEXT,
            url TEXT,
            title TEXT,
            artist TEXT,
            genres TEXT,
            styles TEXT,
            track_count INTEGER,
            cover_url TEXT,
            raw_json TEXT,
            FOREIGN KEY(album_id) REFERENCES albums(id) ON DELETE CASCADE,
            UNIQUE(album_id, provider)
        );

        CREATE INDEX idx_albums_artist ON albums(artist);
        CREATE INDEX idx_albums_album_name ON albums(album_name);
        CREATE INDEX idx_artists_name ON artists(name);
        CREATE INDEX idx_musicbrainz_release ON musicbrainz_metadata(mb_release_id);
        CREATE INDEX idx_tracks_album ON tracks(album_id);
        CREATE INDEX idx_genres_album ON album_genres(album_id);
        CREATE INDEX idx_cover_art_album ON cover_art(album_id);
        CREATE INDEX idx_service_status_album ON album_service_status(album_id);
        CREATE INDEX idx_external_album ON external_metadata(album_id);
        """
    )


def ensure_track_preview_schema(conn: sqlite3.Connection) -> None:
    track_columns = {row[1] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()}
    if track_columns and "preview_url" not in track_columns:
        conn.execute("ALTER TABLE tracks ADD COLUMN preview_url TEXT")


def read_catalog_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def import_catalog(conn: sqlite3.Connection, rows: list[dict[str, str]]) -> None:
    insert_sql = """
        INSERT INTO albums (
            row_number, timestamp, catalog_number, media_format, artist, album_name,
            label, format, compilation, country, released, genre, field_sources, version_number,
            case_broken, label_number_missing, notes, rateyourmusic, other, source_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    payload = []
    for index, row in enumerate(rows, start=1):
        normalized = {field: (row.get(source, "") or "").strip() for field, source in SOURCE_COLUMNS}
        if is_self_titled(normalized["album_name"]) and not is_placeholder(normalized["artist"]):
            normalized["album_name"] = normalized["artist"]
        payload.append(
            (
                index,
                normalized["timestamp"],
                normalized["catalog_number"],
                "cd",
                normalized["artist"],
                normalized["album_name"],
                None,
                "cd",
                0,
                None,
                None,
                None,
                json.dumps({}, ensure_ascii=False),
                normalized["version_number"],
                normalized["case_broken"],
                normalized["label_number_missing"],
                normalized["notes"],
                normalized["rateyourmusic"],
                normalized["other"],
                json.dumps(row, ensure_ascii=False),
            )
        )
    conn.executemany(insert_sql, payload)


def json_request(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict | None, str | None]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return exc.code, None, "not found"
        return exc.code, None, f"HTTP Error {exc.code}: {exc.reason}"


def text_request(url: str, headers: dict[str, str] | None = None) -> tuple[int, str | None, str | None]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.status, response.read().decode(charset, errors="ignore"), None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return exc.code, None, "not found"
        return exc.code, None, f"HTTP Error {exc.code}: {exc.reason}"


def throttle_api_request(provider: str) -> None:
    delay = API_THROTTLE_SECONDS.get(provider, 1.1)
    last_request_at = LAST_API_REQUEST_AT.get(provider)
    if last_request_at is not None:
        elapsed = time.monotonic() - last_request_at
        if elapsed < delay:
            time.sleep(delay - elapsed)
    LAST_API_REQUEST_AT[provider] = time.monotonic()


def cached_json(
    conn: sqlite3.Connection,
    provider: str,
    cache_key: str,
    url: str,
    headers: dict[str, str] | None = None,
    refresh_cache: bool = False,
) -> tuple[dict | None, bool, str | None]:
    if not refresh_cache:
        row = conn.execute(
            """
            SELECT raw_json, error
            FROM api_cache
            WHERE provider = ? AND cache_key = ?
            """,
            (provider, cache_key),
        ).fetchone()
        if row:
            raw_json, error = row
            return json.loads(raw_json) if raw_json else None, True, error

    throttle_api_request(provider)
    status_code, payload, error = json_request(url, headers=headers)
    conn.execute(
        """
        INSERT INTO api_cache (provider, cache_key, url, status_code, fetched_at, raw_json, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, cache_key) DO UPDATE SET
            url = excluded.url,
            status_code = excluded.status_code,
            fetched_at = excluded.fetched_at,
            raw_json = excluded.raw_json,
            error = excluded.error
        """,
        (
            provider,
            cache_key,
            url,
            status_code,
            utc_now(),
            json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            error,
        ),
    )
    return payload, False, error


def cached_text_metadata(
    conn: sqlite3.Connection,
    provider: str,
    cache_key: str,
    url: str,
    refresh_cache: bool = False,
) -> tuple[dict | None, bool, str | None]:
    if not refresh_cache:
        row = conn.execute(
            """
            SELECT raw_json, error
            FROM api_cache
            WHERE provider = ? AND cache_key = ?
            """,
            (provider, cache_key),
        ).fetchone()
        if row:
            raw_json, error = row
            return json.loads(raw_json) if raw_json else None, True, error

    throttle_api_request(provider)
    status_code, text, error = text_request(url)
    payload = {"html": text} if text else {}
    conn.execute(
        """
        INSERT INTO api_cache (provider, cache_key, url, status_code, fetched_at, raw_json, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, cache_key) DO UPDATE SET
            url = excluded.url,
            status_code = excluded.status_code,
            fetched_at = excluded.fetched_at,
            raw_json = excluded.raw_json,
            error = excluded.error
        """,
        (
            provider,
            cache_key,
            url,
            status_code,
            utc_now(),
            json.dumps(payload, ensure_ascii=False) if payload else None,
            error,
        ),
    )
    return payload, False, error


def sanitize_cached_urls(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT provider, cache_key, url
        FROM api_cache
        WHERE provider = 'discogs' AND url LIKE '%token=%'
        """
    ).fetchall()
    for provider, cache_key, url in rows:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        sanitized_query = urllib.parse.urlencode([(key, value) for key, value in query if key != "token"])
        sanitized_url = urllib.parse.urlunparse(parsed._replace(query=sanitized_query))
        conn.execute(
            "UPDATE api_cache SET url = ? WHERE provider = ? AND cache_key = ?",
            (sanitized_url, provider, cache_key),
        )


def musicbrainz_get(
    conn: sqlite3.Connection,
    path: str,
    params: dict[str, str],
    cache_key: str,
    refresh_cache: bool,
) -> tuple[dict | None, bool, str | None]:
    query = urllib.parse.urlencode(params)
    url = f"https://musicbrainz.org/ws/2/{path}?{query}"
    return cached_json(conn, "musicbrainz", cache_key, url, refresh_cache=refresh_cache)


def lucene_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def find_release(
    conn: sqlite3.Connection,
    artist: str,
    album_name: str,
    refresh_cache: bool,
) -> tuple[dict | None, dict, bool]:
    if is_placeholder(artist) or is_placeholder(album_name):
        return None, {"search": None, "selected": None, "skipped": "placeholder artist or album"}, True

    search_album_name = artist if is_self_titled(album_name) and artist else album_name
    terms = []
    if search_album_name:
        terms.append(f"release:{lucene_quote(search_album_name)}")
    if artist and artist.casefold() not in {"v/a", "various", "various artists"}:
        terms.append(f"artist:{lucene_quote(artist)}")

    query = " AND ".join(terms) if terms else lucene_quote(album_name or artist)
    search_cache_key = f"release-search:{query}"
    search_payload, search_cached, _ = musicbrainz_get(
        conn,
        "release/",
        {"query": query, "fmt": "json", "limit": "5"},
        search_cache_key,
        refresh_cache,
    )
    releases = (search_payload or {}).get("releases", [])
    if not releases:
        return None, {"search": search_payload, "selected": None}, search_cached

    selected = releases[0]
    release_id = selected.get("id")
    detail_payload = {}
    detail_cached = True
    if release_id:
        detail_payload, detail_cached, _ = musicbrainz_get(
            conn,
            f"release/{release_id}",
            {
                "fmt": "json",
                "inc": "artist-credits+labels+release-groups+media+recordings+genres+tags",
            },
            f"release-detail:{release_id}",
            refresh_cache,
        )
    return detail_payload or selected, {"search": search_payload, "selected": selected, "detail": detail_payload}, search_cached and detail_cached


def is_placeholder(value: str) -> bool:
    normalized = (value or "").strip().casefold()
    return normalized in {"", "n/a", "na", "none", "unknown", "?"}


def is_self_titled(value: str) -> bool:
    normalized = (value or "").strip().casefold().replace(".", "")
    return normalized in {"s/t", "st", "self titled", "self-titled"}


def normalize_artist_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\s*\(\d+\)\s*$", "", value or "")).strip()


def normalize_match_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def is_various_artist(value: str | None) -> bool:
    normalized = normalize_artist_name(value).casefold().replace(".", "")
    return normalized in {"v/a", "va", "various", "various artists"}


def first_joined(values: list[str | None]) -> str:
    return ", ".join(value for value in values if value)


def artist_credit_name(release: dict) -> str:
    credits = release.get("artist-credit") or []
    return "".join(part if isinstance(part, str) else part.get("name", "") for part in credits)


def release_to_metadata(release: dict | None, raw: dict, status: str, error: str | None = None) -> dict:
    now = utc_now()
    if not release:
        return {
            "lookup_status": status,
            "lookup_error": error,
            "fetched_at": now,
            "raw_json": json.dumps(raw, ensure_ascii=False),
        }

    release_group = release.get("release-group") or {}
    label_info = release.get("label-info") or []
    media = release.get("media") or []
    track_count = sum(len(medium.get("tracks") or []) for medium in media) or None

    return {
        "lookup_status": status,
        "lookup_error": error,
        "fetched_at": now,
        "mb_release_id": release.get("id"),
        "title": release.get("title"),
        "artist_credit": artist_credit_name(release),
        "date": release.get("date"),
        "country": release.get("country"),
        "status": release.get("status"),
        "barcode": release.get("barcode"),
        "asin": release.get("asin"),
        "release_group_id": release_group.get("id"),
        "release_group_primary_type": release_group.get("primary-type"),
        "release_group_secondary_types": json.dumps(release_group.get("secondary-types") or []),
        "label_names": unique_join([(info.get("label") or {}).get("name") for info in label_info]),
        "catalog_numbers": unique_join([info.get("catalog-number") for info in label_info]),
        "format": unique_join([medium.get("format") for medium in media]),
        "track_count": track_count,
        "score": release.get("score"),
        "disambiguation": release.get("disambiguation"),
        "mb_url": f"https://musicbrainz.org/release/{release.get('id')}" if release.get("id") else None,
        "raw_json": json.dumps(raw, ensure_ascii=False),
    }


def upsert_metadata(conn: sqlite3.Connection, album_id: int, metadata: dict) -> None:
    columns = [
        "album_id",
        "lookup_status",
        "lookup_error",
        "fetched_at",
        "mb_release_id",
        "title",
        "artist_credit",
        "date",
        "country",
        "status",
        "barcode",
        "asin",
        "release_group_id",
        "release_group_primary_type",
        "release_group_secondary_types",
        "label_names",
        "catalog_numbers",
        "format",
        "track_count",
        "score",
        "disambiguation",
        "mb_url",
        "raw_json",
    ]
    values = [album_id] + [metadata.get(column) for column in columns[1:]]
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns[1:])
    conn.execute(
        f"""
        INSERT INTO musicbrainz_metadata ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(album_id) DO UPDATE SET {updates}
        """,
        values,
    )


def upsert_service_status(
    conn: sqlite3.Connection,
    album_id: int,
    provider: str,
    lookup_status: str,
    external_id: str | None = None,
    title: str | None = None,
    url: str | None = None,
    lookup_error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO album_service_status (
            album_id, provider, lookup_status, found, fetched_at,
            external_id, title, url, lookup_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(album_id, provider) DO UPDATE SET
            lookup_status = excluded.lookup_status,
            found = excluded.found,
            fetched_at = excluded.fetched_at,
            external_id = excluded.external_id,
            title = excluded.title,
            url = excluded.url,
            lookup_error = excluded.lookup_error
        """,
        (
            album_id,
            provider,
            lookup_status,
            1 if lookup_status == "matched" else 0,
            utc_now(),
            external_id,
            title,
            url,
            lookup_error,
        ),
    )


def replace_tracks(conn: sqlite3.Connection, album_id: int, release: dict | None) -> int:
    conn.execute("DELETE FROM tracks WHERE album_id = ?", (album_id,))
    if not release:
        return 0

    rows = []
    for medium in release.get("media") or []:
        for track in medium.get("tracks") or []:
            recording = track.get("recording") or {}
            rows.append(
                (
                    album_id,
                    medium.get("position"),
                    medium.get("title"),
                    medium.get("format"),
                    track.get("position"),
                    track.get("number"),
                    track.get("title") or recording.get("title"),
                    track.get("length") or recording.get("length"),
                    0,
                    None,
                    recording.get("id"),
                )
            )
    conn.executemany(
        """
        INSERT INTO tracks (
            album_id, medium_position, medium_title, medium_format,
            track_position, track_number, title, length_ms, explicit, preview_url, recording_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def replace_musicbrainz_genres(conn: sqlite3.Connection, album_id: int, release: dict | None) -> int:
    conn.execute("DELETE FROM album_genres WHERE album_id = ? AND source IN ('musicbrainz_genre', 'musicbrainz_tag')", (album_id,))
    if not release:
        return 0

    genre_rows = []
    seen = set()
    candidates = [
        ("musicbrainz_genre", release.get("genres") or []),
        ("musicbrainz_tag", release.get("tags") or []),
        ("musicbrainz_genre", (release.get("release-group") or {}).get("genres") or []),
        ("musicbrainz_tag", (release.get("release-group") or {}).get("tags") or []),
    ]
    for source, items in candidates:
        for item in items:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            key = (source, name.casefold())
            if key in seen:
                continue
            seen.add(key)
            genre_rows.append((album_id, source, name, item.get("count")))

    conn.executemany(
        """
        INSERT OR IGNORE INTO album_genres (album_id, source, name, count)
        VALUES (?, ?, ?, ?)
        """,
        genre_rows,
    )
    return len(genre_rows)


def json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in value.split(",") if item.strip()]


def unique_join(values: list[str | None], limit: int | None = None) -> str | None:
    output = []
    seen = set()
    for value in values:
        cleaned = (value or "").strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
        if limit and len(output) >= limit:
            break
    return ", ".join(output) if output else None


def value_from_mapping_or_string(value, key: str | None = None) -> str | None:
    if isinstance(value, dict):
        if key:
            return value.get(key)
        return value.get("name") or value.get("title") or value.get("uri") or value.get("resource_url")
    if value:
        return str(value)
    return None


def listify(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def discogs_is_compilation(payload: dict) -> bool:
    if is_various_artist(payload.get("artists_sort")):
        return True
    for artist in payload.get("artists") or []:
        if is_various_artist(value_from_mapping_or_string(artist, "name")):
            return True
    track_artists = {
        normalize_artist_name(value_from_mapping_or_string(artist, "name")).casefold()
        for track in flatten_discogs_tracks(payload.get("tracklist") or [])
        for artist in (track.get("artists") or [])
        if value_from_mapping_or_string(artist, "name")
    }
    if len(track_artists) > 1:
        return True
    for fmt in payload.get("formats") or []:
        if not isinstance(fmt, dict):
            continue
        values = [fmt.get("name"), *(fmt.get("descriptions") or [])]
        if any(str(value or "").casefold() == "compilation" for value in values):
            return True
    return False


def musicbrainz_genre_names(conn: sqlite3.Connection, album_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM album_genres
        WHERE album_id = ? AND source = ?
        ORDER BY COALESCE(count, 0) DESC, name
        """,
        (album_id, "musicbrainz_genre"),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            """
            SELECT name
            FROM album_genres
            WHERE album_id = ? AND source = ?
            ORDER BY COALESCE(count, 0) DESC, name
            """,
            (album_id, "musicbrainz_tag"),
        ).fetchall()
    return [row["name"] for row in rows]


def upsert_musicbrainz_external_metadata(conn: sqlite3.Connection, album_id: int, metadata: dict) -> None:
    genres = musicbrainz_genre_names(conn, album_id)
    upsert_external_metadata(
        conn,
        album_id,
        {
            "provider": "musicbrainz",
            "lookup_status": metadata.get("lookup_status"),
            "lookup_error": metadata.get("lookup_error"),
            "fetched_at": metadata.get("fetched_at") or utc_now(),
            "external_id": metadata.get("mb_release_id"),
            "url": metadata.get("mb_url"),
            "title": metadata.get("title"),
            "artist": metadata.get("artist_credit"),
            "genres": json.dumps(genres, ensure_ascii=False),
            "styles": None,
            "track_count": metadata.get("track_count"),
            "cover_url": None,
            "raw_json": metadata.get("raw_json"),
        },
    )


def discogs_master_fields(row: sqlite3.Row | None) -> dict[str, str | None]:
    if not row or row["lookup_status"] != "matched":
        return {}
    raw = json.loads(row["raw_json"] or "{}")
    detail = raw.get("detail") or {}
    labels = detail.get("labels") or []
    label_names = []
    for label in labels:
        label_names.append(value_from_mapping_or_string(label, "name"))
    return {
        "label": unique_join(label_names),
        "country": detail.get("country"),
        "released": detail.get("released") or detail.get("released_formatted"),
        "genre": unique_join(json_list(row["genres"]) + json_list(row["styles"]), limit=6),
        "compilation": discogs_is_compilation(detail),
    }


def lastfm_master_fields(row: sqlite3.Row | None) -> dict[str, str | None]:
    if not row or row["lookup_status"] != "matched":
        return {}
    return {"genre": unique_join(json_list(row["genres"]), limit=6), "compilation": is_various_artist(row["artist"])}


def apple_master_fields(row: sqlite3.Row | None) -> dict[str, str | None]:
    if not row or row["lookup_status"] != "matched":
        return {}
    raw = json.loads(row["raw_json"] or "{}")
    search_results = raw.get("search", {}).get("results") or []
    album = next(
        (
            item
            for item in search_results
            if str(item.get("collectionId") or "") == str(row["external_id"] or "")
        ),
        {},
    )
    return {
        "country": album.get("country"),
        "released": apple_release_year(album.get("releaseDate")),
        "genre": unique_join(json_list(row["genres"]), limit=6),
        "compilation": is_various_artist(row["artist"]),
    }


def musicbrainz_master_fields(conn: sqlite3.Connection, album_id: int) -> dict[str, str | None]:
    row = conn.execute("SELECT * FROM musicbrainz_metadata WHERE album_id = ?", (album_id,)).fetchone()
    if not row or row["lookup_status"] != "matched":
        return {}
    secondary_types = json_list(row["release_group_secondary_types"])
    return {
        "label": row["label_names"],
        "country": row["country"],
        "released": row["date"],
        "genre": unique_join(musicbrainz_genre_names(conn, album_id), limit=6),
        "compilation": any(value.casefold() == "compilation" for value in secondary_types) or is_various_artist(row["artist_credit"]),
    }


def update_master_catalog_fields(conn: sqlite3.Connection, album_id: int) -> None:
    external_rows = {
        row["provider"]: row
        for row in conn.execute(
            "SELECT * FROM external_metadata WHERE album_id = ?",
            (album_id,),
        ).fetchall()
    }
    sources = [
        ("apple_itunes", apple_master_fields(external_rows.get("apple_itunes"))),
        ("discogs", discogs_master_fields(external_rows.get("discogs"))),
        ("lastfm", lastfm_master_fields(external_rows.get("lastfm"))),
        ("musicbrainz", musicbrainz_master_fields(conn, album_id)),
    ]
    resolved: dict[str, str | int | bool | None] = {}
    provenance: dict[str, str] = {}
    for field in ("label", "country", "released", "genre", "compilation"):
        for provider, values in sources:
            value = values.get(field)
            if value is not None and value != "":
                resolved[field] = value
                provenance[field] = provider
                break
        else:
            resolved[field] = 0 if field == "compilation" else None

    conn.execute(
        """
        UPDATE albums
        SET label = ?, country = ?, released = ?, genre = ?, compilation = ?, field_sources = ?
        WHERE id = ?
        """,
        (
            resolved["label"],
            resolved["country"],
            resolved["released"],
            resolved["genre"],
            1 if resolved["compilation"] else 0,
            json.dumps(provenance, ensure_ascii=False),
            album_id,
        ),
    )


def fetch_cover_art(conn: sqlite3.Connection, album_id: int, mb_release_id: str | None, refresh_cache: bool) -> int:
    conn.execute("DELETE FROM cover_art WHERE album_id = ? AND source = 'cover_art_archive'", (album_id,))
    if not mb_release_id:
        return 0

    url = f"https://coverartarchive.org/release/{mb_release_id}"
    payload, _, error = cached_json(conn, "cover_art_archive", f"release:{mb_release_id}", url, refresh_cache=refresh_cache)
    if error or not payload:
        return 0

    image = select_album_cover(payload.get("images") or [])
    if not image:
        return 0

    thumbnails = image.get("thumbnails") or {}
    local_image_url = download_cover_image(
        mb_release_id,
        str(image.get("id")) if image.get("id") is not None else "image",
        thumbnails.get("large") or thumbnails.get("500") or thumbnails.get("small") or image.get("image"),
        refresh_cache,
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
            "cover_art_archive",
            str(image.get("id")) if image.get("id") is not None else None,
            json.dumps(image.get("types") or [], ensure_ascii=False),
            1 if image.get("front") else 0,
            1 if image.get("back") else 0,
            1 if image.get("approved") else 0,
            image.get("image"),
            thumbnails.get("small"),
            thumbnails.get("large") or thumbnails.get("500"),
            local_image_url,
            image.get("comment"),
            json.dumps(image, ensure_ascii=False),
        )
    )
    return 1


def replace_provider_cover_art(
    conn: sqlite3.Connection,
    album_id: int,
    provider: str,
    image_url: str | None,
    image_id: str | None = None,
    raw: dict | None = None,
) -> int:
    conn.execute("DELETE FROM cover_art WHERE album_id = ? AND source = ?", (album_id, provider))
    if not image_url:
        return 0
    local_image_url = download_cover_image(provider, image_id or str(album_id), image_url, False)
    conn.execute(
        """
        INSERT INTO cover_art (
            album_id, source, image_id, types, is_front, is_back, approved,
            image_url, thumbnail_small, thumbnail_large, local_image_url, comment, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            album_id,
            provider,
            image_id,
            json.dumps(["Front"], ensure_ascii=False),
            1,
            0,
            0,
            image_url,
            image_url,
            image_url,
            local_image_url,
            "",
            json.dumps(raw or {}, ensure_ascii=False),
        ),
    )
    return 1


def select_album_cover(images: list[dict]) -> dict | None:
    if not images:
        return None
    return (
        next((image for image in images if image.get("front") and image.get("approved")), None)
        or next((image for image in images if image.get("front")), None)
        or next((image for image in images if image.get("approved")), None)
        or images[0]
    )


def download_cover_image(source_id: str, image_id: str, image_url: str | None, refresh_cache: bool) -> str | None:
    if not image_url:
        return None
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(image_url)
    suffix = Path(parsed.path).suffix
    if suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    safe_name = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in f"{source_id}-{image_id}")
    output_path = COVER_DIR / f"{safe_name}{suffix}"
    if output_path.exists() and not refresh_cache:
        return f"/covers/{output_path.name}"

    request = urllib.request.Request(image_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            output_path.write_bytes(response.read())
        return f"/covers/{output_path.name}"
    except urllib.error.URLError:
        return None


def download_artist_image(artist_name: str, image_url: str | None, refresh_cache: bool) -> str | None:
    if not image_url:
        return None
    ARTIST_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(image_url)
    suffix = Path(parsed.path).suffix
    if suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    safe_name = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in artist_name)
    output_path = ARTIST_IMAGE_DIR / f"{safe_name}{suffix}"
    if output_path.exists() and not refresh_cache:
        if is_usable_artist_image(output_path):
            return f"/artist-images/{output_path.name}"
        output_path.unlink(missing_ok=True)

    request = urllib.request.Request(image_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            output_path.write_bytes(response.read())
        if not is_usable_artist_image(output_path):
            output_path.unlink(missing_ok=True)
            return None
        return f"/artist-images/{output_path.name}"
    except urllib.error.URLError:
        return None


def image_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as file:
            header = file.read(24)
            if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
                return struct.unpack(">II", header[16:24])
            if header.startswith(b"\xff\xd8"):
                file.seek(2)
                while True:
                    marker_start = file.read(1)
                    if not marker_start:
                        return None
                    if marker_start != b"\xff":
                        continue
                    marker = file.read(1)
                    while marker == b"\xff":
                        marker = file.read(1)
                    if marker in {b"\xc0", b"\xc1", b"\xc2", b"\xc3", b"\xc5", b"\xc6", b"\xc7", b"\xc9", b"\xca", b"\xcb", b"\xcd", b"\xce", b"\xcf"}:
                        file.read(3)
                        height, width = struct.unpack(">HH", file.read(4))
                        return width, height
                    length_bytes = file.read(2)
                    if len(length_bytes) != 2:
                        return None
                    length = struct.unpack(">H", length_bytes)[0]
                    file.seek(max(length - 2, 0), 1)
    except OSError:
        return None
    return None


def is_usable_artist_image(path: Path) -> bool:
    dimensions = image_dimensions(path)
    if not dimensions:
        return path.stat().st_size > 4096
    width, height = dimensions
    return width >= 120 and height >= 120


def existing_artist_image_url(artist_name: str) -> str | None:
    safe_name = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in artist_name)
    for suffix in (".jpg", ".jpeg", ".png", ".webp"):
        output_path = ARTIST_IMAGE_DIR / f"{safe_name}{suffix}"
        if output_path.exists() and is_usable_artist_image(output_path):
            return f"/artist-images/{output_path.name}"
    return None


def is_lastfm_placeholder_image(image_url: str | None) -> bool:
    return bool(image_url and LASTFM_PLACEHOLDER_IMAGE_ID in image_url)


def best_lastfm_image_url(images: list[dict]) -> str | None:
    size_rank = {"mega": 6, "extralarge": 5, "large": 4, "medium": 3, "small": 2, "": 1}
    candidates = sorted(
        (image for image in images if image.get("#text") and not is_lastfm_placeholder_image(image.get("#text"))),
        key=lambda image: size_rank.get(image.get("size", ""), 0),
        reverse=True,
    )
    return candidates[0].get("#text") if candidates else None


def fetch_lastfm_artist_page_image(artist: str) -> str | None:
    url = f"https://www.last.fm/music/{urllib.parse.quote_plus(artist)}"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except urllib.error.URLError:
        return None

    candidates = re.findall(
        r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )

    for token in html.split('"'):
        if "lastfm.freetls.fastly.net/i/u/" in token:
            candidates.append(token)

    for candidate in candidates:
        if candidate.startswith("https://") and not is_lastfm_placeholder_image(candidate):
            return candidate
    return None


def upsert_external_metadata(conn: sqlite3.Connection, album_id: int, metadata: dict) -> None:
    columns = [
        "album_id",
        "provider",
        "lookup_status",
        "lookup_error",
        "fetched_at",
        "external_id",
        "url",
        "title",
        "artist",
        "genres",
        "styles",
        "track_count",
        "cover_url",
        "raw_json",
    ]
    values = [album_id] + [metadata.get(column) for column in columns[1:]]
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns[1:])
    conn.execute(
        f"""
        INSERT INTO external_metadata ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(album_id, provider) DO UPDATE SET {updates}
        """,
        values,
    )


def upsert_artist(conn: sqlite3.Connection, metadata: dict) -> None:
    columns = [
        "name",
        "lookup_status",
        "lookup_error",
        "fetched_at",
        "lastfm_mbid",
        "lastfm_url",
        "bio_summary",
        "bio_content",
        "image_url",
        "local_image_url",
        "raw_json",
    ]
    values = [metadata.get(column) for column in columns]
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns[1:])
    conn.execute(
        f"""
        INSERT INTO artists ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(name) DO UPDATE SET {updates}
        """,
        values,
    )


def fetch_lastfm_artist(conn: sqlite3.Connection, artist: str, refresh_cache: bool) -> None:
    artist = (artist or "").strip()
    if is_placeholder(artist):
        return

    existing = conn.execute("SELECT image_url, local_image_url FROM artists WHERE name = ?", (artist,)).fetchone()
    api_key = os.environ.get("LASTFM_API_KEY", "").strip()
    if not api_key:
        upsert_artist(
            conn,
            {
                "name": artist,
                "lookup_status": "not_configured",
                "lookup_error": "Set LASTFM_API_KEY to enable Last.fm artist enrichment.",
                "fetched_at": utc_now(),
            },
        )
        return

    params = urllib.parse.urlencode(
        {
            "method": "artist.getinfo",
            "artist": artist,
            "api_key": api_key,
            "format": "json",
        }
    )
    url = f"https://ws.audioscrobbler.com/2.0/?{params}"
    payload, _, error = cached_json(conn, "lastfm", f"artist:{artist}", url, refresh_cache=refresh_cache)
    artist_payload = (payload or {}).get("artist") or {}
    status = "matched" if artist_payload else ("not_found" if not error or error == "not found" else "error")
    images = artist_payload.get("image") or []
    image_url = best_lastfm_image_url(images)
    if not image_url and artist_payload:
        image_url = fetch_lastfm_artist_page_image(artist)
    local_image_url = (
        existing["local_image_url"]
        if existing
        and existing["local_image_url"]
        and not is_lastfm_placeholder_image(existing["image_url"])
        and not refresh_cache
        else None
    )
    if not local_image_url and not refresh_cache:
        local_image_url = existing_artist_image_url(artist)
    if not local_image_url and image_url:
        local_image_url = download_artist_image(artist, image_url, refresh_cache)
    bio = artist_payload.get("bio") or {}

    upsert_artist(
        conn,
        {
            "name": artist,
            "lookup_status": status,
            "lookup_error": error,
            "fetched_at": utc_now(),
            "lastfm_mbid": artist_payload.get("mbid"),
            "lastfm_url": artist_payload.get("url"),
            "bio_summary": bio.get("summary"),
            "bio_content": bio.get("content"),
            "image_url": image_url,
            "local_image_url": local_image_url,
            "raw_json": json.dumps(payload or {}, ensure_ascii=False),
        },
    )


def fetch_discogs(
    conn: sqlite3.Connection,
    album_id: int,
    artist: str,
    album_name: str,
    refresh_cache: bool,
    prefer_discogs_tracks: bool = False,
) -> None:
    existing = conn.execute(
        "SELECT lookup_status FROM external_metadata WHERE album_id = ? AND provider = ?",
        (album_id, "discogs"),
    ).fetchone()
    if existing and existing["lookup_status"] == "matched" and not refresh_cache:
        if prefer_discogs_tracks:
            replace_tracks_from_cached_discogs_match(conn, album_id)
        return

    token = os.environ.get("DISCOGS_TOKEN", "").strip()
    if not token:
        upsert_external_metadata(
            conn,
            album_id,
            {
                "provider": "discogs",
                "lookup_status": "not_configured",
                "lookup_error": "Set DISCOGS_TOKEN to enable Discogs enrichment.",
                "fetched_at": utc_now(),
            },
        )
        upsert_service_status(conn, album_id, "discogs", "not_configured", lookup_error="Set DISCOGS_TOKEN to enable Discogs enrichment.")
        return

    query = f"{artist} {album_name}".strip()
    headers = {"Authorization": f"Discogs token={token}"}
    release_params = urllib.parse.urlencode({"q": query, "type": "release", "format": "CD", "per_page": "1"})
    master_params = urllib.parse.urlencode({"q": query, "type": "master", "per_page": "1"})
    release_url = f"https://api.discogs.com/database/search?{release_params}"
    master_url = f"https://api.discogs.com/database/search?{master_params}"
    release_payload, _, release_error = cached_json(conn, "discogs", f"search-release:{query}", release_url, headers=headers, refresh_cache=refresh_cache)
    master_payload, _, master_error = cached_json(conn, "discogs", f"search-master:{query}", master_url, headers=headers, refresh_cache=refresh_cache)
    release_results = (release_payload or {}).get("results") or []
    master_results = (master_payload or {}).get("results") or []
    if release_error and master_error:
        status = "not_found" if release_error == "not found" or master_error == "not found" else "error"
        if existing and existing["lookup_status"] == "matched" and status == "error":
            return
        upsert_external_metadata(
            conn,
            album_id,
            {
                "provider": "discogs",
                "lookup_status": status,
                "lookup_error": release_error or master_error,
                "fetched_at": utc_now(),
                "raw_json": json.dumps({"release_search": release_payload, "master_search": master_payload}, ensure_ascii=False),
            },
        )
        upsert_service_status(conn, album_id, "discogs", status, lookup_error=release_error or master_error)
        return
    if not release_results and not master_results:
        upsert_external_metadata(
            conn,
            album_id,
            {
                "provider": "discogs",
                "lookup_status": "not_found",
                "lookup_error": release_error or master_error,
                "fetched_at": utc_now(),
                "raw_json": json.dumps({"release_search": release_payload, "master_search": master_payload}, ensure_ascii=False),
            },
        )
        upsert_service_status(conn, album_id, "discogs", "not_found", lookup_error=release_error or master_error)
        return

    selected = release_results[0] if release_results else master_results[0]
    discogs_id = selected.get("id")
    detail_payload = {}
    if selected.get("type") == "master" and discogs_id:
        master_detail, _, _ = cached_json(conn, "discogs", f"master:{discogs_id}", f"https://api.discogs.com/masters/{discogs_id}", headers=headers, refresh_cache=refresh_cache)
        main_release = (master_detail or {}).get("main_release")
        if main_release:
            detail_url = f"https://api.discogs.com/releases/{main_release}"
            detail_payload, _, _ = cached_json(conn, "discogs", f"release:{main_release}", detail_url, headers=headers, refresh_cache=refresh_cache)
    elif discogs_id:
        detail_url = f"https://api.discogs.com/releases/{discogs_id}"
        detail_payload, _, _ = cached_json(conn, "discogs", f"release:{discogs_id}", detail_url, headers=headers, refresh_cache=refresh_cache)

    payload = detail_payload or selected
    upsert_discogs_release(
        conn,
        album_id,
        payload,
        selected,
        {"release_search": release_payload, "master_search": master_payload, "detail": detail_payload},
        replace_tracklist=prefer_discogs_tracks,
    )


def upsert_discogs_release(
    conn: sqlite3.Connection,
    album_id: int,
    payload: dict,
    selected: dict | None = None,
    raw: dict | None = None,
    replace_tracklist: bool = False,
) -> tuple[str | None, str | None]:
    selected = selected or payload
    discogs_id = payload.get("id") or selected.get("id")
    cover_url = discogs_cover_url(payload, selected)
    artist_names = []
    for artist_entry in payload.get("artists") or []:
        artist_names.append(value_from_mapping_or_string(artist_entry, "name"))
    is_compilation = discogs_is_compilation(payload) or is_various_artist(first_joined(artist_names))
    artist = "Various Artists" if is_compilation and any(is_various_artist(name) for name in artist_names) else first_joined(artist_names)
    upsert_external_metadata(
        conn,
        album_id,
        {
            "provider": "discogs",
            "lookup_status": "matched",
            "lookup_error": None,
            "fetched_at": utc_now(),
            "external_id": str(discogs_id) if discogs_id else None,
            "url": payload.get("uri") or payload.get("resource_url"),
            "title": payload.get("title"),
            "artist": artist,
            "genres": json.dumps(payload.get("genres") or selected.get("genre") or [], ensure_ascii=False),
            "styles": json.dumps(payload.get("styles") or selected.get("style") or [], ensure_ascii=False),
            "track_count": len(payload.get("tracklist") or []),
            "cover_url": cover_url,
            "raw_json": json.dumps(raw or payload, ensure_ascii=False),
        },
    )
    upsert_service_status(
        conn,
        album_id,
        "discogs",
        "matched",
        external_id=str(discogs_id) if discogs_id else None,
        title=payload.get("title"),
        url=payload.get("uri") or payload.get("resource_url"),
    )
    replace_provider_cover_art(conn, album_id, "discogs", cover_url, str(discogs_id) if discogs_id else None, payload)
    insert_discogs_tracks(conn, album_id, payload, replace_existing=replace_tracklist)
    return artist, payload.get("title")


def duration_to_ms(value: str | None) -> int | None:
    if not value:
        return None
    parts = [part for part in value.strip().split(":") if part.isdigit()]
    if not parts:
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds * 1000


def discogs_track_artist(track: dict) -> str | None:
    artists = [value_from_mapping_or_string(artist, "name") for artist in track.get("artists") or []]
    return first_joined([normalize_artist_name(artist) for artist in artists if artist])


def flatten_discogs_tracks(tracklist: list[dict]) -> list[dict]:
    tracks = []
    for item in tracklist:
        if not isinstance(item, dict):
            continue
        if item.get("type_") == "heading":
            tracks.extend(flatten_discogs_tracks(item.get("sub_tracks") or []))
            continue
        if item.get("type_") != "track":
            continue
        tracks.append(item)
        tracks.extend(flatten_discogs_tracks(item.get("sub_tracks") or []))
    return tracks


def insert_discogs_tracks(conn: sqlite3.Connection, album_id: int, payload: dict, replace_existing: bool = False) -> int:
    existing = conn.execute("SELECT COUNT(*) FROM tracks WHERE album_id = ?", (album_id,)).fetchone()[0]
    if existing and not replace_existing:
        return 0
    tracklist = flatten_discogs_tracks(payload.get("tracklist") or [])
    rows = []
    for index, track in enumerate(tracklist, start=1):
        title = track.get("title")
        artist = discogs_track_artist(track)
        display_title = f"{artist} - {title}" if artist and title else title
        if not display_title:
            continue
        rows.append(
            (
                album_id,
                1,
                "",
                payload.get("format") or "CD",
                index,
                track.get("position") or str(index),
                display_title,
                duration_to_ms(track.get("duration")),
                0,
                None,
                f"discogs:{payload.get('id')}:{track.get('position') or index}",
            )
        )
    if not rows:
        return 0
    if replace_existing:
        conn.execute("DELETE FROM tracks WHERE album_id = ?", (album_id,))
    conn.executemany(
        """
        INSERT INTO tracks (
            album_id, medium_position, medium_title, medium_format, track_position,
            track_number, title, length_ms, explicit, preview_url, recording_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def discogs_detail_payload_from_raw(raw_json: str | None) -> dict | None:
    if not raw_json:
        return None
    try:
        raw = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    for key in ("detail", "release"):
        payload = raw.get(key)
        if isinstance(payload, dict) and payload.get("tracklist"):
            return payload
    if raw.get("tracklist"):
        return raw
    return None


def replace_tracks_from_cached_discogs_match(conn: sqlite3.Connection, album_id: int) -> int:
    row = conn.execute(
        """
        SELECT raw_json
        FROM external_metadata
        WHERE album_id = ? AND provider = 'discogs' AND lookup_status = 'matched'
        """,
        (album_id,),
    ).fetchone()
    payload = discogs_detail_payload_from_raw(row["raw_json"] if row else None)
    if not payload:
        return 0
    return insert_discogs_tracks(conn, album_id, payload, replace_existing=True)


def discogs_cover_url(payload: dict, selected: dict) -> str | None:
    images = payload.get("images") or []
    front = next((image for image in images if isinstance(image, dict) and image.get("type") == "primary"), None)
    image = front or (images[0] if images else {})
    return (
        value_from_mapping_or_string(image, "uri")
        or value_from_mapping_or_string(image, "resource_url")
        or payload.get("thumb")
        or selected.get("cover_image")
    )


def fetch_lastfm(conn: sqlite3.Connection, album_id: int, artist: str, album_name: str, refresh_cache: bool) -> None:
    existing = conn.execute(
        "SELECT lookup_status FROM external_metadata WHERE album_id = ? AND provider = ?",
        (album_id, "lastfm"),
    ).fetchone()
    if existing and existing["lookup_status"] == "matched" and not refresh_cache:
        return

    api_key = os.environ.get("LASTFM_API_KEY", "").strip()
    if not api_key:
        upsert_external_metadata(
            conn,
            album_id,
            {
                "provider": "lastfm",
                "lookup_status": "not_configured",
                "lookup_error": "Set LASTFM_API_KEY to enable Last.fm enrichment.",
                "fetched_at": utc_now(),
            },
        )
        upsert_service_status(conn, album_id, "lastfm", "not_configured", lookup_error="Set LASTFM_API_KEY to enable Last.fm enrichment.")
        return

    params = urllib.parse.urlencode(
        {
            "method": "album.getinfo",
            "artist": artist,
            "album": album_name,
            "api_key": api_key,
            "format": "json",
        }
    )
    url = f"https://ws.audioscrobbler.com/2.0/?{params}"
    payload, _, error = cached_json(conn, "lastfm", f"album:{artist}:{album_name}", url, refresh_cache=refresh_cache)
    album = (payload or {}).get("album") or {}
    status = "matched" if album else ("not_found" if not error or error == "not found" else "error")
    if existing and existing["lookup_status"] == "matched" and status == "error":
        return
    upsert_lastfm_album(conn, album_id, album, status, error, payload or {})


def upsert_lastfm_album(
    conn: sqlite3.Connection,
    album_id: int,
    album: dict,
    status: str,
    error: str | None,
    raw: dict | None = None,
) -> tuple[str | None, str | None]:
    tag_items = listify((album.get("tags") or {}).get("tag"))
    tags = [
        value_from_mapping_or_string(tag, "name")
        for tag in tag_items
        if value_from_mapping_or_string(tag, "name")
    ]
    images = listify(album.get("image"))
    cover_url = next((value_from_mapping_or_string(image, "#text") for image in reversed(images) if value_from_mapping_or_string(image, "#text")), None)
    tracks = listify((album.get("tracks") or {}).get("track"))
    upsert_external_metadata(
        conn,
        album_id,
        {
            "provider": "lastfm",
            "lookup_status": status,
            "lookup_error": error,
            "fetched_at": utc_now(),
            "external_id": album.get("mbid") or None,
            "url": album.get("url"),
            "title": album.get("name"),
            "artist": album.get("artist"),
            "genres": json.dumps(tags, ensure_ascii=False),
            "styles": None,
            "track_count": len(tracks),
            "cover_url": cover_url,
            "raw_json": json.dumps(raw or {}, ensure_ascii=False),
        },
    )
    upsert_service_status(
        conn,
        album_id,
        "lastfm",
        status,
        external_id=album.get("mbid") or None,
        title=album.get("name"),
        url=album.get("url"),
        lookup_error=error,
    )
    if album:
        replace_provider_cover_art(conn, album_id, "lastfm", cover_url, album.get("mbid") or str(album_id), album)
    return album.get("artist"), album.get("name")


def apple_artwork_url(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"/\d+x\d+bb\.", "/1200x1200bb.", value)


def apple_release_year(value: str | None) -> str | None:
    if not value:
        return None
    return value[:10]


def apple_album_title_score(result: dict, album_name: str) -> int:
    result_album = normalize_match_text(result.get("collectionName"))
    album_term = normalize_match_text(album_name)
    if not result_album or not album_term:
        return 0
    if result_album == album_term:
        return 100
    if result_album in album_term or album_term in result_album:
        return 45
    return 0


def apple_album_score(result: dict, artist: str, album_name: str) -> int:
    result_artist = normalize_match_text(result.get("artistName"))
    artist_term = normalize_match_text(artist)
    score = apple_album_title_score(result, album_name)
    if result_artist == artist_term:
        score += 70
    elif artist_term and (result_artist in artist_term or artist_term in result_artist):
        score += 25
    return score


def select_apple_album(results: list[dict], artist: str, album_name: str) -> dict | None:
    album_results = [item for item in results if item.get("wrapperType") == "collection" and item.get("collectionId")]
    title_matches = [item for item in album_results if apple_album_title_score(item, album_name) > 0]
    if not title_matches:
        return None
    selected = max(title_matches, key=lambda item: apple_album_score(item, artist, album_name))
    return selected if apple_album_score(selected, artist, album_name) >= 60 else None


def select_apple_album_from_song_results(results: list[dict], artist: str, album_name: str) -> dict | None:
    candidates = {}
    for item in results:
        if item.get("wrapperType") != "track" or not item.get("collectionId"):
            continue
        collection_id = item.get("collectionId")
        if collection_id in candidates:
            continue
        candidates[collection_id] = {
            "wrapperType": "collection",
            "collectionId": collection_id,
            "artistName": item.get("artistName"),
            "collectionName": item.get("collectionName"),
            "collectionViewUrl": item.get("collectionViewUrl"),
            "artworkUrl100": item.get("artworkUrl100"),
            "artworkUrl60": item.get("artworkUrl60"),
            "primaryGenreName": item.get("primaryGenreName"),
            "releaseDate": item.get("releaseDate"),
        }
    return select_apple_album(list(candidates.values()), artist, album_name)


def apple_track_rows(payload: dict) -> list[dict]:
    return [
        item
        for item in payload.get("results") or []
        if item.get("wrapperType") == "track" and item.get("kind") == "song" and item.get("trackName")
    ]


def apple_track_is_explicit(track: dict) -> bool:
    return (
        str(track.get("trackExplicitness") or "").casefold() == "explicit"
        or str(track.get("contentAdvisoryRating") or "").casefold() == "explicit"
    )


def apple_track_preview_url(track: dict) -> str | None:
    preview_url = (track.get("previewUrl") or "").strip()
    return preview_url or None


def track_match_keys(title: str | None) -> set[str]:
    value = title or ""
    keys = {normalize_match_text(value)}
    if " - " in value:
        keys.add(normalize_match_text(value.split(" - ", 1)[1]))
    return {key for key in keys if key}


def update_apple_track_previews(conn: sqlite3.Connection, album_id: int, apple_tracks: list[dict]) -> int:
    existing = conn.execute(
        "SELECT id, title FROM tracks WHERE album_id = ? ORDER BY medium_position, track_position, id",
        (album_id,),
    ).fetchall()
    apple_by_title: dict[str, dict] = {}
    for track in apple_tracks:
        key = normalize_match_text(track.get("trackName"))
        if key and apple_track_preview_url(track):
            apple_by_title[key] = track
    if not existing or not apple_by_title:
        return 0

    changed = 0
    for row in existing:
        apple_track = next((apple_by_title.get(key) for key in track_match_keys(row["title"]) if key in apple_by_title), None)
        if not apple_track:
            continue
        conn.execute("UPDATE tracks SET preview_url = ? WHERE id = ?", (apple_track_preview_url(apple_track), row["id"]))
        changed += 1
    return changed


def apply_apple_track_explicitness(
    conn: sqlite3.Connection,
    album_id: int,
    apple_tracks: list[dict],
    replace_if_empty: bool = True,
    replace_existing: bool = False,
) -> int:
    existing = conn.execute(
        "SELECT id, title FROM tracks WHERE album_id = ? ORDER BY medium_position, track_position, id",
        (album_id,),
    ).fetchall()
    apple_by_title: dict[str, dict] = {}
    for track in apple_tracks:
        key = normalize_match_text(track.get("trackName"))
        if key:
            apple_by_title[key] = track
    if not apple_by_title:
        return 0

    if existing and not replace_existing:
        changed = 0
        for row in existing:
            apple_track = next((apple_by_title.get(key) for key in track_match_keys(row["title"]) if key in apple_by_title), None)
            if not apple_track:
                continue
            explicit = 1 if apple_track_is_explicit(apple_track) else 0
            conn.execute(
                "UPDATE tracks SET explicit = ?, preview_url = COALESCE(?, preview_url) WHERE id = ?",
                (explicit, apple_track_preview_url(apple_track), row["id"]),
            )
            changed += 1
        return changed

    if existing and replace_existing:
        conn.execute("DELETE FROM tracks WHERE album_id = ?", (album_id,))
    elif not replace_if_empty:
        return 0

    rows = []
    for index, track in enumerate(apple_tracks, start=1):
        rows.append(
            (
                album_id,
                1,
                "",
                "",
                track.get("trackNumber") or index,
                str(track.get("trackNumber") or index),
                track.get("trackName"),
                track.get("trackTimeMillis"),
                1 if apple_track_is_explicit(track) else 0,
                apple_track_preview_url(track),
                f"apple_itunes:{track.get('trackId') or index}",
            )
        )
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO tracks (
            album_id, medium_position, medium_title, medium_format, track_position,
            track_number, title, length_ms, explicit, preview_url, recording_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def extract_html_meta_content(html_text: str, keys: list[str]) -> str | None:
    for key in keys:
        escaped_key = re.escape(key)
        patterns = [
            rf'<meta[^>]+(?:property|name)=["\']{escaped_key}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{escaped_key}["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
            if match:
                value = html.unescape(match.group(1)).strip()
                value = re.sub(r"\s+", " ", value)
                if value:
                    return value
    return None


def apple_album_description_from_page(
    conn: sqlite3.Connection,
    collection_id: str | None,
    url: str | None,
    refresh_cache: bool = False,
) -> str | None:
    if not collection_id or not url:
        return None
    cache_key = f"album-page:{collection_id}"
    payload, _, error = cached_text_metadata(conn, "apple_itunes", cache_key, url, refresh_cache=refresh_cache)
    if error or not payload:
        return None
    if payload.get("description"):
        return payload["description"]
    html_text = payload.get("html") or ""
    description = extract_html_meta_content(
        html_text,
        ["music:description", "og:description", "twitter:description", "description"],
    )
    if description:
        payload["description"] = description
        payload.pop("html", None)
        conn.execute(
            "UPDATE api_cache SET raw_json = ? WHERE provider = ? AND cache_key = ?",
            (json.dumps(payload, ensure_ascii=False), "apple_itunes", cache_key),
        )
    return description


def upsert_apple_album(
    conn: sqlite3.Connection,
    album_id: int,
    album: dict,
    lookup_payload: dict,
    tracks_payload: dict | None,
    refresh_cache: bool = False,
) -> tuple[str | None, str | None]:
    collection_id = album.get("collectionId")
    apple_tracks = apple_track_rows(tracks_payload or {})
    cover_url = apple_artwork_url(album.get("artworkUrl100") or album.get("artworkUrl60"))
    description = apple_album_description_from_page(
        conn,
        str(collection_id) if collection_id else None,
        album.get("collectionViewUrl"),
        refresh_cache=refresh_cache,
    )
    upsert_external_metadata(
        conn,
        album_id,
        {
            "provider": "apple_itunes",
            "lookup_status": "matched",
            "lookup_error": None,
            "fetched_at": utc_now(),
            "external_id": str(collection_id) if collection_id else None,
            "url": album.get("collectionViewUrl"),
            "title": album.get("collectionName"),
            "artist": album.get("artistName"),
            "genres": json.dumps([album.get("primaryGenreName")] if album.get("primaryGenreName") else [], ensure_ascii=False),
            "styles": None,
            "track_count": album.get("trackCount") or len(apple_tracks),
            "cover_url": cover_url,
            "raw_json": json.dumps(
                {"search": lookup_payload, "lookup": tracks_payload or {}, "description": description},
                ensure_ascii=False,
            ),
        },
    )
    upsert_service_status(
        conn,
        album_id,
        "apple_itunes",
        "matched",
        external_id=str(collection_id) if collection_id else None,
        title=album.get("collectionName"),
        url=album.get("collectionViewUrl"),
    )
    replace_provider_cover_art(conn, album_id, "apple_itunes", cover_url, str(collection_id) if collection_id else None, album)
    apply_apple_track_explicitness(conn, album_id, apple_tracks, replace_existing=True)
    return album.get("artistName"), album.get("collectionName")


def fetch_apple_itunes(conn: sqlite3.Connection, album_id: int, artist: str, album_name: str, refresh_cache: bool) -> None:
    existing = conn.execute(
        "SELECT lookup_status, external_id, title, raw_json FROM external_metadata WHERE album_id = ? AND provider = ?",
        (album_id, "apple_itunes"),
    ).fetchone()
    existing_title_matches = bool(
        existing
        and apple_album_title_score({"collectionName": existing["title"]}, album_name) > 0
    )
    if existing and existing["lookup_status"] == "matched" and not refresh_cache and existing_title_matches:
        try:
            payload = json.loads(existing["raw_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        tracks = apple_track_rows(payload.get("lookup") or {})
        if tracks:
            apply_apple_track_explicitness(conn, album_id, tracks, replace_existing=True)
            return
        if existing["external_id"]:
            fetch_apple_collection_id(conn, album_id, existing["external_id"], refresh_cache=False)
            return

    query = f"{artist} {album_name}".strip()
    params = urllib.parse.urlencode({"term": query, "media": "music", "entity": "album", "limit": "25", "country": "US"})
    url = f"https://itunes.apple.com/search?{params}"
    payload, _, error = cached_json(conn, "apple_itunes", f"album-search:{query}", url, refresh_cache=refresh_cache)
    results = (payload or {}).get("results") or []
    selected = select_apple_album(results, artist, album_name)
    if not selected and not error and album_name:
        song_params = urllib.parse.urlencode(
            {
                "term": album_name,
                "media": "music",
                "entity": "song",
                "attribute": "albumTerm",
                "limit": "50",
                "country": "US",
            }
        )
        song_url = f"https://itunes.apple.com/search?{song_params}"
        song_payload, _, song_error = cached_json(
            conn,
            "apple_itunes",
            f"album-song-search:{album_name}",
            song_url,
            refresh_cache=refresh_cache,
        )
        selected = select_apple_album_from_song_results((song_payload or {}).get("results") or [], artist, album_name)
        if selected and selected.get("collectionId"):
            fetch_apple_collection_id(conn, album_id, str(selected["collectionId"]), refresh_cache=refresh_cache)
            return
        if song_error:
            error = song_error
    if error or not selected:
        status = "not_found" if not error or error == "not found" else "error"
        upsert_external_metadata(
            conn,
            album_id,
            {
                "provider": "apple_itunes",
                "lookup_status": status,
                "lookup_error": error,
                "fetched_at": utc_now(),
                "raw_json": json.dumps(payload or {}, ensure_ascii=False),
            },
        )
        upsert_service_status(conn, album_id, "apple_itunes", status, lookup_error=error)
        return

    collection_id = selected.get("collectionId")
    lookup_payload = {}
    if collection_id:
        lookup_params = urllib.parse.urlencode({"id": collection_id, "entity": "song", "country": "US"})
        lookup_url = f"https://itunes.apple.com/lookup?{lookup_params}"
        lookup_payload, _, _ = cached_json(conn, "apple_itunes", f"collection:{collection_id}", lookup_url, refresh_cache=refresh_cache)
    upsert_apple_album(conn, album_id, selected, payload or {}, lookup_payload or {}, refresh_cache=refresh_cache)


def fetch_apple_collection_id(conn: sqlite3.Connection, album_id: int, collection_id: str, refresh_cache: bool) -> tuple[str | None, str | None]:
    lookup_params = urllib.parse.urlencode({"id": collection_id, "entity": "song", "country": "US"})
    lookup_url = f"https://itunes.apple.com/lookup?{lookup_params}"
    lookup_payload, _, error = cached_json(conn, "apple_itunes", f"collection:{collection_id}", lookup_url, refresh_cache=refresh_cache)
    if error or not lookup_payload:
        raise ValueError(error or "Apple iTunes album was not found.")
    album = next(
        (
            item
            for item in lookup_payload.get("results") or []
            if item.get("wrapperType") == "collection" and str(item.get("collectionId") or "") == str(collection_id)
        ),
        None,
    )
    if not album:
        raise ValueError("Apple iTunes album was not found.")
    return upsert_apple_album(conn, album_id, album, {"results": [album]}, lookup_payload, refresh_cache=refresh_cache)


def fetch_external_provider(
    conn: sqlite3.Connection,
    provider: str,
    album_id: int,
    artist: str,
    album_name: str,
    refresh_cache: bool,
) -> None:
    try:
        if provider == "discogs":
            fetch_discogs(conn, album_id, artist, album_name, refresh_cache)
        elif provider == "apple_itunes":
            fetch_apple_itunes(conn, album_id, artist, album_name, refresh_cache)
        elif provider == "lastfm":
            fetch_lastfm(conn, album_id, artist, album_name, refresh_cache)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        message = str(exc)
        upsert_external_metadata(
            conn,
            album_id,
            {
                "provider": provider,
                "lookup_status": "error",
                "lookup_error": message,
                "fetched_at": utc_now(),
            },
        )
        upsert_service_status(conn, album_id, provider, "error", lookup_error=message)


def enrich_musicbrainz_release_id(conn: sqlite3.Connection, album_id: int, release_id: str, refresh_cache: bool) -> tuple[str | None, str | None]:
    detail_payload, _, error = musicbrainz_get(
        conn,
        f"release/{release_id}",
        {
            "fmt": "json",
            "inc": "artist-credits+labels+release-groups+media+recordings+genres+tags",
        },
        f"release-detail:{release_id}",
        refresh_cache,
    )
    release = detail_payload or {}
    metadata = release_to_metadata(release if release else None, {"detail": detail_payload}, "matched" if release else "error", error)
    upsert_metadata(conn, album_id, metadata)
    upsert_service_status(
        conn,
        album_id,
        "musicbrainz",
        metadata["lookup_status"],
        external_id=metadata.get("mb_release_id"),
        title=metadata.get("title"),
        url=metadata.get("mb_url"),
        lookup_error=metadata.get("lookup_error"),
    )
    replace_tracks(conn, album_id, release if release else None)
    replace_musicbrainz_genres(conn, album_id, release if release else None)
    upsert_musicbrainz_external_metadata(conn, album_id, metadata)
    fetch_cover_art(conn, album_id, metadata.get("mb_release_id"), refresh_cache)
    return metadata.get("artist_credit"), metadata.get("title")


def enrich_musicbrainz_by_search(conn: sqlite3.Connection, album_id: int, artist: str, album_name: str, refresh_cache: bool) -> tuple[str | None, str | None]:
    release, raw, _ = find_release(conn, artist or "", album_name or "", refresh_cache)
    metadata = release_to_metadata(release, raw, "matched" if release else "not_found")
    upsert_metadata(conn, album_id, metadata)
    upsert_service_status(
        conn,
        album_id,
        "musicbrainz",
        metadata["lookup_status"],
        external_id=metadata.get("mb_release_id"),
        title=metadata.get("title"),
        url=metadata.get("mb_url"),
        lookup_error=metadata.get("lookup_error"),
    )
    replace_tracks(conn, album_id, release)
    replace_musicbrainz_genres(conn, album_id, release)
    upsert_musicbrainz_external_metadata(conn, album_id, metadata)
    fetch_cover_art(conn, album_id, metadata.get("mb_release_id"), refresh_cache)
    return metadata.get("artist_credit"), metadata.get("title")


def fetch_discogs_release_id(
    conn: sqlite3.Connection,
    album_id: int,
    discogs_id: str,
    refresh_cache: bool,
    replace_tracklist: bool = True,
) -> tuple[str | None, str | None]:
    token = os.environ.get("DISCOGS_TOKEN", "").strip()
    if not token:
        raise ValueError("Set DISCOGS_TOKEN to use a Discogs URL.")
    headers = {"Authorization": f"Discogs token={token}"}
    detail_url = f"https://api.discogs.com/releases/{discogs_id}"
    detail_payload, _, error = cached_json(conn, "discogs", f"release:{discogs_id}", detail_url, headers=headers, refresh_cache=refresh_cache)
    if error or not detail_payload:
        raise ValueError(error or "Discogs release was not found.")
    return upsert_discogs_release(conn, album_id, detail_payload, detail_payload, {"detail": detail_payload}, replace_tracklist=replace_tracklist)


def fetch_discogs_master_id(
    conn: sqlite3.Connection,
    album_id: int,
    master_id: str,
    refresh_cache: bool,
    replace_tracklist: bool = True,
) -> tuple[str | None, str | None]:
    token = os.environ.get("DISCOGS_TOKEN", "").strip()
    if not token:
        raise ValueError("Set DISCOGS_TOKEN to use a Discogs URL.")
    headers = {"Authorization": f"Discogs token={token}"}
    master_url = f"https://api.discogs.com/masters/{master_id}"
    master_payload, _, master_error = cached_json(conn, "discogs", f"master:{master_id}", master_url, headers=headers, refresh_cache=refresh_cache)
    if master_error or not master_payload:
        raise ValueError(master_error or "Discogs master was not found.")
    release_id = master_payload.get("main_release")
    if not release_id:
        raise ValueError("Discogs master does not have a main release.")
    detail_url = f"https://api.discogs.com/releases/{release_id}"
    detail_payload, _, detail_error = cached_json(conn, "discogs", f"release:{release_id}", detail_url, headers=headers, refresh_cache=refresh_cache)
    if detail_error or not detail_payload:
        raise ValueError(detail_error or "Discogs main release was not found.")
    return upsert_discogs_release(conn, album_id, detail_payload, detail_payload, {"master": master_payload, "detail": detail_payload}, replace_tracklist=replace_tracklist)


def fetch_lastfm_album_info(conn: sqlite3.Connection, album_id: int, artist: str, album_name: str, refresh_cache: bool) -> tuple[str | None, str | None]:
    api_key = os.environ.get("LASTFM_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Set LASTFM_API_KEY to use a Last.fm URL.")
    params = urllib.parse.urlencode(
        {
            "method": "album.getinfo",
            "artist": artist,
            "album": album_name,
            "api_key": api_key,
            "format": "json",
        }
    )
    url = f"https://ws.audioscrobbler.com/2.0/?{params}"
    payload, _, error = cached_json(conn, "lastfm", f"album:{artist}:{album_name}", url, refresh_cache=refresh_cache)
    album = (payload or {}).get("album") or {}
    if error or not album:
        raise ValueError(error or "Last.fm album was not found.")
    return upsert_lastfm_album(conn, album_id, album, "matched", None, payload or {})


def parse_music_service_url(service_url: str) -> tuple[str, dict[str, str]]:
    parsed = urllib.parse.urlparse((service_url or "").strip())
    host = parsed.netloc.casefold().removeprefix("www.")
    path_parts = [urllib.parse.unquote(part.replace("+", " ")) for part in parsed.path.split("/") if part]
    if host == "musicbrainz.org" and len(path_parts) >= 2 and path_parts[0] == "release":
        return "musicbrainz", {"release_id": path_parts[1]}
    if host == "discogs.com" and len(path_parts) >= 2 and path_parts[0] == "release":
        return "discogs", {"release_id": path_parts[1].split("-", 1)[0]}
    if host == "discogs.com" and len(path_parts) >= 2 and path_parts[0] == "master":
        return "discogs", {"master_id": path_parts[1].split("-", 1)[0]}
    if host == "last.fm" and len(path_parts) >= 3 and path_parts[0] == "music":
        artist = path_parts[1]
        album_name = path_parts[3] if len(path_parts) >= 4 and path_parts[2] in {"+albums", "_"} else path_parts[2]
        return "lastfm", {"artist": artist, "album_name": album_name}
    if host in {"music.apple.com", "itunes.apple.com"}:
        collection_id = next((part for part in reversed(path_parts) if part.isdigit()), "")
        if collection_id:
            return "apple_itunes", {"collection_id": collection_id}
    raise ValueError("Enter a MusicBrainz release URL, Discogs release URL, Apple Music album URL, or Last.fm album URL.")


def enrich_album_from_service_url(conn: sqlite3.Connection, album_id: int, service_url: str, refresh_cache: bool = False) -> dict:
    load_dotenv()
    album = conn.execute("SELECT artist, album_name FROM albums WHERE id = ?", (album_id,)).fetchone()
    if not album:
        raise ValueError("Album not found.")

    provider, data = parse_music_service_url(service_url)
    anchor_artist = album["artist"]
    anchor_title = album["album_name"]

    if provider == "musicbrainz":
        anchor_artist, anchor_title = enrich_musicbrainz_release_id(conn, album_id, data["release_id"], refresh_cache)
        if anchor_artist and anchor_title:
            fetch_discogs(conn, album_id, anchor_artist, anchor_title, refresh_cache, prefer_discogs_tracks=True)
            fetch_apple_itunes(conn, album_id, anchor_artist, anchor_title, refresh_cache)
            fetch_lastfm(conn, album_id, anchor_artist, anchor_title, refresh_cache)
    elif provider == "discogs":
        if data.get("master_id"):
            anchor_artist, anchor_title = fetch_discogs_master_id(conn, album_id, data["master_id"], refresh_cache)
        else:
            anchor_artist, anchor_title = fetch_discogs_release_id(conn, album_id, data["release_id"], refresh_cache)
        if anchor_artist and anchor_title:
            enrich_musicbrainz_by_search(conn, album_id, anchor_artist, anchor_title, refresh_cache)
            replace_tracks_from_cached_discogs_match(conn, album_id)
            fetch_apple_itunes(conn, album_id, anchor_artist, anchor_title, refresh_cache)
            fetch_lastfm(conn, album_id, anchor_artist, anchor_title, refresh_cache)
    elif provider == "lastfm":
        anchor_artist, anchor_title = fetch_lastfm_album_info(conn, album_id, data["artist"], data["album_name"], refresh_cache)
        if anchor_artist and anchor_title:
            enrich_musicbrainz_by_search(conn, album_id, anchor_artist, anchor_title, refresh_cache)
            fetch_discogs(conn, album_id, anchor_artist, anchor_title, refresh_cache, prefer_discogs_tracks=True)
            fetch_apple_itunes(conn, album_id, anchor_artist, anchor_title, refresh_cache)
    elif provider == "apple_itunes":
        anchor_artist, anchor_title = fetch_apple_collection_id(conn, album_id, data["collection_id"], refresh_cache)
        if anchor_artist and anchor_title:
            enrich_musicbrainz_by_search(conn, album_id, anchor_artist, anchor_title, refresh_cache)
            fetch_discogs(conn, album_id, anchor_artist, anchor_title, refresh_cache, prefer_discogs_tracks=True)
            fetch_apple_itunes(conn, album_id, anchor_artist, anchor_title, refresh_cache)
            fetch_lastfm(conn, album_id, anchor_artist, anchor_title, refresh_cache)

    fetch_lastfm_artist(conn, anchor_artist or album["artist"], refresh_cache)
    replace_tracks_from_cached_discogs_match(conn, album_id)
    if anchor_artist and anchor_title:
        fetch_apple_itunes(conn, album_id, anchor_artist, anchor_title, refresh_cache)
    update_master_catalog_fields(conn, album_id)
    services = conn.execute(
        """
        SELECT provider, lookup_status, found, external_id, title, url, lookup_error
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
    return {"provider": provider, "artist": anchor_artist, "album_name": anchor_title, "services": [dict(row) for row in services]}


def enrich_album_from_discogs_url(
    conn: sqlite3.Connection,
    album_id: int,
    service_url: str,
    refresh_cache: bool = False,
    include_related: bool = True,
) -> dict:
    load_dotenv()
    album = conn.execute("SELECT artist, album_name FROM albums WHERE id = ?", (album_id,)).fetchone()
    if not album:
        raise ValueError("Album not found.")

    provider, data = parse_music_service_url(service_url)
    if provider != "discogs":
        raise ValueError("Add Album can only load Discogs release or master URLs.")

    if data.get("master_id"):
        anchor_artist, anchor_title = fetch_discogs_master_id(conn, album_id, data["master_id"], refresh_cache)
    else:
        anchor_artist, anchor_title = fetch_discogs_release_id(conn, album_id, data["release_id"], refresh_cache)

    if include_related and anchor_artist and anchor_title:
        enrich_musicbrainz_by_search(conn, album_id, anchor_artist, anchor_title, refresh_cache)
        replace_tracks_from_cached_discogs_match(conn, album_id)
        fetch_apple_itunes(conn, album_id, anchor_artist, anchor_title, refresh_cache)
        fetch_lastfm(conn, album_id, anchor_artist, anchor_title, refresh_cache)
        fetch_lastfm_artist(conn, anchor_artist, refresh_cache)
    update_master_catalog_fields(conn, album_id)
    services = conn.execute(
        """
        SELECT provider, lookup_status, found, external_id, title, url, lookup_error
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
    return {"provider": provider, "artist": anchor_artist, "album_name": anchor_title, "services": [dict(row) for row in services]}


def parse_catalog_id_list(values: list[str] | None) -> list[str]:
    catalog_ids: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        for catalog_id in str(value).split(","):
            catalog_id = catalog_id.strip()
            if catalog_id and catalog_id not in seen:
                catalog_ids.append(catalog_id)
                seen.add(catalog_id)
    return catalog_ids


def enrich_album_rows(conn: sqlite3.Connection, rows: list[sqlite3.Row], refresh_cache: bool) -> None:
    for row in rows:
        album_id = row["id"]
        row_number = row["row_number"]
        artist = row["artist"]
        album_name = row["album_name"]
        used_musicbrainz_cache = True
        try:
            release, raw, used_musicbrainz_cache = find_release(conn, artist or "", album_name or "", refresh_cache)
            metadata = release_to_metadata(release, raw, "matched" if release else "not_found")
            upsert_metadata(conn, album_id, metadata)
            upsert_service_status(
                conn,
                album_id,
                "musicbrainz",
                metadata["lookup_status"],
                external_id=metadata.get("mb_release_id"),
                title=metadata.get("title"),
                url=metadata.get("mb_url"),
                lookup_error=metadata.get("lookup_error"),
            )
            track_count = replace_tracks(conn, album_id, release)
            genre_count = replace_musicbrainz_genres(conn, album_id, release)
            upsert_musicbrainz_external_metadata(conn, album_id, metadata)
            cover_count = fetch_cover_art(conn, album_id, metadata.get("mb_release_id"), refresh_cache)
            fetch_external_provider(conn, "discogs", album_id, artist or "", album_name or "", refresh_cache)
            fetch_external_provider(conn, "apple_itunes", album_id, artist or "", album_name or "", refresh_cache)
            fetch_external_provider(conn, "lastfm", album_id, artist or "", album_name or "", refresh_cache)
            fetch_lastfm_artist(conn, artist or "", refresh_cache)
            update_master_catalog_fields(conn, album_id)
            cache_note = "cache" if used_musicbrainz_cache else "api"
            print(
                f"{row_number:03d}: {artist} - {album_name} -> "
                f"{metadata.get('mb_release_id') or metadata['lookup_status']} "
                f"({track_count} tracks, {genre_count} genres/tags, {cover_count} covers, {cache_note})"
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            metadata = release_to_metadata(None, {}, "error", str(exc))
            upsert_metadata(conn, album_id, metadata)
            upsert_service_status(conn, album_id, "musicbrainz", "error", lookup_error=str(exc))
            upsert_musicbrainz_external_metadata(conn, album_id, metadata)
            fetch_lastfm_artist(conn, artist or "", refresh_cache)
            update_master_catalog_fields(conn, album_id)
            print(f"{row_number:03d}: {artist} - {album_name} -> error: {exc}")
        conn.commit()


def enrich_first_albums(conn: sqlite3.Connection, limit: int, offset: int, refresh_cache: bool) -> None:
    rows = conn.execute(
        """
        SELECT id, row_number, artist, album_name
        FROM albums
        ORDER BY row_number
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    enrich_album_rows(conn, rows, refresh_cache)


def refresh_catalog_ids(conn: sqlite3.Connection, catalog_ids: list[str]) -> None:
    if not catalog_ids:
        return

    placeholders = ", ".join("?" for _ in catalog_ids)
    order_cases = " ".join(f"WHEN ? THEN {index}" for index, _ in enumerate(catalog_ids))
    rows = conn.execute(
        f"""
        SELECT id, row_number, artist, album_name, catalog_number
        FROM albums
        WHERE catalog_number IN ({placeholders})
        ORDER BY CASE catalog_number {order_cases} ELSE {len(catalog_ids)} END, row_number
        """,
        [*catalog_ids, *catalog_ids],
    ).fetchall()

    found_ids = {row["catalog_number"] for row in rows}
    missing_ids = [catalog_id for catalog_id in catalog_ids if catalog_id not in found_ids]
    for catalog_id in missing_ids:
        print(f"1190_ID {catalog_id}: not found")

    enrich_album_rows(conn, rows, refresh_cache=True)


def apple_tracks_from_external_metadata(raw_json: str | None) -> list[dict]:
    if not raw_json:
        return []
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    return apple_track_rows(payload.get("lookup") or {})


def fetch_apple_preview_tracks(conn: sqlite3.Connection, artist: str, album_name: str, refresh_cache: bool) -> tuple[list[dict], str | None]:
    query = f"{artist} {album_name}".strip()
    params = urllib.parse.urlencode({"term": query, "media": "music", "entity": "album", "limit": "25", "country": "US"})
    url = f"https://itunes.apple.com/search?{params}"
    payload, _, error = cached_json(conn, "apple_itunes", f"album-search:{query}", url, refresh_cache=refresh_cache)
    selected = select_apple_album((payload or {}).get("results") or [], artist, album_name)

    if not selected and not error and album_name:
        song_params = urllib.parse.urlencode(
            {
                "term": album_name,
                "media": "music",
                "entity": "song",
                "attribute": "albumTerm",
                "limit": "50",
                "country": "US",
            }
        )
        song_url = f"https://itunes.apple.com/search?{song_params}"
        song_payload, _, song_error = cached_json(
            conn,
            "apple_itunes",
            f"album-song-search:{album_name}",
            song_url,
            refresh_cache=refresh_cache,
        )
        selected = select_apple_album_from_song_results((song_payload or {}).get("results") or [], artist, album_name)
        if song_error:
            error = song_error

    collection_id = selected.get("collectionId") if selected else None
    if not collection_id:
        return [], error or "not found"

    lookup_params = urllib.parse.urlencode({"id": collection_id, "entity": "song", "country": "US"})
    lookup_url = f"https://itunes.apple.com/lookup?{lookup_params}"
    lookup_payload, _, lookup_error = cached_json(
        conn,
        "apple_itunes",
        f"collection:{collection_id}",
        lookup_url,
        refresh_cache=refresh_cache,
    )
    return apple_track_rows(lookup_payload or {}), lookup_error


def scan_itunes_preview_links(conn: sqlite3.Connection, limit: int, offset: int, refresh_cache: bool) -> None:
    rows = conn.execute(
        """
        SELECT albums.id, albums.row_number, albums.artist, albums.album_name,
               external_metadata.raw_json AS apple_raw_json
        FROM albums
        LEFT JOIN external_metadata
          ON external_metadata.album_id = albums.id
         AND external_metadata.provider = 'apple_itunes'
         AND external_metadata.lookup_status = 'matched'
        ORDER BY albums.row_number
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()

    for row in rows:
        album_id = row["id"]
        row_number = row["row_number"]
        artist = row["artist"] or ""
        album_name = row["album_name"] or ""
        apple_tracks = apple_tracks_from_external_metadata(row["apple_raw_json"])
        if refresh_cache or not apple_tracks:
            apple_tracks, error = fetch_apple_preview_tracks(conn, artist, album_name, refresh_cache)
            if error and not apple_tracks:
                print(f"{row_number:03d}: {artist} - {album_name} -> preview scan {error}")
                conn.commit()
                continue

        changed = update_apple_track_previews(conn, album_id, apple_tracks)
        available = sum(1 for track in apple_tracks if apple_track_preview_url(track))
        conn.commit()
        print(f"{row_number:03d}: {artist} - {album_name} -> {changed} track previews linked ({available} Apple previews found)")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Import the CD catalog CSV into SQLite and optionally enrich albums from local/API metadata providers.")
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--enrich", type=int, default=0, help="Number of catalog rows to enrich.")
    parser.add_argument("--offset", type=int, default=0, help="Number of catalog rows to skip before enriching.")
    parser.add_argument("--scan-itunes-previews", type=int, default=0, metavar="N", help="Scan N catalog rows for cached iTunes track preview links, starting after --offset rows.")
    parser.add_argument("--refresh-cache", action="store_true", help="Ignore cached API payloads and fetch fresh copies.")
    parser.add_argument(
        "--refresh-cache-ids",
        nargs="+",
        metavar="1190_ID",
        help="Refresh cached API payloads and enrich only the albums with these 1190_ID/catalog_number values. Values may be space- or comma-separated.",
    )
    args = parser.parse_args()
    if args.enrich < 0:
        parser.error("--enrich must be 0 or greater.")
    if args.offset < 0:
        parser.error("--offset must be 0 or greater.")
    if args.scan_itunes_previews < 0:
        parser.error("--scan-itunes-previews must be 0 or greater.")
    refresh_cache_ids = parse_catalog_id_list(args.refresh_cache_ids)

    db_exists = args.db.exists()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.db, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000) as conn:
        conn.row_factory = sqlite3.Row
        configure_sqlite_connection(conn)
        if db_exists:
            ensure_track_preview_schema(conn)
            sanitize_cached_urls(conn)
            conn.commit()
        else:
            rows = read_catalog_rows(args.csv)
            create_schema(conn)
            ensure_track_preview_schema(conn)
            sanitize_cached_urls(conn)
            import_catalog(conn, rows)
            conn.commit()
        if args.enrich:
            enrich_first_albums(conn, args.enrich, args.offset, args.refresh_cache)
        if refresh_cache_ids:
            refresh_catalog_ids(conn, refresh_cache_ids)
        if args.scan_itunes_previews:
            scan_itunes_preview_links(conn, args.scan_itunes_previews, args.offset, args.refresh_cache)
    if db_exists:
        print(f"Updated existing database at {args.db}")
    else:
        print(f"Wrote {len(rows)} catalog rows to {args.db}")


if __name__ == "__main__":
    main()
