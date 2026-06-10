const videoEl = document.querySelector("#scannerVideo");
const scannerCoverImage = document.querySelector("#scannerCoverImage");
const startScanButton = document.querySelector("#startScanButton");
const stopScanButton = document.querySelector("#stopScanButton");
const lookupForm = document.querySelector("#lookupForm");
const barcodeInput = document.querySelector("#barcodeInput");
const mobileCatalogInput = document.querySelector("#mobileCatalogInput");
const statusMessage = document.querySelector("#statusMessage");
const releaseCard = document.querySelector("#releaseCard");
const releaseImage = document.querySelector("#releaseImage");
const releaseTitle = document.querySelector("#releaseTitle");
const releaseArtist = document.querySelector("#releaseArtist");
const releaseDetails = document.querySelector("#releaseDetails");
const addReleaseButton = document.querySelector("#addReleaseButton");
const desktopTopAddButton = document.querySelector("#desktopTopAddButton");
const desktopClearButton = document.querySelector("#desktopClearButton");
const desktopAddForm = document.querySelector("#desktopAddForm");
const desktopListenerStatus = document.querySelector("#desktopListenerStatus");
const desktopLoadServiceButton = document.querySelector("#desktopLoadServiceButton");
const desktopServiceMessage = document.querySelector("#desktopServiceMessage");
const desktopCoverPreview = document.querySelector("#desktopCoverPreview");
const desktopTrackRows = document.querySelector("#desktopTrackRows");
const releaseTrackRows = document.querySelector("#releaseTrackRows");
const sessionUser = document.querySelector("#sessionUser");
const desktopQrUser = document.querySelector("#desktopQrUser");
const adminLink = document.querySelector("#adminLink");
const accountMenuButton = document.querySelector("#accountMenuButton");
const accountMenu = document.querySelector("#accountMenu");
const logoutButton = document.querySelector("#logoutButton");

let barcodeDetector = null;
let zxingReader = null;
let zxingControls = null;
let zxingCameraTuned = false;
let scanStream = null;
let scanFrame = 0;
let currentRelease = null;
let lookupInProgress = false;
let lastScanEventId = 0;
let currentRoles = { admin: false, editor: false };
let desktopCoverDataUrl = "";

const UPC_FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e"];
const SCANNER_VIDEO_CONSTRAINTS = {
  facingMode: { ideal: "environment" },
  width: { ideal: 1280 },
  height: { ideal: 720 },
  advanced: [{ focusMode: "continuous" }],
};
const isDesktopView = () => window.matchMedia("(min-width: 760px)").matches;
const canEditCatalog = () => Boolean(currentRoles.admin || currentRoles.editor);
const hasNativeScanner = () => "BarcodeDetector" in window;
const hasZxingScanner = () => Boolean(window.ZXingBrowser?.BrowserMultiFormatReader);
const hasLiveScanner = () => hasNativeScanner() || hasZxingScanner();

function setStatus(message, isError = false) {
  statusMessage.textContent = message || "";
  statusMessage.classList.toggle("isError", Boolean(message && isError));
}

function showScannerVideo() {
  videoEl.hidden = false;
  if (scannerCoverImage) {
    scannerCoverImage.hidden = true;
    scannerCoverImage.style.backgroundImage = "";
    scannerCoverImage.setAttribute("aria-label", "");
  }
}

function showScannerCover(payload) {
  if (isDesktopView() || !scannerCoverImage) return;
  const album = payload.album || {};
  const coverUrl = payload.cover_url || "/images/1190-logo-reversed-300x180.png";
  scannerCoverImage.style.backgroundImage = `url("${coverUrl.replaceAll('"', "%22")}")`;
  scannerCoverImage.setAttribute("aria-label", `${album.album_name || "Release"} cover`);
  scannerCoverImage.hidden = false;
  videoEl.hidden = true;
}

function clearReleaseState() {
  currentRelease = null;
  releaseCard.hidden = true;
  releaseImage.removeAttribute("src");
  releaseImage.alt = "";
  addReleaseButton.disabled = true;
  if (desktopTopAddButton) desktopTopAddButton.disabled = true;
  renderTrackEditorRows(releaseTrackRows, []);
  renderTrackEditorRows(desktopTrackRows, []);
}

function cleanBarcode(value) {
  return String(value || "").replace(/\D/g, "");
}

function text(value) {
  return value === null || value === undefined || value === "" ? "N/A" : String(value);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value);
}

