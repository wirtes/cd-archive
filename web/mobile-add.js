const videoEl = document.querySelector("#scannerVideo");
const startScanButton = document.querySelector("#startScanButton");
const stopScanButton = document.querySelector("#stopScanButton");
const scannerHint = document.querySelector("#scannerHint");
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
const desktopAddForm = document.querySelector("#desktopAddForm");
const desktopListenerStatus = document.querySelector("#desktopListenerStatus");

let barcodeDetector = null;
let zxingReader = null;
let zxingControls = null;
let scanStream = null;
let scanFrame = 0;
let currentRelease = null;
let lookupInProgress = false;
let lastScanEventId = 0;
let currentRoles = { admin: false, editor: false };

const UPC_FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e"];
const isDesktopView = () => window.matchMedia("(min-width: 760px)").matches;
const canEditCatalog = () => Boolean(currentRoles.admin || currentRoles.editor);
const hasNativeScanner = () => "BarcodeDetector" in window;
const hasZxingScanner = () => Boolean(window.ZXingBrowser?.BrowserMultiFormatReader);
const hasLiveScanner = () => hasNativeScanner() || hasZxingScanner();

function setStatus(message, isError = false) {
  statusMessage.textContent = message || "";
  statusMessage.classList.toggle("isError", Boolean(message && isError));
}

function cleanBarcode(value) {
  return String(value || "").replace(/\D/g, "");
}

function text(value) {
  return value === null || value === undefined || value === "" ? "N/A" : String(value);
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

function showRelease(payload) {
  currentRelease = payload;
  const album = payload.album || {};
  releaseTitle.textContent = album.album_name || "Untitled release";
  releaseArtist.textContent = album.artist || "Unknown artist";
  releaseImage.src = payload.cover_url || "/images/1190-logo-reversed-300x180.png";
  releaseImage.alt = `${releaseTitle.textContent} cover`;
  renderDetails(payload);
  populateDesktopForm(album);
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
    setStatus("Release found. Review it, then tap Add.");
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
  if (!album.catalog_number) {
    setStatus("1190_ID is required.", true);
    if (isDesktopView()) {
      desktopAddForm?.elements.catalog_number?.focus();
    } else {
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
    if (desktopListenerStatus) {
      desktopListenerStatus.textContent = "Waiting for mobile scans...";
    }
    setStatus(`${albumName} by ${artist} has been added to the catalog`);
    barcodeInput.focus();
  } catch (error) {
    setStatus(error.message, true);
    addReleaseButton.disabled = false;
    if (desktopTopAddButton) desktopTopAddButton.disabled = false;
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
      }
    }
  } catch {
    if (desktopListenerStatus) {
      desktopListenerStatus.textContent = "Waiting for mobile scans...";
    }
  }
}

async function initializeScanListener() {
  if (!isDesktopView()) return;
  try {
    const response = await fetch("/api/scan-events?latest=1");
    if (response.status === 401) {
      window.location.href = "/login.html?next=%2Fmobile-add.html";
      return;
    }
    const payload = await response.json();
    lastScanEventId = payload.latest_id || 0;
    setStatus("");
    if (desktopListenerStatus) {
      desktopListenerStatus.textContent = "Waiting for mobile scans...";
    }
  } catch {
    if (desktopListenerStatus) {
      desktopListenerStatus.textContent = "Waiting for mobile scans...";
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
  if (zxingReader?.reset) {
    zxingReader.reset();
  }
  if (scanStream) {
    scanStream.getTracks().forEach((track) => track.stop());
    scanStream = null;
  }
  videoEl.srcObject = null;
  startScanButton.disabled = false;
  stopScanButton.disabled = true;
}

function handleScannedBarcode(value) {
  const clean = cleanBarcode(value);
  if (!clean || lookupInProgress) return;
  stopScanner();
  barcodeInput.value = clean;
  lookupBarcode(clean);
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
    video: { facingMode: { ideal: "environment" } },
    audio: false,
  });
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
  startScanButton.disabled = true;
  stopScanButton.disabled = false;
  setStatus("Point the camera at the UPC barcode.");
  zxingControls = await zxingReader.decodeFromVideoDevice(null, videoEl, (result, error, controls) => {
    if (controls && !zxingControls) zxingControls = controls;
    if (!result) return;
    handleScannedBarcode(result.getText?.() || result.text || result.rawValue);
  });
}

async function startScanner() {
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
addReleaseButton.addEventListener("click", addCurrentRelease);
desktopTopAddButton?.addEventListener("click", addCurrentRelease);

async function initializeScannerHint() {
  const nativeFormats = await supportedNativeBarcodeFormats();
  if (!nativeFormats.length && !hasZxingScanner()) {
    scannerHint.textContent = "Live scanning is not available in this browser. Enter the UPC manually.";
  } else if (!nativeFormats.length && hasZxingScanner()) {
    scannerHint.textContent = "Live UPC scanning is available through the built-in ZXing scanner.";
  }
}

async function loadSession() {
  const response = await fetch("/api/session");
  if (response.status === 401) {
    window.location.href = "/login.html?next=%2Fmobile-add.html";
    return;
  }
  const payload = await response.json();
  currentRoles = payload.roles || currentRoles;
  addReleaseButton.hidden = !canEditCatalog();
  if (desktopTopAddButton) desktopTopAddButton.hidden = !canEditCatalog();
}

populateDesktopForm({
  timestamp: new Date().toISOString().slice(0, 19).replace("T", " "),
  format: "CD",
  case_broken: "No",
});
initializeScannerHint();
loadSession().then(async () => {
  await initializeScanListener();
  setInterval(pollScanEvents, 1800);
});
