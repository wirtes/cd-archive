const state = {
  q: "",
  tag: "",
  artist: "",
  label: "",
  hideNa: true,
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
const lightboxEl = document.querySelector("#imageLightbox");
const lightboxImageEl = document.querySelector("#lightboxImage");
const lightboxCloseEl = document.querySelector("#lightboxClose");

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

function sourceLabel(value) {
  const labels = {
    musicbrainz: "MusicBrainz",
    discogs: "Discogs",
    lastfm: "Last.fm",
  };
  return labels[value] || value || "";
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

async function loadStats() {
  const response = await fetch("/api/stats");
  const stats = await response.json();
  statsEl.innerHTML = `${stats.albums.toLocaleString()} albums · ${stats.enriched.toLocaleString()} with source data · ${stats.matched.toLocaleString()} matched · ${stats.tracks.toLocaleString()} tracks · <button class="statsLink" type="button" id="tagCloudButton">${stats.genres.toLocaleString()} genres/tags</button>`;
}

async function loadAlbums() {
  const params = new URLSearchParams({
    q: state.q,
    tag: state.tag,
    artist: state.artist,
    label: state.label,
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
      const serviceText = services.length ? services.map(sourceLabel).join(", ") : "Not found";
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
            ${album.label ? `<button class="labelLink metaLine" type="button" data-label="${escapeAttribute(album.label)}">${escapeHtml(album.label)}</button>` : ""}
          </td>
          <td>${escapeHtml(album.format || album.media_format)}</td>
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
  const { album, artist, tracks = [], genres = [], cover_art: covers = [], external = [], services = [] } = payload;
  const frontCover = covers.find((cover) => cover.is_front) || covers[0];
  const coverUrl = frontCover?.local_image_url || frontCover?.thumbnail_large || frontCover?.thumbnail_small || frontCover?.image_url;
  const genreChips = renderGenreChips(genres, external);
  const serviceBadges = renderServiceBadges(services);
  const providerBlocks = renderProviderBlocks(external);
  const trackList = renderTracks(tracks);
  const artistBlock = renderArtistBlock(artist);
  const serviceUrlForm = renderMusicServiceUrlForm(album, services);

  detailEl.innerHTML = `
    ${coverUrl ? renderLightboxImage("coverImage", coverUrl, `${album.album_name} cover art`) : ""}
    <h2>${escapeHtml(album.album_name)}</h2>
    <p class="metaLine"><button class="artistLink inline" type="button" data-artist="${escapeAttribute(album.artist)}">${escapeHtml(album.artist)}</button> · row ${escapeHtml(album.row_number)}</p>

    ${serviceBadges}
    ${genreChips}

    <h3>Catalog</h3>
    <dl>
      <dt>Catalog #</dt><dd>${escapeHtml(album.catalog_number)}</dd>
      <dt>Media</dt><dd>${escapeHtml(album.media_format)}</dd>
      <dt>Label</dt><dd>${album.label ? `<button class="labelLink" type="button" data-label="${escapeAttribute(album.label)}">${escapeHtml(album.label)}</button>` : "—"}</dd>
      <dt>Format</dt><dd>${escapeHtml(album.format)}</dd>
      <dt>Country</dt><dd>${escapeHtml(album.country)}</dd>
      <dt>Released</dt><dd>${escapeHtml(album.released)}</dd>
      <dt>Genre</dt><dd>${escapeHtml(album.genre)}</dd>
      <dt>Case broken</dt><dd>${escapeHtml(album.case_broken)}</dd>
      <dt>RYM</dt><dd>${escapeHtml(album.rateyourmusic)}</dd>
      <dt>Notes</dt><dd>${escapeHtml(album.notes)}</dd>
      <dt>Other</dt><dd>${escapeHtml(album.other)}</dd>
    </dl>

    ${providerBlocks}

    <h3>Tracks</h3>
    ${trackList}

    ${artistBlock}

    ${serviceUrlForm}
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
  return `<div class="chips">${unique.slice(0, 12).map((chip) => `<button type="button" title="Search ${escapeAttribute(chip.source)}" data-genre="${escapeAttribute(chip.name)}">${escapeHtml(chip.name)}</button>`).join("")}</div>`;
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

function hasMusicServiceMatch(services) {
  return services.some((service) => Boolean(service.found));
}

function renderMusicServiceUrlForm(album, services) {
  if (hasMusicServiceMatch(services)) return "";
  return `
    <form class="serviceUrlForm" data-album-id="${escapeAttribute(album.id)}">
      <label for="serviceUrl-${escapeAttribute(album.id)}">Music Service URL</label>
      <div>
        <input id="serviceUrl-${escapeAttribute(album.id)}" name="url" type="url" placeholder="MusicBrainz, Discogs, or Last.fm album URL" required />
        <button type="submit">Save</button>
      </div>
      <p class="formMessage" aria-live="polite"></p>
    </form>
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

statsEl.addEventListener("click", (event) => {
  const button = event.target.closest("#tagCloudButton");
  if (!button) return;
  showTagCloud();
});

detailEl.addEventListener("submit", async (event) => {
  const form = event.target.closest(".serviceUrlForm");
  if (!form) return;
  event.preventDefault();
  const message = form.querySelector(".formMessage");
  const button = form.querySelector("button");
  const input = form.querySelector("input[name='url']");
  message.textContent = "Looking up services...";
  button.disabled = true;
  try {
    const response = await fetch(`/api/albums/${form.dataset.albumId}/music-service-url`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: input.value.trim() }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to save music service URL.");
    message.textContent = "Saved. Refreshing album metadata...";
    await loadStats();
    await loadAlbums();
    await loadDetail(Number(form.dataset.albumId));
  } catch (error) {
    message.textContent = error.message;
    button.disabled = false;
  }
});

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
  loadAlbums();
});

nextPage.addEventListener("click", () => {
  state.offset += state.limit;
  loadAlbums();
});

loadStats();
hideNaInput.checked = state.hideNa;
loadAlbums();
