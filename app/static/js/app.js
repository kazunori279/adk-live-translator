/**
 * app.js: JS code for the Live Translator app.
 */

/**
 * WebSocket handling
 */

const userId = "demo-user";
let sessionId = "demo-session-" + Math.random().toString(36).substring(7);
let websocket = null;
let is_audio = false;
let pttMode = false;
let audioInitialized = false;

const sourceLangSelect = document.getElementById("sourceLang");
const targetLangSelect = document.getElementById("targetLang");

// Hide subtitle when it would overlap controls or when header wraps
{
  const subtitle = document.querySelector(".subtitle");
  const controls = document.querySelector(".header-controls");
  const titleEl = document.querySelector("header h1");
  if (subtitle && controls && titleEl) {
    const check = () => {
      subtitle.hidden = false;
      const sr = subtitle.getBoundingClientRect();
      const cr = controls.getBoundingClientRect();
      const tr = titleEl.getBoundingClientRect();
      const overlaps = sr.top < cr.bottom && sr.bottom > cr.top && sr.right > cr.left;
      const headerWrapped = cr.top > tr.bottom - 4;
      const subtitleWraps = sr.bottom > tr.bottom + 4;
      subtitle.hidden = overlaps || headerWrapped || subtitleWraps;
    };
    requestAnimationFrame(check);
    window.addEventListener("resize", check);
  }
}

// Custom dropdown logic
function setupCustomSelect(hiddenInput, trigger, dropdown, defaultCode, languages, popular, allCodes) {
  dropdown.innerHTML = "";

  function addOption(code) {
    const div = document.createElement("div");
    div.className = "custom-select-option";
    if (code === defaultCode) div.classList.add("selected");
    div.textContent = languages[code];
    div.dataset.value = code;
    div.addEventListener("click", () => {
      hiddenInput.value = code;
      trigger.textContent = languages[code];
      dropdown.querySelectorAll(".custom-select-option").forEach(o => o.classList.remove("selected"));
      div.classList.add("selected");
      dropdown.classList.remove("open");
      reconnectWithNewLanguage();
    });
    dropdown.appendChild(div);
  }

  for (const code of popular) addOption(code);
  const divider = document.createElement("div");
  divider.className = "custom-select-divider";
  dropdown.appendChild(divider);
  for (const code of allCodes) addOption(code);

  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    // Close other dropdowns
    document.querySelectorAll(".custom-select-dropdown.open").forEach(d => {
      if (d !== dropdown) d.classList.remove("open");
    });
    dropdown.classList.toggle("open");
    // Scroll to selected item
    const selected = dropdown.querySelector(".selected");
    if (selected) selected.scrollIntoView({ block: "center" });
  });
}

// Swap languages
document.getElementById("swapLangs").addEventListener("click", () => {
  const srcVal = sourceLangSelect.value;
  const tgtVal = targetLangSelect.value;
  const srcTrigger = document.getElementById("sourceLangTrigger");
  const tgtTrigger = document.getElementById("targetLangTrigger");
  const srcText = srcTrigger.textContent;
  const tgtText = tgtTrigger.textContent;
  sourceLangSelect.value = tgtVal;
  targetLangSelect.value = srcVal;
  srcTrigger.textContent = tgtText;
  tgtTrigger.textContent = srcText;
  // Update selected states in dropdowns
  document.getElementById("sourceLangDropdown").querySelectorAll(".custom-select-option").forEach(o => {
    o.classList.toggle("selected", o.dataset.value === tgtVal);
  });
  document.getElementById("targetLangDropdown").querySelectorAll(".custom-select-option").forEach(o => {
    o.classList.toggle("selected", o.dataset.value === srcVal);
  });
  reconnectWithNewLanguage();
});

// Close dropdowns on outside click
document.addEventListener("click", () => {
  document.querySelectorAll(".custom-select-dropdown.open").forEach(d => d.classList.remove("open"));
});

// Populate language selectors from API
async function loadLanguages() {
  const resp = await fetch("/api/languages");
  const { languages, popular } = await resp.json();
  const allCodes = Object.keys(languages).sort((a, b) => languages[a].localeCompare(languages[b]));

  setupCustomSelect(
    sourceLangSelect, document.getElementById("sourceLangTrigger"),
    document.getElementById("sourceLangDropdown"), "en", languages, popular, allCodes
  );
  setupCustomSelect(
    targetLangSelect, document.getElementById("targetLangTrigger"),
    document.getElementById("targetLangDropdown"), "ja", languages, popular, allCodes
  );
}
loadLanguages();