function currentTimestampValue() {
  return new Date().toISOString().slice(0, 19).replace("T", " ");
}

function defaultDesktopAlbumValues() {
  return {
    timestamp: currentTimestampValue(),
    catalog_number: "",
    artist: "",
    album_name: "",
    version_number: "",
    case_broken: "No",
    label_number_missing: "",
    label: "",
    format: "CD",
    compilation: false,
    country: "",
    released: "",
    genre: "",
    notes: "",
    other: "",
    music_service_url: "",
  };
}

async function supportedNativeBarcodeFormats() {
  if (!hasNativeScanner()) return [];
  if (!BarcodeDetector.getSupportedFormats) return UPC_FORMATS;
  try {
    const formats = await BarcodeDetector.getSupportedFormats();
    return UPC_FORMATS.filter((format) => formats.includes(format));
  } catch {
    return UPC_FORMATS;
  }
}

function renderDetails(payload) {
  const album = payload.album || {};
  const rows = [
    ["Label", album.label],
    ["Format", album.format],
    ["Country", album.country],
    ["Released", album.released],
    ["Genre", album.genre],
    ["Tracks", payload.track_count],
    ["UPC", payload.barcode],
  ];
  releaseDetails.innerHTML = rows.map(([label, value]) => `<dt>${label}</dt><dd>${text(value)}</dd>`).join("");
}

function firstCoverUrl(payload) {
  const covers = payload.cover_art || [];
  const cover = covers.find((item) => item.is_front) || covers[0];
  return cover?.local_image_url || cover?.thumbnail_large || cover?.thumbnail_small || cover?.image_url || payload.cover_url || "";
}

function showRelease(payload) {
  currentRelease = payload;
  const album = payload.album || {};
  releaseTitle.textContent = album.album_name || "Untitled release";
  releaseArtist.textContent = album.artist || "Unknown artist";
  releaseImage.src = payload.cover_url || "/images/1190-logo-reversed-300x180.png";
  releaseImage.alt = `${releaseTitle.textContent} cover`;
  renderDetails(payload);
  populateDesktopForm(album);
  showDesktopCoverPreview(firstCoverUrl(payload));
  renderTrackEditorRows(desktopTrackRows, payload.tracks || []);
  renderTrackEditorRows(releaseTrackRows, payload.tracks || []);
  showScannerCover(payload);
  releaseCard.hidden = false;
  addReleaseButton.disabled = false;
  if (desktopTopAddButton) desktopTopAddButton.disabled = false;
}

function populateDesktopForm(album) {
  if (!desktopAddForm || !album) return;
  for (const [name, value] of Object.entries(album)) {
    const field = desktopAddForm.elements[name];
    if (!field) continue;
    if (field.type === "checkbox") {
      field.checked = Boolean(value);
    } else {
      field.value = value ?? "";
    }
  }
}

function populateDesktopFormFromPreview(payload, serviceUrl) {
  const album = payload.album || {};
  const external = payload.external || [];
  const match = external.find((item) => item.provider === "discogs" && item.lookup_status === "matched") || external[0] || {};
  populateDesktopForm({
    ...album,
    artist: match.artist || album.artist,
    album_name: match.title || album.album_name,
    music_service_url: serviceUrl,
  });
}

