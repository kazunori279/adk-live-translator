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

const sourceLangSelect = document.getElementById("sourceLang");
const targetLangSelect = document.getElementById("targetLang");

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
const consoleContent = document.getElementById("consoleContent");
const clearConsoleBtn = document.getElementById("clearConsole");
const showAudioEventsCheckbox = document.getElementById("showAudioEvents");
let currentMessageId = null;
let currentBubbleElement = null;
let currentInputTranscriptionId = null;
let currentInputTranscriptionElement = null;
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

// Console logging
function formatTimestamp() {
  const now = new Date();
  return now.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 });
}

function addConsoleEntry(type, content, data = null, emoji = null, author = null, isAudio = false) {
  if (isAudio && !showAudioEventsCheckbox.checked) return;

  const entry = document.createElement("div");
  entry.className = `console-entry ${type}`;

  const header = document.createElement("div");
  header.className = "console-entry-header";

  const leftSection = document.createElement("div");
  leftSection.className = "console-entry-left";

  if (emoji) {
    const emojiIcon = document.createElement("span");
    emojiIcon.className = "console-entry-emoji";
    emojiIcon.textContent = emoji;
    leftSection.appendChild(emojiIcon);
  }

  const expandIcon = document.createElement("span");
  expandIcon.className = "console-expand-icon";
  expandIcon.textContent = data ? "▶" : "";

  const typeLabel = document.createElement("span");
  typeLabel.className = "console-entry-type";
  typeLabel.textContent = type === 'outgoing' ? '↑ Upstream' : type === 'incoming' ? '↓ Downstream' : '⚠ Error';

  leftSection.appendChild(expandIcon);
  leftSection.appendChild(typeLabel);

  if (author) {
    const authorBadge = document.createElement("span");
    authorBadge.className = "console-entry-author";
    authorBadge.textContent = author;
    authorBadge.setAttribute('data-author', author);
    leftSection.appendChild(authorBadge);
  }

  const timestamp = document.createElement("span");
  timestamp.className = "console-entry-timestamp";
  timestamp.textContent = formatTimestamp();

  header.appendChild(leftSection);
  header.appendChild(timestamp);

  const contentDiv = document.createElement("div");
  contentDiv.className = "console-entry-content";
  contentDiv.textContent = content;

  entry.appendChild(header);
  entry.appendChild(contentDiv);

  if (data) {
    const jsonDiv = document.createElement("div");
    jsonDiv.className = "console-entry-json collapsed";
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(data, null, 2);
    jsonDiv.appendChild(pre);
    entry.appendChild(jsonDiv);

    entry.classList.add("expandable");
    entry.addEventListener("click", () => {
      const isExpanded = !jsonDiv.classList.contains("collapsed");
      if (isExpanded) {
        jsonDiv.classList.add("collapsed");
        expandIcon.textContent = "▶";
        entry.classList.remove("expanded");
      } else {
        jsonDiv.classList.remove("collapsed");
        expandIcon.textContent = "▼";
        entry.classList.add("expanded");
      }
    });
  }

  consoleContent.appendChild(entry);
  consoleContent.scrollTop = consoleContent.scrollHeight;
}

clearConsoleBtn.addEventListener('click', () => { consoleContent.innerHTML = ''; });

const toggleConsoleBtn = document.getElementById("toggleConsole");
const consolePanel = document.getElementById("consolePanel");
toggleConsoleBtn.addEventListener('click', () => {
  consolePanel.classList.toggle('hidden');
  toggleConsoleBtn.classList.toggle('active');
});