function getWebSocketUrl() {
  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const source = sourceLangSelect.value;
  const target = targetLangSelect.value;
  return wsProtocol + "//" + window.location.host + "/ws/" + userId + "/" + sessionId + "?source=" + source + "&target=" + target;
}

// Get DOM elements
const messagesDiv = document.getElementById("messages");
const statusIndicator = document.getElementById("statusIndicator");
const statusText = document.getElementById("statusText");
let currentMessageId = null;
let currentBubbleElement = null;
let currentInputTranscriptionId = null;
let currentInputTranscriptionElement = null;
let currentInputRawText = "";
let currentOutputTranscriptionId = null;
let currentOutputTranscriptionElement = null;
let currentOutputRawText = "";
let inputTranscriptionFinished = false;
let hasOutputTranscriptionInTurn = false;

// Helper function to clean spaces between CJK characters
function cleanCJKSpaces(text) {
  const cjkPattern = /[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf\uff00-\uffef]/;
  return text.replace(/(\S)\s+(?=\S)/g, (match, char1) => {
    const nextCharMatch = text.match(new RegExp(char1 + '\\s+(.)', 'g'));
    if (nextCharMatch && nextCharMatch.length > 0) {
      const char2 = nextCharMatch[0].slice(-1);
      if (cjkPattern.test(char1) && cjkPattern.test(char2)) {
        return char1;
      }
    }
    return match;
  });
}

function updateConnectionStatus(status) {
  if (status === "connected") {
    statusIndicator.classList.remove("disconnected");
    statusIndicator.classList.remove("connecting");
    statusText.textContent = "Connected";
  } else if (status === "connecting") {
    statusIndicator.classList.remove("disconnected");
    statusIndicator.classList.add("connecting");
    statusText.textContent = "Connecting...";
  } else {
    statusIndicator.classList.add("disconnected");
    statusIndicator.classList.remove("connecting");
    statusText.textContent = "Disconnected";
  }
}

function createMessageBubble(text, isUser, isPartial = false) {
  const messageDiv = document.createElement("div");
  messageDiv.className = `message ${isUser ? "user" : "agent"}`;

  const bubbleDiv = document.createElement("div");
  bubbleDiv.className = "bubble";

  const textP = document.createElement("p");
  textP.className = "bubble-text";
  textP.textContent = text;

  if (isPartial && !isUser) {
    const typingSpan = document.createElement("span");
    typingSpan.className = "typing-indicator";
    textP.appendChild(typingSpan);
  }

  bubbleDiv.appendChild(textP);
  messageDiv.appendChild(bubbleDiv);
  return messageDiv;
}

function updateMessageBubble(element, text, isPartial = false) {
  const textElement = element.querySelector(".bubble-text");
  const existingIndicator = textElement.querySelector(".typing-indicator");
  if (existingIndicator) existingIndicator.remove();

  textElement.textContent = text;

  if (isPartial) {
    const typingSpan = document.createElement("span");
    typingSpan.className = "typing-indicator";
    textElement.appendChild(typingSpan);
  }
}

function addSystemMessage(text) {
  const messageDiv = document.createElement("div");
  messageDiv.className = "system-message";
  messageDiv.textContent = text;
  messagesDiv.appendChild(messageDiv);
  scrollToBottom();
  return messageDiv;
}

