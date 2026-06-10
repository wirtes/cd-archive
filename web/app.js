const state = {
  q: "",
  tag: "",
  artist: "",
  label: "",
  hideNa: true,
  searchTracks: false,
  limit: 50,
  offset: 0,
  total: 0,
  selectedId: null,
  username: "",
  roles: { admin: false, editor: false },
};

const MUSIC_PREVIEWS_ENABLED = false;

const previewState = {
  audio: new Audio(),
  button: null,
};
const previewCache = new Map();
const albumPreviewCache = new Map();

const rowsEl = document.querySelector("#albumRows");
const tableShell = document.querySelector(".tableShell");
const detailEl = document.querySelector("#detailPane");
const pageLabel = document.querySelector("#pageLabel");
const prevPage = document.querySelector("#prevPage");
const nextPage = document.querySelector("#nextPage");
const searchForm = document.querySelector("#searchForm");
const searchInput = document.querySelector("#searchInput");
const hideNaInput = document.querySelector("#hideNaInput");
const searchTracksInput = document.querySelector("#searchTracksInput");
const addAlbumButton = document.querySelector("#addAlbumButton");
const adminLink = document.querySelector("#adminLink");
const sessionUser = document.querySelector("#sessionUser");
const accountMenuButton = document.querySelector("#accountMenuButton");
const accountMenu = document.querySelector("#accountMenu");
const logoutButton = document.querySelector("#logoutButton");
const statsEl = document.querySelector("#stats");
const lightboxEl = document.querySelector("#imageLightbox");
const lightboxImageEl = document.querySelector("#lightboxImage");
const lightboxCloseEl = document.querySelector("#lightboxClose");
let addCoverDataUrl = "";
let currentDetailPayload = null;

function canEditCatalog() {
  return Boolean(state.roles.admin || state.roles.editor);
}

async function loadSession() {
  const response = await fetch("/api/session");
  if (response.status === 401) {
    window.location.href = "/login.html";
    return;
  }
  const payload = await response.json();
  state.username = payload.username || "";
  state.roles = payload.roles || state.roles;
  if (sessionUser) sessionUser.textContent = state.username || "";
  addAlbumButton.hidden = !canEditCatalog();
  adminLink.hidden = !state.roles.admin;
}

async function logout() {
  await fetch("/api/logout", { method: "POST" });
  window.location.href = "/login.html";
}

function setAccountMenuOpen(open) {
  if (!accountMenu || !accountMenuButton) return;
  accountMenu.hidden = !open;
  accountMenuButton.setAttribute("aria-expanded", open ? "true" : "false");
}

function toggleAccountMenu() {
  setAccountMenuOpen(Boolean(accountMenu?.hidden));
}

function text(value) {
  return value === null || value === undefined || value === "" ? "—" : value;
}

function escapeHtml(value) {
  return String(text(value))
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeRaw(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeRaw(value);
}

function parseList(value) {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.filter(Boolean) : [];
  } catch {
    return String(value)
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }
}

function sourceLabel(value) {
  const labels = {
    musicbrainz: "MusicBrainz",
    discogs: "Discogs",
    apple_itunes: "Apple",
    lastfm: "Last.fm",
  };
  return labels[value] || value || "";
}

function normalizeArtistName(value) {
  return String(value || "").replace(/\s*\(\d+\)\s*$/, "").replace(/\s+/g, " ").trim();
}

function isVariousArtist(value) {
  return ["various", "various artists"].includes(normalizeArtistName(value).toLowerCase());
}

function renderArtistName(value, className = "") {
  if (isVariousArtist(value)) {
    return `<span class="${escapeAttribute(className)}">${escapeHtml("Various Artists")}</span>`;
  }
  return `<button class="artistLink ${escapeAttribute(className)}" type="button" data-artist="${escapeAttribute(value)}">${escapeHtml(value)}</button>`;
}

function renderTrackTitle(album, title) {
  const value = String(title || "");
  if (!album.compilation) {
    return escapeHtml(value);
  }
  const separator = value.indexOf(" - ");
  if (separator <= 0) {
    return escapeHtml(value);
  }
  const artist = value.slice(0, separator).trim();
  const song = value.slice(separator + 3).trim();
  if (!artist || !song) {
    return escapeHtml(value);
  }
  return `${renderArtistName(artist, "inline trackArtistLink")} <span class="trackSeparator">-</span> ${escapeHtml(song)}`;
}

function stripTags(value) {
  if (!value) return "";
  const template = document.createElement("template");
  template.innerHTML = value;
  return template.content.textContent.trim();
}

function truncateWords(value, limit) {
  const words = value.trim().split(/\s+/).filter(Boolean);
  if (words.length <= limit) {
    return { text: value, truncated: false };
  }
  return { text: `${words.slice(0, limit).join(" ")}...`, truncated: true };
}