function updateConnectionStatus(connected) {
  if (connected) {
    statusIndicator.classList.remove("disconnected");
    statusText.textContent = "Connected";
  } else {
    statusIndicator.classList.add("disconnected");
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
}

function scrollToBottom() {
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function sanitizeEventForDisplay(event) {
  const sanitized = JSON.parse(JSON.stringify(event));
  if (sanitized.content && sanitized.content.parts) {
    sanitized.content.parts = sanitized.content.parts.map(part => {
      if (part.inlineData && part.inlineData.data) {
        const byteSize = Math.floor(part.inlineData.data.length * 0.75);
        return { ...part, inlineData: { ...part.inlineData, data: `(${byteSize.toLocaleString()} bytes)` } };
      }
      return part;
    });
  }
  return sanitized;
}

// WebSocket handlers
function connectWebsocket() {
  const ws_url = getWebSocketUrl();
  websocket = new WebSocket(ws_url);

  websocket.onopen = function () {
    updateConnectionStatus(true);
    addSystemMessage("Connected to translation server");
    // First message must be the setup payload (carries the per-browser glossary).
    websocket.send(JSON.stringify({ glossary: getGlossary() }));
    addConsoleEntry('outgoing', 'WebSocket Connected', { userId, sessionId, url: ws_url, glossaryEntries: getGlossary().length }, '🔌', 'system');
  };

  websocket.onmessage = function (event) {
    const serverMsg = JSON.parse(event.data);

    // Console logging
    let eventSummary = 'Event';
    let eventEmoji = '📨';
    const author = serverMsg.author || 'system';

    if (serverMsg.turnComplete) {
      eventSummary = 'Turn Complete';
      eventEmoji = '✅';
    } else if (serverMsg.interrupted) {
      eventSummary = 'Interrupted';
      eventEmoji = '⏸️';
    } else if (serverMsg.inputTranscription) {
      const t = serverMsg.inputTranscription.text || '';
      eventSummary = `Input: "${t.length > 60 ? t.substring(0, 60) + '...' : t}"`;
      eventEmoji = '📝';
    } else if (serverMsg.outputTranscription) {
      const t = serverMsg.outputTranscription.text || '';
      eventSummary = `Output: "${t.length > 60 ? t.substring(0, 60) + '...' : t}"`;
      eventEmoji = '📝';
    } else if (serverMsg.usageMetadata) {
      const u = serverMsg.usageMetadata;
      eventSummary = `Tokens: ${(u.totalTokenCount || 0).toLocaleString()} total`;
      eventEmoji = '📊';
    } else if (serverMsg.content && serverMsg.content.parts) {
      const hasText = serverMsg.content.parts.some(p => p.text);
      const hasAudio = serverMsg.content.parts.some(p => p.inlineData);

      if (hasText) {
        const textPart = serverMsg.content.parts.find(p => p.text);
        const t = textPart?.text || '';
        eventSummary = `Text: "${t.length > 80 ? t.substring(0, 80) + '...' : t}"`;
        eventEmoji = '💭';
      }

      if (hasAudio) {
        const audioPart = serverMsg.content.parts.find(p => p.inlineData);
        const byteSize = Math.floor((audioPart?.inlineData?.data?.length || 0) * 0.75);
        eventSummary = `Audio: ${byteSize.toLocaleString()} bytes`;
        eventEmoji = '🔊';
        addConsoleEntry('incoming', eventSummary, sanitizeEventForDisplay(serverMsg), eventEmoji, author, true);
      }
    }

    const isAudioOnlyEvent = serverMsg.content && serverMsg.content.parts &&
      serverMsg.content.parts.some(p => p.inlineData) &&
      !serverMsg.content.parts.some(p => p.text);
    if (!isAudioOnlyEvent) {
      addConsoleEntry('incoming', eventSummary, sanitizeEventForDisplay(serverMsg), eventEmoji, author);
    }

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
      currentOutputTranscriptionId = null;
      currentOutputTranscriptionElement = null;
      currentOutputRawText = "";
      inputTranscriptionFinished = false;
      hasOutputTranscriptionInTurn = false;
      return;
    }

    // Handle interrupted
    if (serverMsg.interrupted === true) {
      if (audioPlayerNode) {
        audioPlayerNode.port.postMessage({ command: "endOfAudio" });
      }
      if (currentBubbleElement) {
        const ti = currentBubbleElement.querySelector(".typing-indicator");
        if (ti) ti.remove();
        currentBubbleElement.classList.add("interrupted");
      }
      if (currentOutputTranscriptionElement) {
        const ti = currentOutputTranscriptionElement.querySelector(".typing-indicator");
        if (ti) ti.remove();
        currentOutputTranscriptionElement.classList.add("interrupted");
      }
      currentMessageId = null;
      currentBubbleElement = null;
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

      if (transcriptionText) {
        if (inputTranscriptionFinished) return;

        if (currentInputTranscriptionId == null) {
          currentInputTranscriptionId = Math.random().toString(36).substring(7);
          const cleanedText = cleanCJKSpaces(transcriptionText);
          currentInputTranscriptionElement = createMessageBubble(cleanedText, true, !isFinished);
          currentInputTranscriptionElement.id = currentInputTranscriptionId;
          currentInputTranscriptionElement.classList.add("transcription");
          messagesDiv.appendChild(currentInputTranscriptionElement);
        } else {
          if (currentOutputTranscriptionId == null && currentMessageId == null) {
            if (isFinished) {
              updateMessageBubble(currentInputTranscriptionElement, cleanCJKSpaces(transcriptionText), false);
            } else {
              const existingText = currentInputTranscriptionElement.querySelector(".bubble-text").textContent;
              const cleanText = existingText.replace(/\.\.\.$/, '');
              updateMessageBubble(currentInputTranscriptionElement, cleanCJKSpaces(cleanText + transcriptionText), true);
            }
          }
        }

        if (isFinished) {
          currentInputTranscriptionId = null;
          currentInputTranscriptionElement = null;
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
        if (currentInputTranscriptionId != null && currentOutputTranscriptionId == null) {
          const textElement = currentInputTranscriptionElement.querySelector(".bubble-text");
          const typingIndicator = textElement.querySelector(".typing-indicator");
          if (typingIndicator) typingIndicator.remove();
          currentInputTranscriptionId = null;
          currentInputTranscriptionElement = null;
          inputTranscriptionFinished = true;
        }

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

      if (currentInputTranscriptionId != null && currentMessageId == null && currentOutputTranscriptionId == null) {
        const textElement = currentInputTranscriptionElement.querySelector(".bubble-text");
        const typingIndicator = textElement.querySelector(".typing-indicator");
        if (typingIndicator) typingIndicator.remove();
        currentInputTranscriptionId = null;
        currentInputTranscriptionElement = null;
        inputTranscriptionFinished = true;
      }

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
    updateConnectionStatus(false);
    addSystemMessage("Connection closed. Reconnecting in 5 seconds...");
    addConsoleEntry('error', 'WebSocket Disconnected', { status: 'Connection closed', reconnecting: true }, '🔌', 'system');
    setTimeout(() => { connectWebsocket(); }, 5000);
  };

  websocket.onerror = function (e) {
    updateConnectionStatus(false);
    addConsoleEntry('error', 'WebSocket Error', { error: e.type }, '⚠️', 'system');
  };
}
connectWebsocket();

function reconnectWithNewLanguage() {
  sessionId = "demo-session-" + Math.random().toString(36).substring(7);
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
  startAudioPlayerWorklet().then(([node, ctx]) => {
    audioPlayerNode = node;
    audioPlayerContext = ctx;
  });
  startAudioRecorderWorklet(audioRecorderHandler).then(([node, ctx, stream]) => {
    audioRecorderNode = node;
    audioRecorderContext = ctx;
    micStream = stream;
  });
}

const startAudioButton = document.getElementById("startAudioButton");
startAudioButton.addEventListener("click", () => {
  startAudioButton.disabled = true;
  startAudio();
  is_audio = true;
  addSystemMessage("Audio mode enabled - speak to translate in real-time");
  addConsoleEntry('outgoing', 'Audio Mode Enabled', { status: 'Microphone active' }, '🎤', 'system');
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
  let out = text;
  for (const [from, to] of glossaryDisplayMap) {
    if (out.includes(from)) out = out.split(from).join(to);
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
