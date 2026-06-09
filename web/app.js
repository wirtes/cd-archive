const state = {
  q: "",
  tag: "",
  artist: "",
  hideNa: false,
  enriched: "all",
  limit: 50,
  offset: 0,
  total: 0,
  selectedId: null,
};

const rowsEl = document.querySelector("#albumRows");
const detailEl = document.querySelector("#detailPane");
const pageLabel = document.querySelector("#pageLabel");
const prevPage = document.querySelector("#prevPage");
const nextPage = document.querySelector("#nextPage");
const searchForm = document.querySelector("#searchForm");
const searchInput = document.querySelector("#searchInput");
const enrichedFilter = document.querySelector("#enrichedFilter");
const hideNaInput = document.querySelector("#hideNaInput");
const statsEl = document.querySelector("#stats");

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

function escapeAttribute(value) {
  return escapeHtml(value);
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

function formatDuration(ms) {
  if (!ms) return "";
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

async function loadStats() {
  const response = await fetch("/api/stats");
  const stats = await response.json();
  statsEl.textContent = `${stats.albums.toLocaleString()} albums · ${stats.enriched.toLocaleString()} enriched · ${stats.matched.toLocaleString()} matched · ${stats.service_matches.toLocaleString()} service matches · ${stats.tracks.toLocaleString()} tracks · ${stats.genres.toLocaleString()} genres/tags · ${stats.covers.toLocaleString()} covers`;
}

async function loadAlbums() {
  const params = new URLSearchParams({
    q: state.q,
    tag: state.tag,
    artist: state.artist,
    hide_na: state.hideNa ? "1" : "0",
    enriched: state.enriched,
    limit: state.limit,
    offset: state.offset,
  });
  const response = await fetch(`/api/albums?${params}`);
  const payload = await response.json();
  state.total = payload.total;
  renderRows(payload.albums);
  renderPager();
}

function renderRows(albums) {
  rowsEl.innerHTML = albums
    .map((album) => {
      const services = album.matched_services ? album.matched_services.split(",").map((service) => service.trim()).filter(Boolean) : [];
      const serviceText = services.length ? services.join(", ") : "Not found";
      const badgeClass = services.length ? "badge" : "badge missing";
      return `
        <tr data-id="${album.id}" class="${album.id === state.selectedId ? "selected" : ""}">
          <td>${escapeHtml(album.row_number)}</td>
          <td>
            <button class="artistLink" type="button" data-artist="${escapeAttribute(album.artist)}">${escapeHtml(album.artist)}</button>
            <div class="subtle radioId">1190_ID: ${escapeHtml(album.catalog_number)}</div>
          </td>
          <td>
            <div>${escapeHtml(album.album_name)}</div>
            ${album.mb_title ? `<div class="metaLine">MB: ${escapeHtml(album.mb_title)}</div>` : ""}
          </td>
          <td>${escapeHtml(album.version_number)}</td>
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
  renderDetail(payload);
  detailEl.scrollTop = 0;
}

function renderDetail(payload) {
  const { album, musicbrainz: mb, tracks = [], genres = [], cover_art: covers = [], external = [], services = [] } = payload;
  const frontCover = covers.find((cover) => cover.is_front) || covers[0];
  const coverUrl = frontCover?.local_image_url || frontCover?.thumbnail_large || frontCover?.thumbnail_small || frontCover?.image_url;
  const genreChips = renderGenreChips(genres, external);
  const serviceBadges = renderServiceBadges(services);
  const providerBlocks = renderProviderBlocks(external);
  const trackList = renderTracks(tracks);

  detailEl.innerHTML = `
    ${coverUrl ? `<img class="coverImage" src="${escapeHtml(coverUrl)}" alt="${escapeHtml(album.album_name)} cover art" />` : ""}
    <h2>${escapeHtml(album.album_name)}</h2>
    <p class="metaLine"><button class="artistLink inline" type="button" data-artist="${escapeAttribute(album.artist)}">${escapeHtml(album.artist)}</button> · row ${escapeHtml(album.row_number)}</p>

    ${serviceBadges}
    ${genreChips}

    <h3>Catalog</h3>
    <dl>
      <dt>Catalog #</dt><dd>${escapeHtml(album.catalog_number)}</dd>
      <dt>Format</dt><dd>${escapeHtml(album.media_format)}</dd>
      <dt>Version</dt><dd>${escapeHtml(album.version_number)}</dd>
      <dt>Case broken</dt><dd>${escapeHtml(album.case_broken)}</dd>
      <dt>RYM</dt><dd>${escapeHtml(album.rateyourmusic)}</dd>
      <dt>Notes</dt><dd>${escapeHtml(album.notes)}</dd>
      <dt>Other</dt><dd>${escapeHtml(album.other)}</dd>
    </dl>

    <h3>MusicBrainz</h3>
    ${
      mb
        ? `<dl>
            <dt>Status</dt><dd>${escapeHtml(mb.lookup_status)}</dd>
            <dt>Title</dt><dd>${escapeHtml(mb.title)}</dd>
            <dt>Artist</dt><dd>${mb.artist_credit ? `<button class="artistLink inline" type="button" data-artist="${escapeAttribute(mb.artist_credit)}">${escapeHtml(mb.artist_credit)}</button>` : "—"}</dd>
            <dt>Date</dt><dd>${escapeHtml(mb.date)}</dd>
            <dt>Country</dt><dd>${escapeHtml(mb.country)}</dd>
            <dt>Label</dt><dd>${escapeHtml(mb.label_names)}</dd>
            <dt>Label #</dt><dd>${escapeHtml(mb.catalog_numbers)}</dd>
            <dt>Format</dt><dd>${escapeHtml(mb.format)}</dd>
            <dt>Tracks</dt><dd>${escapeHtml(mb.track_count)}</dd>
            <dt>Release</dt><dd>${mb.mb_url ? `<a href="${mb.mb_url}" target="_blank" rel="noreferrer">Open MusicBrainz</a>` : "—"}</dd>
          </dl>`
        : `<div class="emptyState">No MusicBrainz metadata has been attached to this catalog row.</div>`
    }

    ${providerBlocks}

    <h3>Tracks</h3>
    ${trackList}
  `;
}

function renderServiceBadges(services) {
  if (!services.length) return "";
  return `
    <div class="serviceBadges">
      ${services
        .map((service) => {
          const status = service.found ? "found" : service.lookup_status;
          return `<span class="${escapeAttribute(status)}" title="${escapeAttribute(service.lookup_error || service.title || "")}">${escapeHtml(service.provider)}: ${escapeHtml(service.lookup_status)}</span>`;
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
  return `<div class="chips">${unique.slice(0, 12).map((chip) => `<button type="button" title="Search ${escapeAttribute(chip.source)}" data-genre="${escapeAttribute(chip.name)}">${escapeHtml(chip.name)}</button>`).join("")}</div>`;
}

function renderProviderBlocks(external) {
  if (!external.length) return "";
  return `
    <h3>External Sources</h3>
    <div class="providers">
      ${external
        .map((provider) => {
          const genres = [...parseList(provider.genres), ...parseList(provider.styles)];
          const statusClass = provider.lookup_status === "matched" ? "ok" : "muted";
          return `
            <section class="provider">
              <div class="providerHead">
                <strong>${escapeHtml(provider.provider)}</strong>
                <span class="${statusClass}">${escapeHtml(provider.lookup_status)}</span>
              </div>
              ${
                provider.lookup_status === "matched"
                  ? `<div class="metaLine">${provider.artist ? `<button class="artistLink inline" type="button" data-artist="${escapeAttribute(provider.artist)}">${escapeHtml(provider.artist)}</button> · ` : ""}${escapeHtml(provider.title)}${provider.url ? ` · <a href="${escapeHtml(provider.url)}" target="_blank" rel="noreferrer">open</a>` : ""}</div>
                     ${genres.length ? `<div class="miniChips">${genres.slice(0, 10).map((name) => `<span>${escapeHtml(name)}</span>`).join("")}</div>` : ""}`
                  : `<div class="metaLine">${escapeHtml(provider.lookup_error || "No metadata returned")}</div>`
              }
            </section>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderTracks(tracks) {
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
              <span class="trackTitle">${escapeHtml(track.title)}</span>
              <span class="trackTime">${escapeHtml(formatDuration(track.length_ms))}</span>
            </li>
          `,
        )
        .join("")}
    </ol>
  `;
}

let searchTimer = null;
searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.q = searchInput.value.trim();
    state.tag = "";
    state.artist = "";
    state.offset = 0;
    loadAlbums();
  }, 180);
});

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  state.q = searchInput.value.trim();
  state.tag = "";
  state.artist = "";
  state.offset = 0;
  loadAlbums();
});

enrichedFilter.addEventListener("change", () => {
  state.enriched = enrichedFilter.value;
  state.offset = 0;
  loadAlbums();
});

hideNaInput.addEventListener("change", () => {
  state.hideNa = hideNaInput.checked;
  state.offset = 0;
  loadAlbums();
});

rowsEl.addEventListener("click", (event) => {
  const artistButton = event.target.closest("[data-artist]");
  if (artistButton) {
    searchByArtist(artistButton.dataset.artist || "");
    return;
  }
  const row = event.target.closest("tr[data-id]");
  if (row) {
    loadDetail(Number(row.dataset.id));
  }
});

detailEl.addEventListener("click", (event) => {
  const artistButton = event.target.closest("[data-artist]");
  if (artistButton) {
    searchByArtist(artistButton.dataset.artist || "");
    return;
  }
  const chip = event.target.closest("[data-genre]");
  if (!chip) return;
  state.q = "";
  state.tag = chip.dataset.genre || "";
  state.artist = "";
  state.offset = 0;
  searchInput.value = state.tag;
  loadAlbums();
});

function searchByArtist(artist) {
  state.q = "";
  state.tag = "";
  state.artist = artist;
  state.offset = 0;
  searchInput.value = artist;
  loadAlbums();
}

prevPage.addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.limit);
  loadAlbums();
});

nextPage.addEventListener("click", () => {
  state.offset += state.limit;
  loadAlbums();
});

loadStats();
loadAlbums();