function scrollToBottom() {
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

let connectingMsg = null;

// WebSocket handlers
function connectWebsocket() {
  const ws_url = getWebSocketUrl();
  websocket = new WebSocket(ws_url);
  if (connectingMsg) connectingMsg.remove();
  connectingMsg = addSystemMessage("Connecting...");

  websocket.onopen = function () {
    updateConnectionStatus("connected");
    if (connectingMsg) {
      connectingMsg.remove();
      connectingMsg = null;
    }
    startAudioButton.disabled = false;
    pttToggle.disabled = false;
    // First message must be the setup payload (carries the per-browser glossary).
    websocket.send(JSON.stringify({ glossary: getGlossary() }));
  };

  websocket.onmessage = function (event) {
    const serverMsg = JSON.parse(event.data);

    // Handle turn complete
    if (serverMsg.turnComplete === true) {
      if (currentBubbleElement) {
        const ti = currentBubbleElement.querySelector(".typing-indicator");
        if (ti) ti.remove();
      }
      if (currentOutputTranscriptionElement) {
        const ti = currentOutputTranscriptionElement.querySelector(".typing-indicator");
        if (ti) ti.remove();
      }
      currentMessageId = null;
      currentBubbleElement = null;
      currentInputTranscriptionId = null;
      currentInputTranscriptionElement = null;
      currentInputRawText = "";
      currentOutputTranscriptionId = null;
      currentOutputTranscriptionElement = null;
      currentOutputRawText = "";
      inputTranscriptionFinished = false;
      hasOutputTranscriptionInTurn = false;
      return;
    }

    // Handle input transcription (user's spoken words)
    if (serverMsg.inputTranscription && serverMsg.inputTranscription.text) {
      const transcriptionText = serverMsg.inputTranscription.text;
      const isFinished = serverMsg.inputTranscription.finished;

      if (transcriptionText && !inputTranscriptionFinished) {
        if (currentInputTranscriptionId == null) {
          currentInputTranscriptionId = Math.random().toString(36).substring(7);
          currentInputRawText = transcriptionText;
          currentInputTranscriptionElement = createMessageBubble(cleanCJKSpaces(currentInputRawText), true, !isFinished);
          currentInputTranscriptionElement.id = currentInputTranscriptionId;
          currentInputTranscriptionElement.classList.add("transcription");
          messagesDiv.appendChild(currentInputTranscriptionElement);
        } else {
          if (isFinished) {
            currentInputRawText = transcriptionText;
          } else {
            currentInputRawText += transcriptionText;
          }
          updateMessageBubble(currentInputTranscriptionElement, cleanCJKSpaces(currentInputRawText), !isFinished);
        }

        if (isFinished) {
          currentInputTranscriptionId = null;
          currentInputTranscriptionElement = null;
          currentInputRawText = "";
          inputTranscriptionFinished = true;
        }
        scrollToBottom();
      }
    }

    // Handle output transcription (translated speech)
    if (serverMsg.outputTranscription && serverMsg.outputTranscription.text) {
      const transcriptionText = serverMsg.outputTranscription.text;
      const isFinished = serverMsg.outputTranscription.finished;
      hasOutputTranscriptionInTurn = true;

      if (transcriptionText) {
        if (currentOutputTranscriptionId == null) {
          currentOutputTranscriptionId = Math.random().toString(36).substring(7);
          currentOutputRawText = transcriptionText;
          currentOutputTranscriptionElement = createMessageBubble(applyDisplayMap(currentOutputRawText), false, !isFinished);
          currentOutputTranscriptionElement.id = currentOutputTranscriptionId;
          currentOutputTranscriptionElement.classList.add("transcription");
          messagesDiv.appendChild(currentOutputTranscriptionElement);
        } else {
          if (isFinished) {
            currentOutputRawText = transcriptionText;
            updateMessageBubble(currentOutputTranscriptionElement, applyDisplayMap(currentOutputRawText), false);
          } else {
            currentOutputRawText += transcriptionText;
            updateMessageBubble(currentOutputTranscriptionElement, applyDisplayMap(currentOutputRawText), true);
          }
        }

        if (isFinished) {
          currentOutputTranscriptionId = null;
          currentOutputTranscriptionElement = null;
          currentOutputRawText = "";
        }
        scrollToBottom();
      }
    }

    // Handle content events (text or audio)
    if (serverMsg.content && serverMsg.content.parts) {
      const parts = serverMsg.content.parts;

      for (const part of parts) {
        if (part.inlineData) {
          const mimeType = part.inlineData.mimeType;
          const data = part.inlineData.data;
          if (mimeType && mimeType.startsWith("audio/pcm") && audioPlayerNode) {
            audioPlayerNode.port.postMessage(base64ToArray(data));
          }
        }

        if (part.text) {
          if (part.thought) continue;
          if (!serverMsg.partial && hasOutputTranscriptionInTurn) continue;

          if (currentMessageId == null) {
            currentMessageId = Math.random().toString(36).substring(7);
            currentBubbleElement = createMessageBubble(part.text, false, true);
            currentBubbleElement.id = currentMessageId;
            messagesDiv.appendChild(currentBubbleElement);
          } else {
            const existingText = currentBubbleElement.querySelector(".bubble-text").textContent;
            const cleanText = existingText.replace(/\.\.\.$/, '');
            updateMessageBubble(currentBubbleElement, cleanText + part.text, true);
          }
          scrollToBottom();
        }
      }
    }
  };

  websocket.onclose = function () {
    updateConnectionStatus("disconnected");
    startAudioButton.disabled = true;
    pttToggle.disabled = true;
    if (connectingMsg) connectingMsg.remove();
    connectingMsg = addSystemMessage("Connecting...");
    setTimeout(() => { connectWebsocket(); }, 5000);
  };

  websocket.onerror = function (e) {
    updateConnectionStatus("disconnected");
  };
}
connectWebsocket();

function reconnectWithNewLanguage() {
  sessionId = "demo-session-" + Math.random().toString(36).substring(7);
  updateConnectionStatus("connecting");
  startAudioButton.disabled = true;
  pttToggle.disabled = true;
  if (websocket) {
    websocket.onclose = null;
    websocket.close();
  }
  messagesDiv.innerHTML = '';
  const srcName = document.getElementById("sourceLangTrigger").textContent;
  const tgtName = document.getElementById("targetLangTrigger").textContent;
  addSystemMessage(`Language changed: ${srcName} → ${tgtName}`);
  connectWebsocket();
}

function base64ToArray(base64) {
  let standardBase64 = base64.replace(/-/g, '+').replace(/_/g, '/');
  while (standardBase64.length % 4) standardBase64 += '=';
  const binaryString = window.atob(standardBase64);
  const len = binaryString.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = binaryString.charCodeAt(i);
  return bytes.buffer;
}

/**
 * Audio handling
 */

let audioPlayerNode;
let audioPlayerContext;
let audioRecorderNode;
let audioRecorderContext;
let micStream;

import { startAudioPlayerWorklet } from "./audio-player.js";
import { startAudioRecorderWorklet } from "./audio-recorder.js";

function startAudio() {
  const inputId = getSavedInputDevice();
  const outputId = getSavedOutputDevice();
  startAudioPlayerWorklet(outputId).then(([node, ctx]) => {
    audioPlayerNode = node;
    audioPlayerContext = ctx;
  });
  const loadingOverlay = document.getElementById("loadingOverlay");
  loadingOverlay.classList.remove("hidden");
  startAudioRecorderWorklet(audioRecorderHandler, inputId).then(([node, ctx, stream]) => {
    audioRecorderNode = node;
    audioRecorderContext = ctx;
    micStream = stream;
    setTimeout(() => {
      loadingOverlay.classList.add("hidden");
      const { src, tgt } = getLanguageNames();
      addSystemMessage(`Ready for ${src} to ${tgt} translation`);
      if (pttMode) {
        startAudioButton.disabled = false;
        is_audio = false;
      }
    }, 3000);
  });
}

const startAudioButton = document.getElementById("startAudioButton");
const pttToggle = document.getElementById("pttToggle");

function initAudioIfNeeded() {
  if (audioInitialized) return;
  audioInitialized = true;
  startAudio();
}

function getLanguageNames() {
  const src = document.getElementById("sourceLangTrigger").textContent;
  const tgt = document.getElementById("targetLangTrigger").textContent;
  return { src, tgt };
}

// Always-on mode: click Start
startAudioButton.addEventListener("click", () => {
  if (pttMode) return;
  startAudioButton.disabled = true;
  initAudioIfNeeded();
  is_audio = true;
});

// PTT toggle
pttToggle.addEventListener("change", () => {
  pttMode = pttToggle.checked;
  if (pttMode) {
    startAudioButton.classList.add("ptt-mode");
    if (!audioInitialized) {
      startAudioButton.disabled = true;
      startAudioButton.textContent = "Hold to Talk";
      initAudioIfNeeded();
      is_audio = true;
    } else {
      startAudioButton.disabled = false;
      startAudioButton.textContent = "Hold to Talk";
      is_audio = false;
    }
  } else {
    startAudioButton.classList.remove("ptt-mode");
    startAudioButton.classList.remove("ptt-active");
    startAudioButton.textContent = "Start";
    is_audio = false;
    audioInitialized = false;
    reconnectWithNewLanguage();
  }
});

// PTT hold handlers
function pttDown(e) {
  if (!pttMode || startAudioButton.disabled) return;
  e.preventDefault();
  if (pttTailTimeout) { clearTimeout(pttTailTimeout); pttTailTimeout = null; }
  is_audio = true;
  startAudioButton.classList.add("ptt-active");
  startAudioButton.textContent = "Talking...";
}

let pttTailTimeout = null;

function pttUp() {
  if (!pttMode) return;
  startAudioButton.classList.remove("ptt-active");
  startAudioButton.textContent = "Hold to Talk";
  if (pttTailTimeout) clearTimeout(pttTailTimeout);
  pttTailTimeout = setTimeout(() => {
    is_audio = false;
    pttTailTimeout = null;
  }, 1500);
}

startAudioButton.addEventListener("mousedown", pttDown);
startAudioButton.addEventListener("mouseup", pttUp);
startAudioButton.addEventListener("mouseleave", pttUp);
startAudioButton.addEventListener("touchstart", pttDown);
startAudioButton.addEventListener("touchend", pttUp);
startAudioButton.addEventListener("touchcancel", pttUp);

// Spacebar shortcut for PTT
document.addEventListener("keydown", (e) => {
  if (!pttMode || e.repeat) return;
  if (e.code === "Space" && !e.target.matches("input, textarea, select, button:not(#startAudioButton)")) {
    e.preventDefault();
    pttDown(e);
  }
});
document.addEventListener("keyup", (e) => {
  if (!pttMode) return;
  if (e.code === "Space" && !e.target.matches("input, textarea, select")) {
    e.preventDefault();
    pttUp();
  }
});

function audioRecorderHandler(pcmData) {
  if (websocket && websocket.readyState === WebSocket.OPEN && is_audio) {
    websocket.send(pcmData);
  }
}

/**
 * Glossary (client-side, per browser)
 *
 * The glossary lives in this browser only — stored in localStorage and sent
 * to the server as the first WebSocket message of each session. The server
 * never persists it, so different browsers can run different glossaries
 * concurrently.
 */
const GLOSSARY_KEY = "live-translator.glossary.v2";
const MAX_GLOSSARY_BYTES = 256 * 1024;
const MAX_GLOSSARY_ENTRIES = 1000;

const glossaryOverlay = document.getElementById("glossaryOverlay");
const glossaryList = document.getElementById("glossaryList");
const glossaryCount = document.getElementById("glossaryCount");
const glossaryStatus = document.getElementById("glossaryStatus");
const glossaryFile = document.getElementById("glossaryFile");

let glossaryPairs = loadGlossaryFromStorage();
let glossaryDisplayMap = buildDisplayMap(glossaryPairs);

function normalizeEntry(p) {
  if (!p || typeof p.source !== "string" || typeof p.target !== "string") return null;
  const source = p.source;
  const target = p.target;
  const transcription = typeof p.transcription === "string" && p.transcription.length
    ? p.transcription
    : target;
  return { source, target, transcription };
}

function loadGlossaryFromStorage() {
  try {
    const raw = localStorage.getItem(GLOSSARY_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    return parsed.map(normalizeEntry).filter(Boolean);
  } catch {
    return null;
  }
}

function saveGlossaryToStorage(pairs) {
  try {
    localStorage.setItem(GLOSSARY_KEY, JSON.stringify(pairs));
  } catch (err) {
    console.warn("Failed to persist glossary to localStorage:", err);
  }
}

function getGlossary() {
  return glossaryPairs || [];
}

function buildDisplayMap(pairs) {
  const map = [];
  for (const p of pairs || []) {
    if (p.transcription && p.transcription !== p.target) {
      map.push([p.target, p.transcription]);
    }
  }
  // Apply longer targets first so a longer match wins over a shorter prefix.
  map.sort((a, b) => b[0].length - a[0].length);
  return map;
}

function applyDisplayMap(text) {
  if (!text || !glossaryDisplayMap.length) return text;
  let out = text.normalize('NFKC');
  for (const [from, to] of glossaryDisplayMap) {
    const nFrom = from.normalize('NFKC');
    if (out.includes(nFrom)) out = out.split(nFrom).join(to);
  }
  return out;
}

function setGlossary(pairs) {
  glossaryPairs = pairs.map(normalizeEntry).filter(Boolean);
  glossaryDisplayMap = buildDisplayMap(glossaryPairs);
  saveGlossaryToStorage(glossaryPairs);
}

function parseGlossaryCsv(text) {
  const pairs = [];
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim()) continue;
    // Split into at most 3 fields so that the 2nd and 3rd may themselves
    // contain commas? The spec is simple CSV; we don't support quoted commas.
    const parts = line.split(",");
    if (parts.length < 2) {
      throw new Error(`Line ${i + 1} must be 'source,target' (3rd column optional).`);
    }
    const source = parts[0].trim();
    const target = parts[1].trim();
    const transcription = (parts.length >= 3 ? parts.slice(2).join(",").trim() : "") || target;
    if (!source || !target) {
      throw new Error(`Line ${i + 1} is missing source or target.`);
    }
    pairs.push({ source, target, transcription });
    if (pairs.length > MAX_GLOSSARY_ENTRIES) {
      throw new Error(`Too many entries (max ${MAX_GLOSSARY_ENTRIES}).`);
    }
  }
  return pairs;
}