function formatDuration(ms) {
  if (!ms) return "";
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function currentTimestampValue() {
  const date = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function formatOptionValue(value) {
  const normalized = String(value || "").trim().toLowerCase();
  const options = {
    cd: "CD",
    vinyl: "Vinyl",
    cassette: "Cassette",
    digital: "Digital",
  };
  return options[normalized] || value || "CD";
}

function normalizePreviewText(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function previewSearchTerms(album, track) {
  const terms = [
    [album.artist, album.album_name, track.title],
    [album.artist, track.title],
    [track.title, album.artist],
    [album.album_name, track.title],
  ]
    .map((parts) => parts.filter(Boolean).join(" ").trim())
    .filter(Boolean);
  return [...new Set(terms)];
}

function albumPreviewKey(album) {
  return `${album.artist}|${album.album_name}`;
}

function albumPreviewTerm(album) {
  const artist = album.artist || "";
  const albumName = album.album_name || "";
  return normalizePreviewText(artist) === normalizePreviewText(albumName)
    ? artist || albumName
    : [artist, albumName].filter(Boolean).join(" ");
}

function appleAlbumFromServices(album, external) {
  const providerOrder = ["apple_itunes", "discogs", "lastfm", "musicbrainz"];
  for (const provider of providerOrder) {
    const match = external.find(
      (source) =>
        source.provider === provider &&
        source.lookup_status === "matched" &&
        (source.artist || source.title),
    );
    if (match) {
      return {
        artist: match.artist || album.artist,
        album_name: match.title || album.album_name,
      };
    }
  }
  return { artist: album.artist, album_name: album.album_name };
}

function albumPreviewScore(result, album) {
  const resultAlbum = normalizePreviewText(result.collectionName);
  const resultTitle = normalizePreviewText(result.trackName);
  const resultArtist = normalizePreviewText(result.artistName || result.collectionArtistName);
  const albumName = normalizePreviewText(album.album_name);
  const artist = normalizePreviewText(album.artist);
  if (!albumName) return -1;

  let score = 0;
  if (resultAlbum && resultAlbum === albumName) {
    score += 100;
  } else if (resultAlbum && (resultAlbum.includes(albumName) || albumName.includes(resultAlbum))) {
    score += 60;
  } else if (resultTitle && (resultTitle === albumName || resultTitle.includes(albumName) || albumName.includes(resultTitle))) {
    score += 60;
  } else {
    return -1;
  }

  if (artist && resultArtist === artist) {
    score += 80;
  } else if (artist && (resultArtist.includes(artist) || artist.includes(resultArtist))) {
    score += 45;
  } else if (artist) {
    score -= 30;
  }

  return score;
}

function previewScore(result, album, track) {
  if (!result.previewUrl) return -1;
  const resultTitle = normalizePreviewText(result.trackName);
  const resultArtist = normalizePreviewText(result.artistName);
  const resultAlbum = normalizePreviewText(result.collectionName);
  const title = normalizePreviewText(track.title);
  const artist = normalizePreviewText(album.artist);
  const albumName = normalizePreviewText(album.album_name);
  if (!title) return -1;

  let score = 0;
  if (resultTitle === title) {
    score += 100;
  } else if (resultTitle.includes(title) || title.includes(resultTitle)) {
    score += 60;
  } else {
    const titleWords = title.split(" ").filter((word) => word.length > 2);
    const matchingWords = titleWords.filter((word) => resultTitle.includes(word)).length;
    if (!matchingWords) return -1;
    score += matchingWords * 8;
  }

  if (artist && resultArtist === artist) {
    score += 80;
  } else if (artist && (resultArtist.includes(artist) || artist.includes(resultArtist))) {
    score += 45;
  } else if (artist) {
    score -= 35;
  }

  if (albumName && resultAlbum === albumName) {
    score += 25;
  } else if (albumName && (resultAlbum.includes(albumName) || albumName.includes(resultAlbum))) {
    score += 15;
  }

  return score;
}

function appleSearch(params) {
  return fetch(`https://itunes.apple.com/search?${params}`).then((response) => {
    if (!response.ok) throw new Error("Apple lookup failed.");
    return response.json();
  });
}

async function loadStats() {
  const response = await fetch("/api/stats");
  const stats = await response.json();
  statsEl.innerHTML = `
    <span class="statsLine">${stats.albums.toLocaleString()} albums · ${stats.enriched.toLocaleString()} with source data · ${stats.tracks.toLocaleString()} tracks</span>
    <span class="statsLine">${stats.matched.toLocaleString()} matched albums · <button class="statsLink" type="button" id="tagCloudButton">${stats.genres.toLocaleString()} genres/tags</button></span>
  `;
}

async function loadAlbums(options = {}) {
  const params = new URLSearchParams({
    q: state.q,
    tag: state.tag,
    artist: state.artist,
    label: state.label,
    hide_na: state.hideNa ? "1" : "0",
    search_tracks: state.searchTracks ? "1" : "0",
    limit: state.limit,
    offset: state.offset,
  });
  const response = await fetch(`/api/albums?${params}`);
  const payload = await response.json();
  state.total = payload.total;
  renderRows(payload.albums);
  renderPager();
  if (options.scrollToTop && tableShell) {
    tableShell.scrollTop = 0;
  }
}

function renderRows(albums) {
  rowsEl.innerHTML = albums
    .map((album) => {
      const services = album.matched_services ? album.matched_services.split(",").map((service) => service.trim()).filter(Boolean) : [];
      const serviceText = services.length ? services.map(sourceLabel).join(", ") : "Not found";
      const badgeClass = services.length ? "badge" : "badge missing";
      const formatClass = album.format_matches_api ? "" : "formatMismatch";
      const formatTitle =
        album.format_matches_api || !album.api_formats?.length
          ? ""
          : `Catalog format not found in API formats: ${album.api_formats.join(", ")}`;
      return `
        <tr data-id="${album.id}" class="${album.id === state.selectedId ? "selected" : ""}">
          <td>${escapeHtml(album.row_number)}</td>
          <td>
            ${renderArtistName(album.artist)}
            <div class="subtle radioId">1190_ID: ${escapeHtml(album.catalog_number)}</div>
          </td>
          <td>
            <div>${escapeHtml(album.album_name)}</div>
            ${album.label ? `<button class="labelLink metaLine" type="button" data-label="${escapeAttribute(album.label)}">${escapeHtml(album.label)}</button>` : ""}
          </td>
          <td><span class="${formatClass}" title="${escapeAttribute(formatTitle)}">${escapeHtml(album.format || album.media_format)}</span></td>
          <td><span class="${badgeClass}">${escapeHtml(serviceText)}</span></td>
        </tr>
      `;
    })
    .join("");
}

function renderPager() {
  const start = state.total === 0 ? 0 : state.offset + 1;
  const end = Math.min(state.offset + state.limit, state.total);
  pageLabel.textContent = `${start.toLocaleString()}-${end.toLocaleString()} of ${state.total.toLocaleString()}`;
  prevPage.disabled = state.offset === 0;
  nextPage.disabled = state.offset + state.limit >= state.total;
}

async function loadDetail(albumId) {
  state.selectedId = albumId;
  document.querySelectorAll("tbody tr").forEach((row) => {
    row.classList.toggle("selected", Number(row.dataset.id) === albumId);
  });
  const response = await fetch(`/api/albums/${albumId}`);
  const payload = await response.json();
  currentDetailPayload = payload;
  renderDetail(payload);
  detailEl.scrollTop = 0;
}

function renderDetail(payload) {
  const { album, artist, tracks = [], genres = [], cover_art: covers = [], external = [], services = [] } = payload;
  const appleAlbum = appleAlbumFromServices(album, external);
  const frontCover = covers.find((cover) => cover.is_front) || covers[0];
  const coverUrl = frontCover?.local_image_url || frontCover?.thumbnail_large || frontCover?.thumbnail_small || frontCover?.image_url;
  const genreChips = renderGenreChips(genres, external);
  const serviceBadges = renderServiceBadges(services);
  const providerBlocks = renderProviderBlocks(external);
  const trackList = renderTracks(appleAlbum, tracks);
  const artistBlock = album.compilation || isVariousArtist(album.artist) ? "" : renderArtistBlock(artist);

  detailEl.innerHTML = `
    ${
      canEditCatalog()
        ? `<div class="detailActions">
            <button class="primaryButton" type="button" data-edit-album="${escapeAttribute(album.id)}">Edit</button>
            <button class="dangerButton" type="button" data-delete-album="${escapeAttribute(album.id)}">Delete</button>
          </div>`
        : ""
    }
    ${coverUrl ? renderLightboxImage("coverImage", coverUrl, `${album.album_name} cover art`) : ""}
    <h2>${escapeHtml(album.album_name)}</h2>
    <p class="metaLine">${renderArtistName(album.artist, "inline")} · row ${escapeHtml(album.row_number)}</p>

    ${serviceBadges}
    ${genreChips}

    <h3>Catalog</h3>
    <dl>
      <dt>Catalog #</dt><dd>${escapeHtml(album.catalog_number)}</dd>
      <dt>Label</dt><dd>${album.label ? `<button class="labelLink" type="button" data-label="${escapeAttribute(album.label)}">${escapeHtml(album.label)}</button>` : "—"}</dd>
      <dt>Format</dt><dd>${escapeHtml(album.format)}</dd>
      <dt>Compilation</dt><dd>${album.compilation ? "Yes" : "No"}</dd>
      <dt>Country</dt><dd>${escapeHtml(album.country)}</dd>
      <dt>Released</dt><dd>${escapeHtml(album.released)}</dd>
      <dt>Genre</dt><dd>${escapeHtml(album.genre)}</dd>
      <dt>Case broken</dt><dd>${escapeHtml(album.case_broken)}</dd>
      <dt>RYM</dt><dd>${escapeHtml(album.rateyourmusic)}</dd>
      <dt>Notes</dt><dd>${escapeHtml(album.notes)}</dd>
      <dt>Other</dt><dd>${escapeHtml(album.other)}</dd>
      <dt>recording_id</dt><dd>${renderRecordingIds(tracks)}</dd>
    </dl>

    ${providerBlocks}

    <h3>Tracks</h3>
    ${trackList}

    ${artistBlock}
  `;
  if (MUSIC_PREVIEWS_ENABLED) {
    updateTrackPreviewAvailability(album, appleAlbum, tracks);
  }
}

function renderRecordingIds(tracks) {
  const ids = [...new Set(tracks.map((track) => track.recording_id).filter(Boolean))];
  if (!ids.length) return "—";
  const [firstId, ...rest] = ids;
  return `
    <details class="recordingIds">
      <summary>
        <span class="turnIcon" aria-hidden="true"></span>
        <span>${escapeHtml(firstId)}</span>
      </summary>
      ${
        rest.length
          ? `<ul>${rest.map((id) => `<li>${escapeHtml(id)}</li>`).join("")}</ul>`
          : ""
      }
    </details>
  `;
}

function renderServiceBadges(services) {
  if (!services.length) return "";
  return `
    <div class="serviceBadges">
      ${services
        .map((service) => {
          const status = service.found ? "found" : service.lookup_status;
          return `<span class="${escapeAttribute(status)}" title="${escapeAttribute(service.lookup_error || service.title || "")}">${escapeHtml(sourceLabel(service.provider))}: ${escapeHtml(service.lookup_status)}</span>`;
        })
        .join("")}
    </div>
  `;
}

function renderGenreChips(genres, external) {
  const chips = [];
  for (const genre of genres) {
    chips.push({ name: genre.name, source: genre.source.replace("musicbrainz_", "MB ") });
  }
  for (const provider of external) {
    if (provider.lookup_status !== "matched") continue;
    for (const name of [...parseList(provider.genres), ...parseList(provider.styles)]) {
      chips.push({ name, source: provider.provider });
    }
  }

  const seen = new Set();
  const unique = chips.filter((chip) => {
    const key = chip.name.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (!unique.length) return "";
  return `<div class="chips">${unique.slice(0, 12).map((chip) => `<button type="button" title="Search ${escapeAttribute(sourceLabel(chip.source))}" data-genre="${escapeAttribute(chip.name)}">${escapeHtml(chip.name)}</button>`).join("")}</div>`;
}

function renderProviderBlocks(external) {
  if (!external.length) return "";
  return `
    <h3>Music Services</h3>
    <div class="providers">
      ${external
        .map((provider) => {
          const genres = [...parseList(provider.genres), ...parseList(provider.styles)];
          const statusClass = provider.lookup_status === "matched" ? "ok" : "muted";
          return `
            <section class="provider">
              <div class="providerHead">
                <strong>${escapeHtml(sourceLabel(provider.provider))}</strong>
                <span class="${statusClass}">${escapeHtml(provider.lookup_status)}</span>
              </div>
              ${
                provider.lookup_status === "matched"
                  ? `<div class="metaLine">${provider.artist ? `${renderArtistName(provider.artist, "inline")} · ` : ""}${escapeHtml(provider.title)}${provider.url ? ` · <a href="${escapeHtml(provider.url)}" target="_blank" rel="noreferrer">open</a>` : ""}</div>
                     ${genres.length ? `<div class="miniChips">${genres.slice(0, 10).map((name) => `<button type="button" data-genre="${escapeAttribute(name)}">${escapeHtml(name)}</button>`).join("")}</div>` : ""}`
                  : `<div class="metaLine">${escapeHtml(provider.lookup_error || "No metadata returned")}</div>`
              }
            </section>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderTracks(album, tracks) {
  if (!tracks.length) {
    return `<div class="emptyState">No tracklist cached for this album.</div>`;
  }
  return `
    <ol class="tracks">
      ${tracks
        .map(
          (track) => `
            <li>
              <span class="trackNumber">${escapeHtml(track.track_number || track.track_position)}</span>
              <span class="trackTitle">
                <button
                  class="previewButton"
                  type="button"
                  data-preview-track="${escapeAttribute(track.title)}"
                  data-preview-artist="${escapeAttribute(album.artist)}"
                  data-preview-album="${escapeAttribute(album.album_name)}"
                  hidden
                  aria-label="Play preview"
                ></button>
                ${track.explicit ? `<span class="explicitBadge" title="Explicit lyrics" aria-label="Explicit lyrics">E</span>` : ""}
                <span>${renderTrackTitle(album, track.title)}</span>
                <span class="previewMessage" role="status"></span>
              </span>
              <span class="trackTime">${escapeHtml(formatDuration(track.length_ms))}</span>
            </li>
          `,
        )
        .join("")}
    </ol>
  `;
}

const addAlbumFields = [
  { name: "timestamp", label: "Timestamp", type: "text", value: currentTimestampValue },
  { name: "catalog_number", label: "1190_ID", type: "number", inputMode: "numeric", pattern: "[0-9]*", required: true },
  { name: "artist", label: "Artist", type: "text" },
  { name: "album_name", label: "Album Name", type: "text" },
  { name: "version_number", label: "Version Number", type: "text" },
  { name: "case_broken", label: "Case Broken", type: "select", options: ["", "No", "Yes"] },
  { name: "label_number_missing", label: "Label Number Missing", type: "text" },
  { name: "label", label: "Label", type: "text" },
  { name: "format", label: "Format", type: "select", value: "CD", options: ["CD", "Vinyl", "Cassette", "Digital"] },
  { name: "compilation", label: "Compilation", type: "checkbox" },
  { name: "country", label: "Country", type: "text" },
  { name: "released", label: "Released", type: "text" },
  { name: "genre", label: "Genre", type: "text" },
  { name: "notes", label: "Notes", type: "textarea" },
  { name: "other", label: "Other", type: "textarea" },
];

function renderAddField(field) {
  const id = `add-${field.name}`;
  const fieldValue = typeof field.value === "function" ? field.value() : field.value || "";
  if (field.type === "textarea") {
    return `
      <label class="formField wide" for="${id}">
        <span>${escapeHtml(field.label)}</span>
        <textarea id="${id}" name="${field.name}"></textarea>
      </label>
    `;
  }
  if (field.type === "checkbox") {
    return `
      <label class="formField checkboxField" for="${id}">
        <span>${escapeHtml(field.label)}</span>
        <input id="${id}" name="${field.name}" type="checkbox" ${fieldValue ? "checked" : ""} />
      </label>
    `;
  }
  if (field.type === "select") {
    return `
      <label class="formField" for="${id}">
        <span>${escapeHtml(field.label)}</span>
        <select id="${id}" name="${field.name}">
          ${(field.options || [])
            .map((option) => `<option value="${escapeAttribute(option)}" ${option === fieldValue ? "selected" : ""}>${escapeRaw(option)}</option>`)
            .join("")}
        </select>
      </label>
    `;
  }
  const inputAttrs = [
    field.required ? "required" : "",
    field.inputMode ? `inputmode="${escapeAttribute(field.inputMode)}"` : "",
    field.pattern ? `pattern="${escapeAttribute(field.pattern)}"` : "",
  ]
    .filter(Boolean)
    .join(" ");
  return `
    <label class="formField" for="${id}">
      <span>${escapeHtml(field.label)}</span>
      <input id="${id}" name="${field.name}" type="${escapeAttribute(field.type)}" value="${escapeAttribute(fieldValue)}" ${inputAttrs} />
    </label>
  `;
}

function showAlbumForm(mode = "add", payload = null) {
  const isEdit = mode === "edit";
  const album = payload?.album || {};
  if (!isEdit) {
    state.selectedId = null;
    document.querySelectorAll("tbody tr").forEach((row) => row.classList.remove("selected"));
  }
  addCoverDataUrl = "";
  detailEl.scrollTop = 0;
  detailEl.innerHTML = `
    <form class="addAlbumForm" id="addAlbumForm" data-mode="${escapeAttribute(mode)}" ${isEdit ? `data-album-id="${escapeAttribute(album.id)}"` : ""}>
      <h2>${isEdit ? "Edit Album" : "Add Album"}</h2>
      ${
        isEdit
          ? `<label class="formField wide" for="edit-service-url">
              <span>Match to this Album</span>
              <div class="serviceLookupRow">
                <input id="edit-service-url" name="music_service_url" type="url" placeholder="MusicBrainz, Discogs, Apple Music, or Last.fm album URL" />
                <button type="button" data-preview-match-url>Get Album Info</button>
              </div>
            </label>`
          : `<label class="formField wide" for="add-service-url">
              <span>Load from Music Service URL</span>
              <div class="serviceLookupRow">
                <input id="add-service-url" name="music_service_url" type="url" placeholder="Discogs release or master URL" />
                <button type="button" data-load-service-url>Load</button>
              </div>
            </label>`
      }
      <p class="formMessage" data-add-message aria-live="polite"></p>
      <div class="formGrid">
        ${addAlbumFields.map(renderAddField).join("")}
        <label class="formField wide" for="add-cover">
          <span>Album Cover Image</span>
          <input id="add-cover" name="cover" type="file" accept="image/png,image/jpeg,image/webp" />
        </label>
      </div>
      <div class="coverPreview" data-cover-preview hidden></div>
      <section class="trackEditor" aria-label="Song list">
        <div class="trackEditorHead">
          <h3>Song List</h3>
          <button type="button" data-add-track-row>Add Track</button>
        </div>
        <div class="trackRows" data-track-rows></div>
      </section>
      <div class="formActions">
        <button class="primaryButton" type="submit">${isEdit ? "Save Changes" : "Save Album"}</button>
        <button type="button" data-cancel-add>Cancel</button>
      </div>
    </form>
  `;
  if (isEdit) {
    populateAlbumFormFromAlbum(detailEl.querySelector(".addAlbumForm"), album);
  }
  renderTrackEditorRows(detailEl.querySelector(".addAlbumForm"), isEdit ? payload?.tracks || [] : []);
}

function showAddAlbumForm() {
  if (!canEditCatalog()) return;
  showAlbumForm("add");
}

function addFormValues(form) {
  const values = {};
  for (const field of addAlbumFields) {
    if (field.type === "checkbox") {
      values[field.name] = Boolean(form.elements[field.name]?.checked);
    } else {
      values[field.name] = form.elements[field.name]?.value?.trim() || "";
    }
  }
  return values;
}

function renderTrackEditorRow(track = {}, index = 0) {
  const trackNumber = track.track_number || track.track_position || String(index + 1);
  return `
    <div class="trackEditRow">
      <label>
        <span>#</span>
        <input name="track_number" type="text" value="${escapeAttribute(trackNumber)}" />
      </label>
      <label>
        <span>Title</span>
        <input name="title" type="text" value="${escapeAttribute(track.title || "")}" />
      </label>
      <label class="explicitCheck">
        <span>Explicit Lyrics</span>
        <input name="explicit" type="checkbox" ${track.explicit ? "checked" : ""} />
      </label>
      <button type="button" class="iconButton" data-remove-track-row aria-label="Remove track">×</button>
    </div>
  `;
}

function renderTrackEditorRows(form, tracks = []) {
  const rows = form?.querySelector("[data-track-rows]");
  if (!rows) return;
  form.dataset.tracksLoaded = tracks.length ? "1" : "";
  const source = tracks.length ? tracks : [{ track_number: "1", title: "", explicit: false }];
  rows.innerHTML = source.map(renderTrackEditorRow).join("");
}

function addTrackEditorRow(form, track = {}) {
  const rows = form?.querySelector("[data-track-rows]");
  if (!rows) return;
  form.dataset.tracksLoaded = "1";
  const index = rows.querySelectorAll(".trackEditRow").length;
  rows.insertAdjacentHTML("beforeend", renderTrackEditorRow(track, index));
}

function trackEditorValues(form) {
  return [...form.querySelectorAll(".trackEditRow")]
    .map((row, index) => {
      const title = row.querySelector("input[name='title']")?.value.trim() || "";
      const trackNumber = row.querySelector("input[name='track_number']")?.value.trim() || String(index + 1);
      const explicit = Boolean(row.querySelector("input[name='explicit']")?.checked);
      return { track_number: trackNumber, title, explicit };
    })
    .filter((track) => track.title);
}

function setAddField(form, name, value, overwrite = true) {
  const field = form.elements[name];
  if (!field || value === null || value === undefined || value === "") return;
  if (field.type === "checkbox") {
    field.checked = Boolean(value);
    return;
  }
  if (!overwrite && field.value.trim()) return;
  field.value = name === "format" ? formatOptionValue(value) : value;
}

function populateAlbumFormFromAlbum(form, album) {
  for (const field of addAlbumFields) {
    const control = form.elements[field.name];
    if (!control) continue;
    if (field.type === "checkbox") {
      control.checked = Boolean(album[field.name]);
    } else if (album[field.name] !== null && album[field.name] !== undefined) {
      control.value = field.name === "format" ? formatOptionValue(album[field.name]) : album[field.name];
    }
  }
}

function bestExternalMatch(external) {
  const order = ["apple_itunes", "discogs", "lastfm", "musicbrainz"];
  for (const provider of order) {
    const match = external.find((item) => item.provider === provider && item.lookup_status === "matched");
    if (match) return match;
  }
  return null;
}

function firstCoverUrl(covers) {
  const cover = covers.find((item) => item.is_front) || covers[0];
  return cover?.local_image_url || cover?.thumbnail_large || cover?.thumbnail_small || cover?.image_url || "";
}

function showAddCoverPreview(url) {
  const preview = detailEl.querySelector("[data-cover-preview]");
  if (!preview) return;
  if (!url) {
    preview.hidden = true;
    preview.innerHTML = "";
    return;
  }
  preview.hidden = false;
  preview.innerHTML = renderLightboxImage("coverImage", url, "Album cover preview");
}

function populateAddFormFromBundle(form, payload) {
  const album = payload.album || {};
  const external = payload.external || [];
  const match = bestExternalMatch(external) || {};
  const values = {
    artist: match.artist || album.artist,
    album_name: match.title || album.album_name,
    label: album.label,
    compilation: album.compilation,
    country: album.country,
    released: album.released,
    genre: album.genre,
  };
  Object.entries(values).forEach(([name, value]) => setAddField(form, name, value));
  setAddField(form, "format", formatOptionValue(album.format), false);
  showAddCoverPreview(firstCoverUrl(payload.cover_art || []));
  renderTrackEditorRows(form, payload.tracks || []);
}

async function updateTrackPreviewAvailability(album, appleAlbum, tracks) {
  detailEl.dataset.applePreviewCheck = "checking";
  delete detailEl.dataset.applePreviewError;
  const hasAlbum = await findAppleAlbum(appleAlbum, tracks);
  if (state.selectedId !== album.id) return;
  detailEl.dataset.applePreviewCheck = hasAlbum ? "found" : "missing";
  detailEl.querySelectorAll(".previewButton").forEach((button) => {
    button.hidden = !hasAlbum;
  });
}

async function findAppleAlbum(album, tracks) {
  const cacheKey = albumPreviewKey(album);
  if (albumPreviewCache.has(cacheKey)) return albumPreviewCache.get(cacheKey);
  const trackNames = new Set(tracks.map((track) => normalizePreviewText(track.title)).filter(Boolean));
  if (!trackNames.size) {
    albumPreviewCache.set(cacheKey, false);
    return false;
  }

  const params = new URLSearchParams({
    term: albumPreviewTerm(album),
    media: "music",
    entity: "song",
    limit: "25",
    country: "US",
  });
  try {
    const payload = await appleSearch(params);
    const found = (payload.results || []).some((result) => {
      const trackName = normalizePreviewText(result.trackName);
      const artistScore = albumPreviewScore(result, album);
      return result.previewUrl && (artistScore >= 100 || trackNames.has(trackName));
    });
    albumPreviewCache.set(cacheKey, found);
    return found;
  } catch {
    detailEl.dataset.applePreviewError = "lookup failed";
    albumPreviewCache.set(cacheKey, false);
    return false;
  }
}

async function findApplePreview(album, track) {
  const cacheKey = `${album.artist}|${album.album_name}|${track.title}`;
  if (previewCache.has(cacheKey)) return previewCache.get(cacheKey);

  let best = { score: -1, previewUrl: "" };
  for (const term of previewSearchTerms(album, track)) {
    const params = new URLSearchParams({
      term,
      media: "music",
      entity: "song",
      limit: "25",
      country: "US",
    });
    try {
      const payload = await appleSearch(params);
      for (const result of payload.results || []) {
        const score = previewScore(result, album, track);
        if (score > best.score) {
          best = { score, previewUrl: result.previewUrl || "" };
        }
      }
      if (best.score >= 170) break;
    } catch {
      continue;
    }
  }
  const previewUrl = best.score >= 60 ? best.previewUrl : "";
  previewCache.set(cacheKey, previewUrl);
  return previewUrl;
}

function renderArtistBlock(artist) {
  if (!artist || artist.lookup_status !== "matched") return "";
  const bio = stripTags(artist.bio_content || artist.bio_summary || "");
  const excerpt = truncateWords(bio, 400);
  return `
    <section class="artistProfile">
      <h3>Artist</h3>
      ${artist.local_image_url ? renderLightboxImage("artistImage", artist.local_image_url, `${artist.name} artist image`) : ""}
      <h4>${escapeHtml(artist.name)}</h4>
      ${
        bio
          ? excerpt.truncated
            ? `<p class="artistBio bioExcerpt">${escapeHtml(excerpt.text)} <button class="textLink" type="button" data-bio-toggle>Full bio</button></p>
               <p class="artistBio bioFull" hidden>${escapeHtml(bio)} <button class="textLink" type="button" data-bio-toggle>Less</button></p>`
            : `<p class="artistBio">${escapeHtml(bio)}</p>`
          : `<p class="metaLine">No Last.fm biography is cached for this artist.</p>`
      }
      ${artist.lastfm_url ? `<a href="${escapeHtml(artist.lastfm_url)}" target="_blank" rel="noreferrer">Last.fm profile</a>` : ""}
    </section>
  `;
}

function renderLightboxImage(className, imageUrl, altText) {
  return `
    <button class="lightboxTrigger ${escapeAttribute(className)}Button" type="button" data-lightbox-src="${escapeAttribute(imageUrl)}" data-lightbox-alt="${escapeAttribute(altText)}">
      <img class="${escapeAttribute(className)}" src="${escapeHtml(imageUrl)}" alt="${escapeHtml(altText)}" />
    </button>
  `;
}

async function showTagCloud() {
  const response = await fetch("/api/tags");
  const payload = await response.json();
  const tags = payload.tags || [];
  state.selectedId = null;
  document.querySelectorAll("tbody tr").forEach((row) => row.classList.remove("selected"));
  detailEl.scrollTop = 0;
  detailEl.innerHTML = `
    <h2>Tag Cloud</h2>
    <p class="metaLine">${tags.length.toLocaleString()} tags from cached music service data</p>
    <div class="tagCloud">
      ${tags
        .map((tag) => {
          const level = Math.min(5, Math.max(1, Math.ceil(Math.log2(tag.count + 1))));
          return `<button type="button" class="tagLevel${level}" data-cloud-tag="${escapeAttribute(tag.name)}"><span>${escapeHtml(tag.count)}</span> ${escapeHtml(tag.name)}</button>`;
        })
        .join("")}
    </div>
  `;
}

let searchTimer = null;
searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.q = searchInput.value.trim();
    state.tag = "";
    state.artist = "";
    state.label = "";
    state.offset = 0;
    loadAlbums();
  }, 180);
});

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  state.q = searchInput.value.trim();
  state.tag = "";
  state.artist = "";
  state.label = "";
  state.offset = 0;
  loadAlbums();
});

