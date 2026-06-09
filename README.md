# Radio 1190 Music Archive

A local SQLite-backed browser for the Radio 1190 CD catalog. The app imports `data/CD Catalog.csv`, enriches catalog rows with MusicBrainz, Discogs, and Last.fm metadata, caches API responses locally, downloads usable cover/artist images, and serves a lightweight web interface at `http://127.0.0.1:8190`.

## What It Stores

The original spreadsheet remains the source of truth for supplied catalog rows. The SQLite database extends those rows with a master album record assembled from external services in this priority order:

1. MusicBrainz
2. Discogs
3. Last.fm

The master album fields currently filled from those services are:

- `label`
- `country`
- `released`
- `genre`
- `compilation`

`format` is catalog-supplied only. The current catalog assumes imported spreadsheet items are CDs, and the web Add/Edit form treats `format` as the single user-facing format field. External services can be used to check whether the supplied format appears in API data, but they do not overwrite the catalog format.

Every album keeps normalized provider records in `external_metadata`, one row per matched or attempted service. MusicBrainz, Discogs, and Last.fm are peers in that table. Some services also populate service-specific helper tables when they expose richer data; for example, `musicbrainz_metadata` stores detailed MusicBrainz release fields, and Discogs/MusicBrainz can both populate `tracks`.

`Various`, `V/A`, and `VA` are normalized to the special display value `Various Artists`. Compilation rows suppress artist profile display, and compilation tracks rendered as `Artist - Song` make the track artist clickable for an artist search.

## Local Files

| Path | Purpose |
| --- | --- |
| `data/CD Catalog.csv` | Source spreadsheet export. |
| `data/cd_catalog.sqlite` | Generated SQLite database used by the app. |
| `web/` | Static frontend files served by `app.py`. |
| `web/covers/` | Locally cached album cover images. |
| `web/artist-images/` | Locally cached Last.fm artist images. |
| `.env` | Local API tokens. This file should not be committed. |
| `.env.example` | Template showing supported token names. |
| `scripts/build_database.py` | CSV import, schema creation, API enrichment, cache writes, and manual URL enrichment helpers. |
| `app.py` | Local HTTP server and JSON API. |

## Environment

Create `.env` from `.env.example` and add the tokens you have:

```sh
DISCOGS_TOKEN=your_discogs_token
LASTFM_API_KEY=your_lastfm_api_key
```

MusicBrainz does not require a token, but the script identifies itself with a user agent and rate-limits normal MusicBrainz lookups.

## Build The Database

Import the CSV only:

```sh
python3 scripts/build_database.py
```

Import the CSV and enrich the first 50 catalog rows:

```sh
python3 scripts/build_database.py --enrich 50
```

The script uses the `api_cache` table so repeated runs reuse cached API payloads unless `--refresh-cache` is supplied.

## Run The App

```sh
python3 app.py
```

Open:

```text
http://127.0.0.1:8190
```

The default port is `8190`. Override it with `PORT=...` if needed.

## Debian Service

Install and start a systemd service:

```sh
sudo scripts/install_debian_service.sh
```

Restart it after changes:

```sh
sudo scripts/restart_debian_service.sh
```

Both scripts use `SERVICE_NAME=radio1190-archive` and `PORT=8190` by default. Set `APP_DIR`, `RUN_USER`, `PYTHON_BIN`, `SERVICE_NAME`, or `PORT` before running the install script to override those values.

## Manual Music Service Matching

If a selected album has no matched music services, the sidebar shows a `Music Service URL` field. Paste one of these:

- MusicBrainz release URL, for example `https://musicbrainz.org/release/<release-id>`
- Discogs release URL, for example `https://www.discogs.com/release/<release-id>-...`
- Discogs master URL, for example `https://www.discogs.com/master/<master-id>-...`
- Last.fm album URL, for example `https://www.last.fm/music/<artist>/<album>`

When submitted, the supplied URL becomes the anchor match for that album. Discogs master URLs are resolved through the master record's `main_release`. The app then uses the artist/title from that service to look up the same album in the other two services, stores the results in SQLite, and refreshes the sidebar.

