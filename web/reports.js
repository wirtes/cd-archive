const reportsList = document.querySelector("#reportsList");
const sessionUser = document.querySelector("#sessionUser");
const adminLink = document.querySelector("#adminLink");
const accountMenuButton = document.querySelector("#accountMenuButton");
const accountMenu = document.querySelector("#accountMenu");
const logoutButton = document.querySelector("#logoutButton");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

function setAccountMenuOpen(open) {
  if (!accountMenuButton || !accountMenu) return;
  accountMenu.hidden = !open;
  accountMenuButton.setAttribute("aria-expanded", String(open));
}

function toggleAccountMenu() {
  setAccountMenuOpen(accountMenu?.hidden);
}

function reportRow(label, value, className = "") {
  return `
    <div class="${escapeHtml(className)}">
      <dt>${escapeHtml(label)}</dt>
      <dd>${formatNumber(value)}</dd>
    </div>
  `;
}

function renderReports(data) {
  reportsList.innerHTML = [
    reportRow("Total number of records", data.total_records),
    reportRow("Count of records matched to music services", data.matched_records),
    reportRow("Count of records matched to Apple iTunes", data.apple_itunes_matches, "subReport"),
    reportRow("Count of records matched to Discogs", data.discogs_matches, "subReport"),
    reportRow("Count of records matched to Last.fm", data.lastfm_matches, "subReport"),
    reportRow("Count of records matched to MusicBrainz", data.musicbrainz_matches, "subReport"),
    reportRow("Records manually updated", data.manually_updated_records),
    reportRow("Number of tracks", data.tracks),
    reportRow("Number of unique genre tags", data.unique_genre_tags),
  ].join("");
}

async function loadSession() {
  const response = await fetch("/api/session", { cache: "no-store" });
  if (response.status === 401) {
    window.location.href = "/login.html?next=%2Freports.html";
    return null;
  }
  const payload = await response.json();
  if (sessionUser) sessionUser.textContent = payload.username || "";
  if (adminLink) adminLink.hidden = !payload.roles?.admin;
  return payload;
}

async function loadReports() {
  const response = await fetch("/api/reports", { cache: "no-store" });
  if (response.status === 401) {
    window.location.href = "/login.html?next=%2Freports.html";
    return;
  }
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Unable to load reports.");
  renderReports(payload);
}

async function logout() {
  await fetch("/api/logout", { method: "POST" });
  window.location.href = "/login.html?next=%2Freports.html";
}

accountMenuButton?.addEventListener("click", toggleAccountMenu);
logoutButton?.addEventListener("click", logout);
document.addEventListener("click", (event) => {
  if (!accountMenu || accountMenu.hidden) return;
  if (event.target.closest(".accountMenuWrap")) return;
  setAccountMenuOpen(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") setAccountMenuOpen(false);
});

loadSession()
  .then((session) => {
    if (session) return loadReports();
    return null;
  })
  .catch((error) => {
    reportsList.innerHTML = reportRow("Error", error.message);
  });
