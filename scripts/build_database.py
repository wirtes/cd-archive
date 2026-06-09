#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "CD Catalog.csv"
DB_PATH = ROOT / "data" / "cd_catalog.sqlite"
COVER_DIR = ROOT / "web" / "covers"

USER_AGENT = "cd-archive/1.0 (local catalog enrichment; https://musicbrainz.org/doc/MusicBrainz_API)"
MUSICBRAINZ_DELAY_SECONDS = 1.1
ENV_PATH = ROOT / ".env"


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
            version_number TEXT,
            case_broken TEXT,
            label_number_missing TEXT,
            notes TEXT,
            rateyourmusic TEXT,
            other TEXT,
            source_json TEXT NOT NULL
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
        CREATE INDEX idx_musicbrainz_release ON musicbrainz_metadata(mb_release_id);
        CREATE INDEX idx_tracks_album ON tracks(album_id);
        CREATE INDEX idx_genres_album ON album_genres(album_id);
        CREATE INDEX idx_cover_art_album ON cover_art(album_id);
        CREATE INDEX idx_service_status_album ON album_service_status(album_id);
        CREATE INDEX idx_external_album ON external_metadata(album_id);
        """
    )


def read_catalog_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def import_catalog(conn: sqlite3.Connection, rows: list[dict[str, str]]) -> None:
    insert_sql = """
        INSERT INTO albums (
            row_number, timestamp, catalog_number, media_format, artist, album_name, version_number,
            case_broken, label_number_missing, notes, rateyourmusic, other, source_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        "label_names": first_joined([(info.get("label") or {}).get("name") for info in label_info]),
        "catalog_numbers": first_joined([info.get("catalog-number") for info in label_info]),
        "format": first_joined([medium.get("format") for medium in media]),
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
                    recording.get("id"),
                )
            )
    conn.executemany(
        """
        INSERT INTO tracks (
            album_id, medium_position, medium_title, medium_format,
            track_position, track_number, title, length_ms, recording_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def fetch_discogs(conn: sqlite3.Connection, album_id: int, artist: str, album_name: str, refresh_cache: bool) -> None:
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
    params = urllib.parse.urlencode({"q": query, "type": "release", "format": "CD", "per_page": "1"})
    search_url = f"https://api.discogs.com/database/search?{params}"
    search_payload, _, search_error = cached_json(conn, "discogs", f"search:{query}", search_url, headers=headers, refresh_cache=refresh_cache)
    results = (search_payload or {}).get("results") or []
    if search_error or not results:
        status = "not_found" if not search_error or search_error == "not found" else "error"
        upsert_external_metadata(
            conn,
            album_id,
            {
                "provider": "discogs",
                "lookup_status": status,
                "lookup_error": search_error,
                "fetched_at": utc_now(),
                "raw_json": json.dumps(search_payload or {}, ensure_ascii=False),
            },
        )
        upsert_service_status(conn, album_id, "discogs", status, lookup_error=search_error)
        return

    selected = results[0]
    discogs_id = selected.get("id")
    detail_payload = {}
    if discogs_id:
        detail_url = f"https://api.discogs.com/releases/{discogs_id}"
        detail_payload, _, _ = cached_json(conn, "discogs", f"release:{discogs_id}", detail_url, headers=headers, refresh_cache=refresh_cache)

    payload = detail_payload or selected
    cover_url = discogs_cover_url(payload, selected)
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
            "artist": first_joined([artist.get("name") for artist in payload.get("artists", [])]) if payload.get("artists") else None,
            "genres": json.dumps(payload.get("genres") or selected.get("genre") or [], ensure_ascii=False),
            "styles": json.dumps(payload.get("styles") or selected.get("style") or [], ensure_ascii=False),
            "track_count": len(payload.get("tracklist") or []),
            "cover_url": cover_url,
            "raw_json": json.dumps({"search": search_payload, "detail": detail_payload}, ensure_ascii=False),
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


def discogs_cover_url(payload: dict, selected: dict) -> str | None:
    images = payload.get("images") or []
    front = next((image for image in images if image.get("type") == "primary"), None)
    image = front or (images[0] if images else {})
    return image.get("uri") or image.get("resource_url") or payload.get("thumb") or selected.get("cover_image")


def fetch_lastfm(conn: sqlite3.Connection, album_id: int, artist: str, album_name: str, refresh_cache: bool) -> None:
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
    tags = [tag.get("name") for tag in ((album.get("tags") or {}).get("tag") or []) if tag.get("name")]
    images = album.get("image") or []
    cover_url = next((image.get("#text") for image in reversed(images) if image.get("#text")), None)
    status = "matched" if album else ("not_found" if not error or error == "not found" else "error")
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
            "track_count": len(((album.get("tracks") or {}).get("track") or [])),
            "cover_url": cover_url,
            "raw_json": json.dumps(payload or {}, ensure_ascii=False),
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


def enrich_first_albums(conn: sqlite3.Connection, limit: int, refresh_cache: bool) -> None:
    rows = conn.execute(
        """
        SELECT id, row_number, artist, album_name
        FROM albums
        ORDER BY row_number
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    for offset, row in enumerate(rows):
        album_id, row_number, artist, album_name = row
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
            cover_count = fetch_cover_art(conn, album_id, metadata.get("mb_release_id"), refresh_cache)
            fetch_external_provider(conn, "discogs", album_id, artist or "", album_name or "", refresh_cache)
            fetch_external_provider(conn, "lastfm", album_id, artist or "", album_name or "", refresh_cache)
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
            print(f"{row_number:03d}: {artist} - {album_name} -> error: {exc}")
        conn.commit()
        if not used_musicbrainz_cache and offset != len(rows) - 1:
            time.sleep(MUSICBRAINZ_DELAY_SECONDS)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Import the CD catalog CSV into SQLite and optionally enrich albums from local/API metadata providers.")
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--enrich", type=int, default=0, help="Number of leading catalog rows to enrich.")
    parser.add_argument("--refresh-cache", action="store_true", help="Ignore cached API payloads and fetch fresh copies.")
    args = parser.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    rows = read_catalog_rows(args.csv)
    with sqlite3.connect(args.db) as conn:
        create_schema(conn)
        sanitize_cached_urls(conn)
        import_catalog(conn, rows)
        conn.commit()
        if args.enrich:
            enrich_first_albums(conn, args.enrich, args.refresh_cache)
    print(f"Wrote {len(rows)} catalog rows to {args.db}")


if __name__ == "__main__":
    main()