## Add And Edit Albums

Use the header `Add` button to add a new album manually. The Add form can load album data from a Discogs release or master URL only. This keeps new user-created records anchored to Discogs and avoids broad multi-service searching during entry. Existing unmatched albums can still use the sidebar `Music Service URL` field with MusicBrainz, Discogs, or Last.fm URLs.

Album detail views include `Edit` and `Delete` buttons. Edit updates the catalog fields stored on `albums`; Delete removes the album and its dependent cached metadata through SQLite foreign-key cascades.

The Add/Edit form includes:

- Spreadsheet/catalog fields such as timestamp, `1190_ID`, artist, album title, version, case status, notes, and other. New albums pre-populate timestamp with the current local date/time.
- Extended catalog fields: label, format, compilation, country, released, and genre.
- Optional album cover upload.

`RateYourMusic` remains stored from the original spreadsheet and appears in read-only album details, but it is not shown in Add/Edit because it is not currently used for enrichment or lookups.

## Web Features

- Click an artist name to show all releases by that artist.
- Click a genre/tag chip to filter by that tag only.
- Click genre/style chips inside Music Services boxes to filter by that tag only.
- Click the underlined `genres/tags` count in the header to open the tag cloud.
- Click any tag in the tag cloud to filter by that tag only.
- Click a record label to show all releases from that label.
- `Hide N/A albums` defaults on and hides rows where both artist and album are `N/A`.
- The `Music Service` column lists the services matched for each album.
- The catalog `format` value is highlighted when cached API formats do not include that format.
- `Add` opens the Add Album form.
- Album details include `Edit` and `Delete`.
- Compilation track artists are clickable when a track is displayed as `Artist - Song`.
- Album covers and artist images open in a lightbox.
- Artist images and Last.fm bios appear at the bottom of the sidebar when available; long bios are truncated with a full-bio link.
- Apple/iTunes preview code is present but currently disabled.

## Database ERD

`external_metadata` is the normalized music-service table. MusicBrainz, Discogs, and Last.fm are all represented there as peer providers. `musicbrainz_metadata` is shown separately only because the app still keeps a MusicBrainz-specific release detail cache for richer fields from earlier builds; it is not the master service model.

```mermaid
erDiagram
    ALBUMS {
        integer id PK
        integer row_number
        text timestamp
        text catalog_number
        text media_format
        text artist
        text album_name
        text label
        text format
        integer compilation
        text country
        text released
        text genre
        text field_sources
        text version_number
        text case_broken
        text label_number_missing
        text notes
        text rateyourmusic
        text other
        text source_json
    }

    ARTISTS {
        integer id PK
        text name UK
        text lookup_status
        text lookup_error
        text fetched_at
        text lastfm_mbid
        text lastfm_url
        text bio_summary
        text bio_content
        text image_url
        text local_image_url
        text raw_json
    }

    MUSICBRAINZ_METADATA {
        integer album_id PK
        text lookup_status
        text lookup_error
        text fetched_at
        text mb_release_id
        text title
        text artist_credit
        text date
        text country
        text status
        text barcode
        text asin
        text release_group_id
        text release_group_primary_type
        text release_group_secondary_types
        text label_names
        text catalog_numbers
        text format
        integer track_count
        integer score
        text disambiguation
        text mb_url
        text raw_json
    }

    TRACKS {
        integer id PK
        integer album_id FK
        integer medium_position
        text medium_title
        text medium_format
        integer track_position
        text track_number
        text title
        integer length_ms
        text recording_id
    }

    ALBUM_GENRES {
        integer id PK
        integer album_id FK
        text source
        text name
        integer count
    }

    COVER_ART {
        integer id PK
        integer album_id FK
        text source
        text image_id
        text types
        integer is_front
        integer is_back
        integer approved
        text image_url
        text thumbnail_small
        text thumbnail_large
        text local_image_url
        text comment
        text raw_json
    }

    ALBUM_SERVICE_STATUS {
        integer album_id PK
        text provider PK
        text lookup_status
        integer found
        text fetched_at
        text external_id
        text title
        text url
        text lookup_error
    }

    EXTERNAL_METADATA {
        integer id PK
        integer album_id FK
        text provider
        text lookup_status
        text lookup_error
        text fetched_at
        text external_id
        text url
        text title
        text artist
        text genres
        text styles
        integer track_count
        text cover_url
        text raw_json
    }

    API_CACHE {
        text provider PK
        text cache_key PK
        text url
        integer status_code
        text fetched_at
        text raw_json
        text error
    }

    ARTISTS ||--o{ ALBUMS : "matches by artist name"
    ALBUMS ||--o| MUSICBRAINZ_METADATA : "has MusicBrainz detail cache"
    ALBUMS ||--o{ TRACKS : "has"
    ALBUMS ||--o{ ALBUM_GENRES : "has"
    ALBUMS ||--o{ COVER_ART : "has"
    ALBUMS ||--o{ ALBUM_SERVICE_STATUS : "tracks service status"
    ALBUMS ||--o{ EXTERNAL_METADATA : "has normalized service metadata"
```