function renderGlossary(pairs) {
  glossaryCount.textContent = pairs.length;
  if (!pairs.length) {
    glossaryList.innerHTML = '<div class="glossary-empty">No glossary entries.</div>';
    return;
  }
  const table = document.createElement("table");
  table.className = "glossary-table";

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["Source", "Pronunciation", "Transcript"]) {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const { source, target, transcription } of pairs) {
    const tr = document.createElement("tr");
    for (const value of [source, target, transcription || target]) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);

  glossaryList.innerHTML = "";
  glossaryList.appendChild(table);
}

function setGlossaryStatus(text, kind) {
  glossaryStatus.textContent = text || "";
  glossaryStatus.className = "glossary-status" + (kind ? " " + kind : "");
}

async function fetchDefaultGlossary() {
  const resp = await fetch("/api/glossary/defaults");
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  const { pairs } = await resp.json();
  return pairs;
}

async function ensureGlossarySeeded() {
  if (glossaryPairs !== null) return;
  try {
    const defaults = await fetchDefaultGlossary();
    setGlossary(defaults);
  } catch (err) {
    console.warn("Failed to seed default glossary:", err);
    setGlossary([]);
  }
}

ensureGlossarySeeded();

document.getElementById("openGlossary").addEventListener("click", async () => {
  glossaryOverlay.classList.remove("hidden");
  setGlossaryStatus("");
  await ensureGlossarySeeded();
  renderGlossary(getGlossary());
});