hideNaInput.addEventListener("change", () => {
  state.hideNa = hideNaInput.checked;
  state.offset = 0;
  loadAlbums();
});

searchTracksInput.addEventListener("change", () => {
  state.searchTracks = searchTracksInput.checked;
  state.offset = 0;
  loadAlbums();
});

addAlbumButton.addEventListener("click", showAddAlbumForm);

rowsEl.addEventListener("click", (event) => {
  const artistButton = event.target.closest("[data-artist]");
  if (artistButton) {
    searchByArtist(artistButton.dataset.artist || "");
    return;
  }
  const labelButton = event.target.closest("[data-label]");
  if (labelButton) {
    searchByLabel(labelButton.dataset.label || "");
    return;
  }
  const row = event.target.closest("tr[data-id]");
  if (row) {
    loadDetail(Number(row.dataset.id));
  }
});

detailEl.addEventListener("click", (event) => {
  const previewButton = event.target.closest("[data-preview-track]");
  if (previewButton) {
    if (!MUSIC_PREVIEWS_ENABLED) return;
    togglePreview(previewButton);
    return;
  }
  const loadServiceButton = event.target.closest("[data-load-service-url]");
  if (loadServiceButton) {
    loadServiceUrlIntoAddForm(loadServiceButton.closest(".addAlbumForm"));
    return;
  }
  const previewMatchButton = event.target.closest("[data-preview-match-url]");
  if (previewMatchButton) {
    previewMatchIntoEditForm(previewMatchButton.closest(".addAlbumForm"));
    return;
  }
  const editButton = event.target.closest("[data-edit-album]");
  if (editButton && currentDetailPayload) {
    showAlbumForm("edit", currentDetailPayload);
    return;
  }
  const deleteButton = event.target.closest("[data-delete-album]");
  if (deleteButton && currentDetailPayload) {
    deleteAlbum(Number(deleteButton.dataset.deleteAlbum));
    return;
  }
  const addTrackButton = event.target.closest("[data-add-track-row]");
  if (addTrackButton) {
    addTrackEditorRow(addTrackButton.closest(".addAlbumForm"));
    return;
  }
  const removeTrackButton = event.target.closest("[data-remove-track-row]");
  if (removeTrackButton) {
    const form = removeTrackButton.closest(".addAlbumForm");
    removeTrackButton.closest(".trackEditRow")?.remove();
    if (!form.querySelector(".trackEditRow")) {
      addTrackEditorRow(form);
    }
    return;
  }
  const cancelAdd = event.target.closest("[data-cancel-add]");
  if (cancelAdd) {
    if (currentDetailPayload?.album?.id) {
      renderDetail(currentDetailPayload);
    } else {
      detailEl.innerHTML = `<div class="emptyState">Select an album to see catalog notes and music service metadata.</div>`;
    }
    return;
  }
  const bioToggle = event.target.closest("[data-bio-toggle]");
  if (bioToggle) {
    const profile = bioToggle.closest(".artistProfile");
    const excerpt = profile?.querySelector(".bioExcerpt");
    const full = profile?.querySelector(".bioFull");
    if (excerpt && full) {
      const showFull = full.hidden;
      excerpt.hidden = showFull;
      full.hidden = !showFull;
    }
    return;
  }
  const lightboxTrigger = event.target.closest("[data-lightbox-src]");
  if (lightboxTrigger) {
    openLightbox(lightboxTrigger.dataset.lightboxSrc || "", lightboxTrigger.dataset.lightboxAlt || "");
    return;
  }
  const artistButton = event.target.closest("[data-artist]");
  if (artistButton) {
    searchByArtist(artistButton.dataset.artist || "");
    return;
  }
  const labelButton = event.target.closest("[data-label]");
  if (labelButton) {
    searchByLabel(labelButton.dataset.label || "");
    return;
  }
  const chip = event.target.closest("[data-genre]");
  const cloudTag = event.target.closest("[data-cloud-tag]");
  if (!chip && !cloudTag) return;
  state.q = "";
  state.tag = chip?.dataset.genre || cloudTag?.dataset.cloudTag || "";
  state.artist = "";
  state.label = "";
  state.offset = 0;
  searchInput.value = state.tag;
  loadAlbums();
});