## Data And Asset Storage

```mermaid
flowchart LR
    CSV["data/CD Catalog.csv"] --> Builder["scripts/build_database.py"]
    Env[".env tokens"] --> Builder

    Builder --> DB[("data/cd_catalog.sqlite")]
    Builder --> Covers["web/covers/"]
    Builder --> ArtistImages["web/artist-images/"]

    MusicBrainz["MusicBrainz API"] --> Builder
    Discogs["Discogs API"] --> Builder
    LastFM["Last.fm API"] --> Builder
    CoverArchive["Cover Art Archive"] --> Builder

    DB --> App["app.py JSON API"]
    Covers --> App
    ArtistImages --> App
    Web["web/index.html + app.js + styles.css"] --> Browser["Browser UI"]
    App --> Browser

    Browser --> ManualURL["Sidebar Music Service URL"]
    Browser --> AddDiscogs["Add form Discogs URL"]
    ManualURL --> App
    AddDiscogs --> App
    App --> Builder
```

## API Endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/albums` | `GET` | List albums with optional filters: `q`, `tag`, `artist`, `label`, `hide_na`, `enriched`, `limit`, `offset`. |
| `/api/albums` | `POST` | Create an album from Add form fields, optional Discogs URL, and optional cover upload. |
| `/api/albums/<id>` | `GET` | Return one album, service metadata, tracks, genres, covers, and artist profile. |
| `/api/albums/<id>` | `PUT` | Update an album from Edit form fields and optional cover upload. |
| `/api/albums/<id>` | `DELETE` | Delete an album and dependent cached rows. |
| `/api/music-service-preview` | `POST` | Preview Discogs release/master metadata for the Add form without creating a catalog row. |
| `/api/albums/<id>/music-service-url` | `POST` | Submit a MusicBrainz, Discogs, or Last.fm album URL for an unmatched album. |
| `/api/stats` | `GET` | Return high-level catalog stats. |
| `/api/tags` | `GET` | Return tag-cloud data from cached MusicBrainz, Discogs, and Last.fm genre/tag/style records. |

## Notes

- `api_cache` stores raw JSON responses keyed by provider and request, so routine rebuilds avoid unnecessary API calls.
- `external_metadata` stores normalized provider records for MusicBrainz, Discogs, and Last.fm.
- `musicbrainz_metadata` is a service-specific detail/cache table for MusicBrainz release fields. It does not make MusicBrainz the master service model; MusicBrainz also has a normalized row in `external_metadata` like Discogs and Last.fm.
- `tracks` can be populated from MusicBrainz or Discogs. Discogs compilation tracks are stored as `Artist - Song` so the UI can make the artist portion searchable.
- `album_service_status` records whether each service was found, not found, errored, or not configured.
- Album art and artist images are stored as local files and referenced by local web paths.
- The app is intended for local use and does not implement authentication.