document.getElementById("closeGlossary").addEventListener("click", () => {
  glossaryOverlay.classList.add("hidden");
});

glossaryOverlay.addEventListener("click", (e) => {
  if (e.target === glossaryOverlay) glossaryOverlay.classList.add("hidden");
});

document.getElementById("uploadGlossary").addEventListener("click", async () => {
  const file = glossaryFile.files[0];
  if (!file) {
    setGlossaryStatus("Pick a .csv file first.", "error");
    return;
  }
  if (!file.name.toLowerCase().endsWith(".csv")) {
    setGlossaryStatus("File must have a .csv extension.", "error");
    return;
  }
  if (file.size > MAX_GLOSSARY_BYTES) {
    setGlossaryStatus(`File exceeds ${MAX_GLOSSARY_BYTES} bytes.`, "error");
    return;
  }
  try {
    const text = await file.text();
    const pairs = parseGlossaryCsv(text);
    setGlossary(pairs);
    renderGlossary(pairs);
    setGlossaryStatus(
      `Replaced with ${pairs.length} entries. Applies on next session.`,
      "ok"
    );
    glossaryFile.value = "";
  } catch (err) {
    setGlossaryStatus("Load failed: " + err.message, "error");
  }
});

document.getElementById("resetGlossary").addEventListener("click", async () => {
  try {
    const defaults = await fetchDefaultGlossary();
    setGlossary(defaults);
    renderGlossary(defaults);
    setGlossaryStatus(
      `Reset to ${defaults.length} default entries. Applies on next session.`,
      "ok"
    );
    glossaryFile.value = "";
  } catch (err) {
    setGlossaryStatus("Reset failed: " + err.message, "error");
  }
});