detailEl.addEventListener("change", (event) => {
  const input = event.target.closest(".addAlbumForm input[type='file'][name='cover']");
  if (!input) return;
  const file = input.files?.[0];
  addCoverDataUrl = "";
  if (!file) {
    showAddCoverPreview("");
    return;
  }
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    const message = detailEl.querySelector("[data-add-message]");
    if (message) message.textContent = "Cover image must be a JPEG, PNG, or WebP file.";
    input.value = "";
    return;
  }
  const reader = new FileReader();
  reader.addEventListener("load", () => {
    addCoverDataUrl = String(reader.result || "");
    showAddCoverPreview(addCoverDataUrl);
  });
  reader.readAsDataURL(file);
});

function resetPreviewButton(button) {
  if (!button) return;
  button.classList.remove("playing");
  button.classList.remove("loading");
  button.setAttribute("aria-label", "Play preview");
}

async function togglePreview(button) {
  if (button.classList.contains("unavailable") || button.classList.contains("loading")) return;
  if (previewState.button === button && !previewState.audio.paused) {
    previewState.audio.pause();
    resetPreviewButton(button);
    return;
  }

  let previewUrl = button.dataset.previewUrl || "";
  if (!previewUrl) {
    const message = button.parentElement?.querySelector(".previewMessage");
    button.classList.add("loading");
    button.setAttribute("aria-label", "Loading preview");
    if (message) message.textContent = "";
    previewUrl = await findApplePreview(
      { artist: button.dataset.previewArtist || "", album_name: button.dataset.previewAlbum || "" },
      { title: button.dataset.previewTrack || "" },
    );
    button.classList.remove("loading");
    if (!previewUrl) {
      button.classList.add("unavailable");
      button.setAttribute("aria-label", "Preview unavailable");
      if (message) message.textContent = "Preview unavailable";
      return;
    }
    button.dataset.previewUrl = previewUrl;
  }

  resetPreviewButton(previewState.button);
  previewState.button = button;
  previewState.audio.src = previewUrl;
  previewState.audio.loop = false;
  button.classList.add("playing");
  button.setAttribute("aria-label", "Pause preview");
  try {
    await previewState.audio.play();
  } catch {
    resetPreviewButton(button);
  }
}

