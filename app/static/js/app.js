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
    addConsoleEntry('incoming', 'WebSocket Connected', { userId, sessionId, url: ws_url }, '🔌', 'system');
  };

  websocket.onmessage = function (event) {
    const adkEvent = JSON.parse(event.data);

    // Console logging
    let eventSummary = 'Event';
    let eventEmoji = '📨';
    const author = adkEvent.author || 'system';

    if (adkEvent.turnComplete) {
      eventSummary = 'Turn Complete';
      eventEmoji = '✅';
    } else if (adkEvent.interrupted) {
      eventSummary = 'Interrupted';
      eventEmoji = '⏸️';
    } else if (adkEvent.inputTranscription) {
      const t = adkEvent.inputTranscription.text || '';
      eventSummary = `Input: "${t.length > 60 ? t.substring(0, 60) + '...' : t}"`;
      eventEmoji = '📝';
    } else if (adkEvent.outputTranscription) {
      const t = adkEvent.outputTranscription.text || '';
      eventSummary = `Output: "${t.length > 60 ? t.substring(0, 60) + '...' : t}"`;
      eventEmoji = '📝';
    } else if (adkEvent.usageMetadata) {
      const u = adkEvent.usageMetadata;
      eventSummary = `Tokens: ${(u.totalTokenCount || 0).toLocaleString()} total`;
      eventEmoji = '📊';
    } else if (adkEvent.content && adkEvent.content.parts) {
      const hasText = adkEvent.content.parts.some(p => p.text);
      const hasAudio = adkEvent.content.parts.some(p => p.inlineData);

      if (hasText) {
        const textPart = adkEvent.content.parts.find(p => p.text);
        const t = textPart?.text || '';
        eventSummary = `Text: "${t.length > 80 ? t.substring(0, 80) + '...' : t}"`;
        eventEmoji = '💭';
      }

      if (hasAudio) {
        const audioPart = adkEvent.content.parts.find(p => p.inlineData);
        const byteSize = Math.floor((audioPart?.inlineData?.data?.length || 0) * 0.75);
        eventSummary = `Audio: ${byteSize.toLocaleString()} bytes`;
        eventEmoji = '🔊';
        addConsoleEntry('incoming', eventSummary, sanitizeEventForDisplay(adkEvent), eventEmoji, author, true);
      }
    }

    const isAudioOnlyEvent = adkEvent.content && adkEvent.content.parts &&
      adkEvent.content.parts.some(p => p.inlineData) &&
      !adkEvent.content.parts.some(p => p.text);
    if (!isAudioOnlyEvent) {
      addConsoleEntry('incoming', eventSummary, sanitizeEventForDisplay(adkEvent), eventEmoji, author);
    }

    // Handle turn complete
    if (adkEvent.turnComplete === true) {
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
      inputTranscriptionFinished = false;
      hasOutputTranscriptionInTurn = false;
      return;
    }

    // Handle interrupted
    if (adkEvent.interrupted === true) {
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
      inputTranscriptionFinished = false;
      hasOutputTranscriptionInTurn = false;
      return;
    }

    // Handle input transcription (user's spoken words)
    if (adkEvent.inputTranscription && adkEvent.inputTranscription.text) {
      const transcriptionText = adkEvent.inputTranscription.text;
      const isFinished = adkEvent.inputTranscription.finished;

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
    if (adkEvent.outputTranscription && adkEvent.outputTranscription.text) {
      const transcriptionText = adkEvent.outputTranscription.text;
      const isFinished = adkEvent.outputTranscription.finished;
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
          currentOutputTranscriptionElement = createMessageBubble(transcriptionText, false, !isFinished);
          currentOutputTranscriptionElement.id = currentOutputTranscriptionId;
          currentOutputTranscriptionElement.classList.add("transcription");
          messagesDiv.appendChild(currentOutputTranscriptionElement);
        } else {
          if (isFinished) {
            updateMessageBubble(currentOutputTranscriptionElement, transcriptionText, false);
          } else {
            const existingText = currentOutputTranscriptionElement.querySelector(".bubble-text").textContent;
            const cleanText = existingText.replace(/\.\.\.$/, '');
            updateMessageBubble(currentOutputTranscriptionElement, cleanText + transcriptionText, true);
          }
        }

        if (isFinished) {
          currentOutputTranscriptionId = null;
          currentOutputTranscriptionElement = null;
        }
        scrollToBottom();
      }
    }

    // Handle content events (text or audio)
    if (adkEvent.content && adkEvent.content.parts) {
      const parts = adkEvent.content.parts;

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
          if (!adkEvent.partial && hasOutputTranscriptionInTurn) continue;

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
  addSystemMessage(`Language changed: ${sourceLangSelect.options[sourceLangSelect.selectedIndex].text} → ${targetLangSelect.options[targetLangSelect.selectedIndex].text}`);
  connectWebsocket();
}

sourceLangSelect.addEventListener("change", reconnectWithNewLanguage);
targetLangSelect.addEventListener("change", reconnectWithNewLanguage);

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