/**
 * Audio device selection (per browser, stored in localStorage)
 */
const AUDIO_INPUT_KEY = "live-translator.audio.inputDeviceId";
const AUDIO_OUTPUT_KEY = "live-translator.audio.outputDeviceId";

function getSavedInputDevice() {
  return localStorage.getItem(AUDIO_INPUT_KEY) || "";
}
function setSavedInputDevice(id) {
  if (id) localStorage.setItem(AUDIO_INPUT_KEY, id);
  else localStorage.removeItem(AUDIO_INPUT_KEY);
}
function getSavedOutputDevice() {
  return localStorage.getItem(AUDIO_OUTPUT_KEY) || "";
}
function setSavedOutputDevice(id) {
  if (id) localStorage.setItem(AUDIO_OUTPUT_KEY, id);
  else localStorage.removeItem(AUDIO_OUTPUT_KEY);
}

const audioOverlay = document.getElementById("audioOverlay");
const audioInputSelect = document.getElementById("audioInputSelect");
const audioOutputSelect = document.getElementById("audioOutputSelect");
const audioHint = document.getElementById("audioHint");

async function populateAudioDevices() {
  let devices;
  try {
    devices = await navigator.mediaDevices.enumerateDevices();
    if (!devices.some(d => d.label)) {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach(t => t.stop());
      devices = await navigator.mediaDevices.enumerateDevices();
    }
  } catch {
    audioHint.textContent = "Could not enumerate audio devices.";
    return;
  }

  const inputs = devices.filter(d => d.kind === "audioinput");
  const outputs = devices.filter(d => d.kind === "audiooutput");
  const hasLabels = inputs.some(d => d.label);

  audioHint.textContent = hasLabels
    ? ""
    : "Grant microphone permission to see device names.";

  const savedInput = getSavedInputDevice();
  const savedOutput = getSavedOutputDevice();

  audioInputSelect.innerHTML = "";
  const defaultIn = document.createElement("option");
  defaultIn.value = "";
  defaultIn.textContent = "System Default";
  audioInputSelect.appendChild(defaultIn);
  for (const d of inputs) {
    const opt = document.createElement("option");
    opt.value = d.deviceId;
    opt.textContent = d.label || `Microphone (${d.deviceId.slice(0, 8)}...)`;
    if (d.deviceId === savedInput) opt.selected = true;
    audioInputSelect.appendChild(opt);
  }

  audioOutputSelect.innerHTML = "";
  const defaultOut = document.createElement("option");
  defaultOut.value = "";
  defaultOut.textContent = "System Default";
  audioOutputSelect.appendChild(defaultOut);
  for (const d of outputs) {
    const opt = document.createElement("option");
    opt.value = d.deviceId;
    opt.textContent = d.label || `Speaker (${d.deviceId.slice(0, 8)}...)`;
    if (d.deviceId === savedOutput) opt.selected = true;
    audioOutputSelect.appendChild(opt);
  }
}