function resetDesktopAddForm() {
  if (!desktopAddForm) return;
  populateDesktopForm(defaultDesktopAlbumValues());
  if (desktopServiceMessage) desktopServiceMessage.textContent = "";
  desktopCoverDataUrl = "";
  showDesktopCoverPreview("");
  renderTrackEditorRows(desktopTrackRows, []);
  renderTrackEditorRows(releaseTrackRows, []);
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
        <span>Explicit</span>
        <input name="explicit" type="checkbox" ${track.explicit ? "checked" : ""} />
      </label>
      <button type="button" class="iconButton" data-remove-track-row aria-label="Remove track">×</button>
    </div>
  `;
}

function renderTrackEditorRows(rows, tracks = []) {
  if (!rows) return;
  rows.dataset.tracksLoaded = tracks.length ? "1" : "";
  const source = tracks.length ? tracks : [{ track_number: "1", title: "", explicit: false }];
  rows.innerHTML = source.map(renderTrackEditorRow).join("");
}

function addTrackEditorRow(rows, track = {}) {
  if (!rows) return;
  rows.dataset.tracksLoaded = "1";
  const index = rows.querySelectorAll(".trackEditRow").length;
  rows.insertAdjacentHTML("beforeend", renderTrackEditorRow(track, index));
}

function trackEditorValues(rows) {
  if (!rows) return [];
  return [...rows.querySelectorAll(".trackEditRow")]
    .map((row, index) => {
      const title = row.querySelector("input[name='title']")?.value.trim() || "";
      const trackNumber = row.querySelector("input[name='track_number']")?.value.trim() || String(index + 1);
      const explicit = Boolean(row.querySelector("input[name='explicit']")?.checked);
      return { track_number: trackNumber, title, explicit };
    })
    .filter((track) => track.title);
}

function activeTrackRows() {
  return isDesktopView() ? desktopTrackRows : releaseTrackRows;
}

function shouldSendTracks(rows, tracks) {
  return Boolean(rows?.dataset.tracksLoaded || tracks.length);
}

function showDesktopCoverPreview(url) {
  if (!desktopCoverPreview) return;
  if (!url) {
    desktopCoverPreview.hidden = true;
    desktopCoverPreview.innerHTML = "";
    return;
  }
  desktopCoverPreview.hidden = false;
  desktopCoverPreview.innerHTML = `<img src="${escapeAttribute(url)}" alt="Album cover preview" />`;
}

function setFieldInvalid(control, invalid = true) {
  if (!control) return;
  control.classList.toggle("isInvalid", invalid);
  control.closest("label")?.classList.toggle("isInvalid", invalid);
}

function clearInvalidState() {
  document.querySelectorAll(".isInvalid").forEach((element) => element.classList.remove("isInvalid"));
}

function desktopFormAlbum() {
  if (!desktopAddForm || !isDesktopView()) {
    return { ...(currentRelease?.album || {}), catalog_number: mobileCatalogInput.value.trim() };
  }
  const album = {};
  for (const element of desktopAddForm.elements) {
    if (!element.name) continue;
    album[element.name] = element.type === "checkbox" ? element.checked : element.value.trim();
  }
  return album;
}

async function publishScan(payload) {
  if (!payload || isDesktopView()) return;
  try {
    await fetch("/api/scan-events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ release: payload, source: "mobile" }),
    });
  } catch {
    // The phone can still add locally even if a desktop listener is not reachable.
  }
}

async function lookupBarcode(barcode) {
  const clean = cleanBarcode(barcode);
  if (!clean) {
    setStatus("Enter or scan a UPC barcode.", true);
    return;
  }
  if (lookupInProgress) return;
  lookupInProgress = true;
  setStatus("Looking up Discogs release...");
  releaseCard.hidden = true;
  try {
    const response = await fetch("/api/discogs-barcode-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ barcode: clean }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Discogs lookup failed.");
    barcodeInput.value = payload.barcode || clean;
    showRelease(payload);
    await publishScan(payload);
    setStatus("Release found. Add an 1190_ID, then hit Add Album.");
  } catch (error) {
    currentRelease = null;
    setStatus(error.message, true);
  } finally {
    lookupInProgress = false;
  }
}

async function addCurrentRelease() {
  if (!currentRelease) return;
  if (!canEditCatalog()) {
    setStatus("Editor role required to add albums.", true);
    return;
  }
  const album = desktopFormAlbum();
  const rows = activeTrackRows();
  const tracks = trackEditorValues(rows);
  clearInvalidState();
  if (!album.catalog_number) {
    setStatus("1190_ID is required.", true);
    if (isDesktopView()) {
      setFieldInvalid(desktopAddForm?.elements.catalog_number);
      desktopAddForm?.elements.catalog_number?.focus();
    } else {
      setFieldInvalid(mobileCatalogInput);
      mobileCatalogInput.focus();
    }
    return;
  }
  if (!currentRelease.release_url) {
    setStatus("Discogs release URL is missing.", true);
    return;
  }
  addReleaseButton.disabled = true;
  if (desktopTopAddButton) desktopTopAddButton.disabled = true;
  setStatus("Adding album...");
  try {
    const response = await fetch("/api/albums", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        album,
        music_service_url: currentRelease.release_url,
        cover_data_url: desktopCoverDataUrl,
        ...(shouldSendTracks(rows, tracks) ? { tracks } : {}),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to add album.");
    const addedAlbum = payload.album || album;
    const albumName = addedAlbum.album_name || album.album_name || "Album";
    const artist = addedAlbum.artist || album.artist || "Unknown artist";
    currentRelease = null;
    releaseCard.hidden = true;
    barcodeInput.value = "";
    if (mobileCatalogInput) mobileCatalogInput.value = "";
    resetDesktopAddForm();
    clearReleaseState();
    showScannerVideo();
    if (desktopListenerStatus) {
      desktopListenerStatus.textContent = "Listening for mobile scans...";
    }
    setStatus(`${albumName} by ${artist} has been added to the catalog`);
    barcodeInput.focus();
  } catch (error) {
    setStatus(error.message, true);
    addReleaseButton.disabled = false;
    if (desktopTopAddButton) desktopTopAddButton.disabled = false;
  }
}

async function loadDesktopServiceUrl() {
  if (!desktopAddForm) return;
  const serviceUrl = desktopAddForm.elements.music_service_url?.value.trim();
  setFieldInvalid(desktopAddForm.elements.music_service_url, false);
  if (!serviceUrl) {
    setFieldInvalid(desktopAddForm.elements.music_service_url);
    if (desktopServiceMessage) desktopServiceMessage.textContent = "Enter a Discogs release or master URL.";
    return;
  }
  if (desktopServiceMessage) desktopServiceMessage.textContent = "Looking up Discogs data...";
  if (desktopLoadServiceButton) desktopLoadServiceButton.disabled = true;
  try {
    const album = desktopFormAlbum();
    const response = await fetch("/api/music-service-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: serviceUrl,
        artist: album.artist,
        album_name: album.album_name,
        format: album.format || "CD",
        compilation: album.compilation,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to load music service data.");
    const previewAlbum = payload.album || {};
    const releaseUrl = serviceUrl;
    currentRelease = {
      ...payload,
      album: previewAlbum,
      cover_url: firstCoverUrl(payload),
      release_url: releaseUrl,
      track_count: payload.tracks?.length || 0,
      barcode: "",
    };
    populateDesktopFormFromPreview(payload, serviceUrl);
    showRelease(currentRelease);
    if (desktopServiceMessage) desktopServiceMessage.textContent = "Loaded Discogs data. Review the fields before saving.";
    if (desktopListenerStatus) desktopListenerStatus.textContent = "Discogs URL loaded. Review the form, then click Add.";
    setStatus("Album info loaded. Add an 1190_ID, then hit Add Album.");
  } catch (error) {
    if (desktopServiceMessage) desktopServiceMessage.textContent = error.message;
  } finally {
    if (desktopLoadServiceButton) desktopLoadServiceButton.disabled = false;
  }
}

async function lazyLoadDesktopScanTracks(release) {
  if (!isDesktopView() || !release?.release_url) return;
  if (release.tracks?.length) {
    renderTrackEditorRows(desktopTrackRows, release.tracks);
  }
  if (desktopListenerStatus) {
    desktopListenerStatus.textContent = "Loading track list...";
  }
  try {
    const response = await fetch("/api/music-service-match-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: release.release_url,
        album: {
          ...(release.album || {}),
          ...desktopFormAlbum(),
        },
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to load track list.");
    const tracks = payload.tracks || [];
    currentRelease = {
      ...(currentRelease || release),
      tracks,
      track_count: tracks.length,
    };
    renderDetails(currentRelease);
    renderTrackEditorRows(desktopTrackRows, tracks);
    if (desktopListenerStatus) {
      desktopListenerStatus.textContent = tracks.length
        ? "Track list loaded. Review the form, then click Add."
        : "No track list found. Review the form, then click Add.";
    }
  } catch (error) {
    if (desktopListenerStatus) {
      desktopListenerStatus.textContent = `${error.message} Review the form, then click Add.`;
    }
  }
}

async function pollScanEvents() {
  if (!isDesktopView()) return;
  try {
    const response = await fetch(`/api/scan-events?after=${lastScanEventId}`);
    if (response.status === 401) {
      window.location.href = "/login.html";
      return;
    }
    const payload = await response.json();
    for (const event of payload.events || []) {
      lastScanEventId = Math.max(lastScanEventId, event.id);
      if (event.release) {
        showRelease(event.release);
        setStatus("Barcode scan received from your mobile device.");
        if (desktopListenerStatus) {
          desktopListenerStatus.textContent = "Latest scan loaded. Review the form, then click Add.";
        }
        lazyLoadDesktopScanTracks(event.release);
      }
    }
  } catch {
    if (desktopListenerStatus) {
      desktopListenerStatus.textContent = "Listening for mobile scans...";
    }
  }
}

async function initializeScanListener() {
  if (!isDesktopView()) return;
  try {
    const response = await fetch("/api/scan-events?latest=1");
    if (response.status === 401) {
      window.location.href = "/login.html?next=%2Fadd.html";
      return;
    }
    const payload = await response.json();
    lastScanEventId = payload.latest_id || 0;
    setStatus("");
    if (desktopListenerStatus) {
      desktopListenerStatus.textContent = "Listening for mobile scans...";
    }
  } catch {
    if (desktopListenerStatus) {
      desktopListenerStatus.textContent = "Listening for mobile scans...";
    }
  }
}

function stopScanner() {
  cancelAnimationFrame(scanFrame);
  scanFrame = 0;
  if (zxingControls) {
    zxingControls.stop();
    zxingControls = null;
  }
  zxingCameraTuned = false;
  if (zxingReader?.reset) {
    zxingReader.reset();
  }
  if (scanStream) {
    scanStream.getTracks().forEach((track) => track.stop());
    scanStream = null;
  }
  videoEl.srcObject = null;
  if (!currentRelease) showScannerVideo();
  startScanButton.disabled = false;
  stopScanButton.disabled = true;
}

function resetForNewScan() {
  stopScanner();
  clearReleaseState();
  resetDesktopAddForm();
  showScannerVideo();
  barcodeInput.value = "";
  if (mobileCatalogInput) mobileCatalogInput.value = "";
  clearInvalidState();
  setStatus("");
  if (desktopListenerStatus) {
    desktopListenerStatus.textContent = "Listening for mobile scans...";
  }
}

function handleScannedBarcode(value) {
  const clean = cleanBarcode(value);
  if (!clean || lookupInProgress) return;
  stopScanner();
  barcodeInput.value = clean;
  lookupBarcode(clean);
}

async function tuneCameraForCloseScan(stream) {
  const track = stream?.getVideoTracks?.()[0];
  if (!track?.getCapabilities || !track.applyConstraints) return;
  const capabilities = track.getCapabilities();
  const advanced = [];
  if (capabilities.focusMode?.includes("continuous")) {
    advanced.push({ focusMode: "continuous" });
  }
  if (typeof capabilities.zoom?.min === "number" && typeof capabilities.zoom?.max === "number") {
    const zoom = Math.min(capabilities.zoom.max, Math.max(capabilities.zoom.min, 1.5));
    advanced.push({ zoom });
  }
  if (!advanced.length) return;
  try {
    await track.applyConstraints({ advanced });
  } catch {
    // Browsers expose camera controls unevenly. Scanning still works without these hints.
  }
}

async function scanLoop() {
  if (!barcodeDetector || !scanStream || lookupInProgress) return;
  try {
    const codes = await barcodeDetector.detect(videoEl);
    const value = cleanBarcode(codes[0]?.rawValue);
    if (value) {
      handleScannedBarcode(value);
      return;
    }
  } catch {
    stopScanner();
    setStatus("Barcode scanning stopped. Enter the UPC manually.", true);
    return;
  }
  scanFrame = requestAnimationFrame(scanLoop);
}

async function startNativeScanner() {
  const formats = await supportedNativeBarcodeFormats();
  if (!formats.length) {
    throw new Error("Native scanner does not support UPC/EAN formats.");
  }
  barcodeDetector = barcodeDetector || new BarcodeDetector({ formats });
  scanStream = await navigator.mediaDevices.getUserMedia({
    video: SCANNER_VIDEO_CONSTRAINTS,
    audio: false,
  });
  await tuneCameraForCloseScan(scanStream);
  videoEl.srcObject = scanStream;
  await videoEl.play();
  startScanButton.disabled = true;
  stopScanButton.disabled = false;
  setStatus("Point the camera at the UPC barcode.");
  scanFrame = requestAnimationFrame(scanLoop);
}

async function startZxingScanner() {
  const { BrowserMultiFormatReader } = window.ZXingBrowser || {};
  if (!BrowserMultiFormatReader) {
    throw new Error("Live barcode scanning is not available in this browser. Enter the UPC manually.");
  }
  zxingReader = zxingReader || new BrowserMultiFormatReader();
  zxingCameraTuned = false;
  startScanButton.disabled = true;
  stopScanButton.disabled = false;
  setStatus("Point the camera at the UPC barcode.");
  zxingControls = await zxingReader.decodeFromConstraints({ video: SCANNER_VIDEO_CONSTRAINTS, audio: false }, videoEl, async (result, error, controls) => {
    if (controls && !zxingControls) zxingControls = controls;
    if (!zxingCameraTuned && controls?.streamVideoConstraintsApply) {
      zxingCameraTuned = true;
      try {
        await controls.streamVideoConstraintsApply({ advanced: [{ focusMode: "continuous" }, { zoom: 1.5 }] });
      } catch {
        // Optional close-scan camera tuning is best effort.
      }
    }
    if (!result) return;
    handleScannedBarcode(result.getText?.() || result.text || result.rawValue);
  });
}

async function startScanner() {
  resetForNewScan();
  if (isDesktopView()) return;
  if (!window.isSecureContext) {
    setStatus("Camera scanning requires HTTPS or localhost. Enter the UPC manually on this connection.", true);
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    setStatus("This browser cannot access the camera. Enter the UPC manually.", true);
    return;
  }
  if (!hasLiveScanner()) {
    setStatus("Live barcode scanning is not available in this browser. Enter the UPC manually.", true);
    return;
  }
  try {
    if ((await supportedNativeBarcodeFormats()).length) {
      await startNativeScanner();
    } else {
      await startZxingScanner();
    }
  } catch (error) {
    stopScanner();
    setStatus(error.message || "Camera access failed. Enter the UPC manually.", true);
  }
}

lookupForm.addEventListener("submit", (event) => {
  event.preventDefault();
  stopScanner();
  lookupBarcode(barcodeInput.value);
});

startScanButton.addEventListener("click", startScanner);
stopScanButton.addEventListener("click", stopScanner);
desktopClearButton?.addEventListener("click", resetForNewScan);
addReleaseButton.addEventListener("click", addCurrentRelease);
desktopTopAddButton?.addEventListener("click", addCurrentRelease);
desktopLoadServiceButton?.addEventListener("click", loadDesktopServiceUrl);

function setAccountMenuOpen(open) {
  if (!accountMenuButton || !accountMenu) return;
  accountMenu.hidden = !open;
  accountMenuButton.setAttribute("aria-expanded", String(open));
}

function toggleAccountMenu() {
  setAccountMenuOpen(accountMenu?.hidden);
}

accountMenuButton?.addEventListener("click", toggleAccountMenu);
document.addEventListener("click", (event) => {
  if (!accountMenu || accountMenu.hidden) return;
  if (event.target.closest(".accountMenuWrap")) return;
  setAccountMenuOpen(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") setAccountMenuOpen(false);
});

document.addEventListener("click", (event) => {
  const addTrackButton = event.target.closest("[data-add-track-row]");
  if (addTrackButton) {
    const target = addTrackButton.dataset.trackTarget === "release" ? releaseTrackRows : desktopTrackRows;
    addTrackEditorRow(target);
    return;
  }
  const removeTrackButton = event.target.closest("[data-remove-track-row]");
  if (removeTrackButton) {
    const rows = removeTrackButton.closest("[data-track-rows]");
    removeTrackButton.closest(".trackEditRow")?.remove();
    if (rows && !rows.querySelector(".trackEditRow")) addTrackEditorRow(rows);
  }
});

desktopAddForm?.addEventListener("change", (event) => {
  const input = event.target.closest("input[type='file'][name='cover']");
  if (!input) return;
  const file = input.files?.[0];
  desktopCoverDataUrl = "";
  if (!file) {
    showDesktopCoverPreview(firstCoverUrl(currentRelease || {}));
    return;
  }
  const reader = new FileReader();
  reader.addEventListener("load", () => {
    desktopCoverDataUrl = String(reader.result || "");
    showDesktopCoverPreview(desktopCoverDataUrl);
  });
  reader.readAsDataURL(file);
});

async function logout() {
  await fetch("/api/logout", { method: "POST" });
  window.location.href = "/login.html?next=%2Fadd.html";
}

logoutButton?.addEventListener("click", logout);

async function loadSession() {
  const response = await fetch("/api/session");
  if (response.status === 401) {
    window.location.href = "/login.html?next=%2Fadd.html";
    return;
  }
  const payload = await response.json();
  currentRoles = payload.roles || currentRoles;
  if (sessionUser) sessionUser.textContent = payload.username || "";
  if (desktopQrUser) desktopQrUser.textContent = payload.username || "this user";
  if (adminLink) adminLink.hidden = !currentRoles.admin;
  addReleaseButton.hidden = !canEditCatalog();
  if (desktopTopAddButton) desktopTopAddButton.hidden = !canEditCatalog();
}

resetDesktopAddForm();
loadSession().then(async () => {
  await initializeScanListener();
  setInterval(pollScanEvents, 1800);
});