previewState.audio.addEventListener("ended", () => resetPreviewButton(previewState.button));
previewState.audio.addEventListener("pause", () => {
  if (previewState.audio.ended) return;
  resetPreviewButton(previewState.button);
});

statsEl.addEventListener("click", (event) => {
  const button = event.target.closest("#tagCloudButton");
  if (!button) return;
  showTagCloud();
});

detailEl.addEventListener("submit", async (event) => {
  const addForm = event.target.closest(".addAlbumForm");
  if (addForm) {
    event.preventDefault();
    saveAddedAlbum(addForm);
    return;
  }
});

async function loadServiceUrlIntoAddForm(form) {
  if (!form) return;
  const message = form.querySelector("[data-add-message]");
  const button = form.querySelector("[data-load-service-url]");
  const serviceUrl = form.elements.music_service_url.value.trim();
  if (!serviceUrl) {
    message.textContent = "Enter a Discogs release or master URL.";
    return;
  }
  message.textContent = "Looking up Discogs data...";
  button.disabled = true;
  try {
    const current = addFormValues(form);
    const response = await fetch("/api/music-service-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: serviceUrl,
        artist: current.artist,
        album_name: current.album_name,
        format: current.format || "CD",
        compilation: current.compilation,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to load music service data.");
    populateAddFormFromBundle(form, payload);
    message.textContent = "Loaded Discogs data. Review the fields before saving.";
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function previewMatchIntoEditForm(form) {
  if (!form) return;
  const message = form.querySelector("[data-add-message]");
  const button = form.querySelector("[data-preview-match-url]");
  const serviceUrl = form.elements.music_service_url.value.trim();
  if (!serviceUrl) {
    message.textContent = "Enter a MusicBrainz, Discogs, Apple Music, or Last.fm album URL.";
    return;
  }
  message.textContent = "Looking up album info...";
  button.disabled = true;
  try {
    const response = await fetch("/api/music-service-match-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: serviceUrl,
        album: addFormValues(form),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to get album info.");
    populateAddFormFromBundle(form, payload);
    message.textContent = "Loaded album info. Review the fields, then save to update the database.";
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function saveAddedAlbum(form) {
  const message = form.querySelector("[data-add-message]");
  const submitButton = form.querySelector("button[type='submit']");
  const values = addFormValues(form);
  const isEdit = form.dataset.mode === "edit";
  const serviceUrl = form.elements.music_service_url?.value.trim() || "";
  const tracks = trackEditorValues(form);
  if (!values.catalog_number) {
    message.textContent = "1190_ID is required.";
    form.elements.catalog_number?.focus();
    return;
  }
  if (!values.artist && !values.album_name && !serviceUrl) {
    message.textContent = "Artist, album name, or Discogs URL is required.";
    return;
  }
  message.textContent = "Saving album...";
  submitButton.disabled = true;
  try {
    const response = await fetch(isEdit ? `/api/albums/${form.dataset.albumId}` : "/api/albums", {
      method: isEdit ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        album: values,
        music_service_url: serviceUrl,
        cover_data_url: addCoverDataUrl,
        ...(isEdit || tracks.length || form.dataset.tracksLoaded ? { tracks } : {}),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to save album.");
    message.textContent = "Saved. Loading album...";
    await loadStats();
    state.offset = 0;
    state.q = "";
    state.tag = "";
    state.artist = "";
    state.label = "";
    searchInput.value = "";
    await loadAlbums();
    await loadDetail(payload.album_id);
  } catch (error) {
    message.textContent = error.message;
    submitButton.disabled = false;
  }
}

async function deleteAlbum(albumId) {
  const albumName = currentDetailPayload?.album?.album_name || "this album";
  if (!window.confirm(`Delete ${albumName}?`)) return;
  try {
    const response = await fetch(`/api/albums/${albumId}`, { method: "DELETE" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to delete album.");
    currentDetailPayload = null;
    state.selectedId = null;
    detailEl.innerHTML = `<div class="emptyState">Album deleted.</div>`;
    await loadStats();
    await loadAlbums();
  } catch (error) {
    detailEl.insertAdjacentHTML("afterbegin", `<p class="formMessage">${escapeHtml(error.message)}</p>`);
  }
}

function openLightbox(imageUrl, altText) {
  if (!imageUrl) return;
  lightboxImageEl.src = imageUrl;
  lightboxImageEl.alt = altText;
  lightboxEl.hidden = false;
  document.body.classList.add("lightboxOpen");
  lightboxCloseEl.focus();
}

function closeLightbox() {
  lightboxEl.hidden = true;
  lightboxImageEl.removeAttribute("src");
  lightboxImageEl.alt = "";
  document.body.classList.remove("lightboxOpen");
}

lightboxCloseEl.addEventListener("click", closeLightbox);
lightboxEl.addEventListener("click", (event) => {
  if (event.target === lightboxEl) closeLightbox();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !lightboxEl.hidden) closeLightbox();
  if (event.key === "Escape" && accountMenu && !accountMenu.hidden) setAccountMenuOpen(false);
});

document.addEventListener("click", (event) => {
  if (!accountMenu || !accountMenuButton || accountMenu.hidden) return;
  if (event.target.closest("#accountMenu") || event.target.closest("#accountMenuButton")) return;
  setAccountMenuOpen(false);
});

function searchByArtist(artist) {
  state.q = "";
  state.tag = "";
  state.artist = artist;
  state.label = "";
  state.offset = 0;
  searchInput.value = artist;
  loadAlbums();
}

function searchByLabel(label) {
  state.q = "";
  state.tag = "";
  state.artist = "";
  state.label = label;
  state.offset = 0;
  searchInput.value = label;
  loadAlbums();
}

prevPage.addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.limit);
  loadAlbums({ scrollToTop: true });
});

nextPage.addEventListener("click", () => {
  state.offset += state.limit;
  loadAlbums({ scrollToTop: true });
});

accountMenuButton?.addEventListener("click", toggleAccountMenu);
logoutButton?.addEventListener("click", logout);

async function init() {
  await loadSession();
  await loadStats();
  hideNaInput.checked = state.hideNa;
  searchTracksInput.checked = state.searchTracks;
  loadAlbums();
}

init();