audioInputSelect.addEventListener("change", () => {
  setSavedInputDevice(audioInputSelect.value);
});

audioOutputSelect.addEventListener("change", () => {
  setSavedOutputDevice(audioOutputSelect.value);
});

document.getElementById("applyAudio").addEventListener("click", async () => {
  if (audioRecorderContext) {
    if (micStream) micStream.getTracks().forEach(t => t.stop());
    await audioRecorderContext.close();
    const [node, ctx, stream] = await startAudioRecorderWorklet(audioRecorderHandler, getSavedInputDevice());
    audioRecorderNode = node;
    audioRecorderContext = ctx;
    micStream = stream;
  }
  if (audioPlayerContext) {
    await audioPlayerContext.close();
    const [node, ctx] = await startAudioPlayerWorklet(getSavedOutputDevice());
    audioPlayerNode = node;
    audioPlayerContext = ctx;
  }
  audioOverlay.classList.add("hidden");
});

document.getElementById("openAudio").addEventListener("click", async () => {
  audioOverlay.classList.remove("hidden");
  await populateAudioDevices();
});

document.getElementById("closeAudio").addEventListener("click", () => {
  audioOverlay.classList.add("hidden");
});

audioOverlay.addEventListener("click", (e) => {
  if (e.target === audioOverlay) audioOverlay.classList.add("hidden");
});
