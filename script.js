/* ═══════════════════════════════════════════════════
   VoiceScript — script.js
   Handles drag-and-drop, upload, API call, UI state
═══════════════════════════════════════════════════ */

"use strict";

// ── DOM References ──────────────────────────────────
const dropZone         = document.getElementById("dropZone");
const fileInput        = document.getElementById("fileInput");
const dropText         = document.getElementById("dropText");
const dropSub          = document.getElementById("dropSub");
const dropFileInfo     = document.getElementById("dropFileInfo");
const fileName         = document.getElementById("fileName");
const fileSize         = document.getElementById("fileSize");
const btnTranscribe    = document.getElementById("btnTranscribe");

const uploadSection    = document.getElementById("uploadSection");
const processingSection= document.getElementById("processingSection");
const processingFileName = document.getElementById("processingFileName");

const errorBanner      = document.getElementById("errorBanner");
const errorMsg         = document.getElementById("errorMsg");
const errorClose       = document.getElementById("errorClose");

const resultCard       = document.getElementById("resultCard");
const langBadge        = document.getElementById("langBadge");
const resultMeta       = document.getElementById("resultMeta");

const tabFull          = document.getElementById("tabFull");
const tabTimestamps    = document.getElementById("tabTimestamps");
const panelFull        = document.getElementById("panelFull");
const panelTimestamps  = document.getElementById("panelTimestamps");
const transcriptFull   = document.getElementById("transcriptFull");
const segmentsList     = document.getElementById("segmentsList");

const btnCopy          = document.getElementById("btnCopy");
const btnDownload      = document.getElementById("btnDownload");
const btnReset         = document.getElementById("btnReset");

// ── State ───────────────────────────────────────────
let selectedFile = null;
let lastTranscript = "";
let lastFilename   = "";

// ── Language Code → Full Name Map ───────────────────
const LANGUAGE_NAMES = {
  en: "English", zh: "Chinese", ja: "Japanese", ko: "Korean",
  fr: "French",  de: "German",  es: "Spanish",  it: "Italian",
  pt: "Portuguese", ru: "Russian", ar: "Arabic", hi: "Hindi",
  th: "Thai", vi: "Vietnamese", id: "Indonesian", ms: "Malay",
  nl: "Dutch", pl: "Polish", tr: "Turkish", sv: "Swedish",
  da: "Danish", fi: "Finnish", cs: "Czech", uk: "Ukrainian",
  he: "Hebrew", fa: "Persian", ro: "Romanian", hu: "Hungarian",
  el: "Greek",  bg: "Bulgarian", hr: "Croatian", sk: "Slovak",
  ca: "Catalan", lt: "Lithuanian", lv: "Latvian", et: "Estonian",
};

function getLanguageName(code) {
  if (!code) return "Unknown";
  const name = LANGUAGE_NAMES[code.toLowerCase()];
  return name ? `${name} (${code.toUpperCase()})` : code.toUpperCase();
}

// ── File Helpers ─────────────────────────────────────
function formatBytes(bytes) {
  if (bytes < 1024)       return bytes + " B";
  if (bytes < 1048576)    return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1048576).toFixed(1) + " MB";
}

const ALLOWED_TYPES = [
  "video/mp4", "video/x-matroska", "video/webm", "video/quicktime",
  "video/x-msvideo", "audio/mpeg", "audio/wav", "audio/ogg",
  "audio/flac", "audio/x-m4a", "audio/mp4", "audio/aac",
  "audio/x-ms-wma", "audio/webm",
];

const ALLOWED_EXTS = /\.(mp4|mp3|wav|mkv|webm|m4a|ogg|flac|aac|mov|avi|wma)$/i;

function isFileAllowed(file) {
  return ALLOWED_TYPES.includes(file.type) || ALLOWED_EXTS.test(file.name);
}

// ── Drop Zone: click ─────────────────────────────────
dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") fileInput.click();
});

fileInput.addEventListener("change", () => {
  if (fileInput.files.length > 0) handleFileSelect(fileInput.files[0]);
});

// ── Drop Zone: drag ──────────────────────────────────
dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("drag-over");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  const files = e.dataTransfer.files;
  if (files.length > 0) handleFileSelect(files[0]);
});

// ── Handle File Select ────────────────────────────────
function handleFileSelect(file) {
  if (!isFileAllowed(file)) {
    showError(`❌ Unsupported file: "${file.name}". Please use MP4, MP3, WAV, MKV, WebM, M4A, etc.`);
    return;
  }
  selectedFile = file;
  hideError();

  // Update drop zone UI
  dropZone.classList.add("file-ready");
  dropText.textContent = "File ready!";
  dropSub.textContent  = "Click to choose a different file";
  dropFileInfo.style.display = "flex";
  fileName.textContent = file.name;
  fileSize.textContent = formatBytes(file.size);

  btnTranscribe.disabled = false;
  btnTranscribe.focus();
}

// ── Transcribe Button ─────────────────────────────────
btnTranscribe.addEventListener("click", startTranscription);

async function startTranscription() {
  if (!selectedFile) return;

  // Show processing state
  processingSection.classList.remove("hidden");
  uploadSection.classList.add("hidden");
  hideError();
  processingFileName.textContent = selectedFile.name;

  const formData = new FormData();
  formData.append("file", selectedFile);

  try {
    const response = await fetch("/transcribe", {
      method: "POST",
      body: formData,
    });

    const data = await response.json();

    if (!response.ok || data.error) {
      throw new Error(data.error || `Server error ${response.status}`);
    }

    renderResult(data);

  } catch (err) {
    // Back to upload state on error
    processingSection.classList.add("hidden");
    uploadSection.classList.remove("hidden");
    showError(err.message || "Transcription failed. Is the Flask server running?");
  }
}

// ── Render Result ─────────────────────────────────────
function renderResult(data) {
  lastTranscript = data.transcript || "";
  lastFilename   = selectedFile ? selectedFile.name.replace(/\.[^.]+$/, "") : "transcript";

  // Language badge
  const langName = getLanguageName(data.language);
  langBadge.textContent = `🌍 ${langName}`;

  // Meta info
  const prob = data.language_probability
    ? ` · Confidence: ${(data.language_probability * 100).toFixed(0)}%`
    : "";
  resultMeta.innerHTML = `
    <span>📝 ${data.word_count.toLocaleString()} words</span>
    <span>🔤 ${data.char_count.toLocaleString()} characters</span>
    <span>🗣 ${langName}${prob}</span>
  `;

  // Full transcript
  transcriptFull.textContent = lastTranscript || "(No speech detected)";

  // Timestamps tab
  segmentsList.innerHTML = "";
  if (data.segments && data.segments.length > 0) {
    data.segments.forEach((seg) => {
      const div = document.createElement("div");
      div.className = "segment";
      div.innerHTML = `
        <span class="segment-time">[${seg.start} → ${seg.end}]</span>
        <span class="segment-text">${escapeHtml(seg.text)}</span>
      `;
      segmentsList.appendChild(div);
    });
    tabTimestamps.disabled = false;
  } else {
    tabTimestamps.disabled = true;
    segmentsList.innerHTML = `<p style="color:var(--text-muted);font-size:0.85rem;padding:1rem;">No segment data available.</p>`;
  }

  // Switch to Full Text tab
  switchTab("full");

  // Show result card, hide processing
  processingSection.classList.add("hidden");
  uploadSection.classList.remove("hidden");
  resultCard.classList.remove("hidden");
  resultCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ── Tab Switching ─────────────────────────────────────
tabFull.addEventListener("click", () => switchTab("full"));
tabTimestamps.addEventListener("click", () => switchTab("timestamps"));

function switchTab(which) {
  tabFull.classList.toggle("active", which === "full");
  tabTimestamps.classList.toggle("active", which === "timestamps");
  panelFull.classList.toggle("hidden", which !== "full");
  panelTimestamps.classList.toggle("hidden", which !== "timestamps");
  tabFull.setAttribute("aria-selected", which === "full");
  tabTimestamps.setAttribute("aria-selected", which === "timestamps");
}

// ── Copy to Clipboard ─────────────────────────────────
btnCopy.addEventListener("click", async () => {
  if (!lastTranscript) return;
  try {
    await navigator.clipboard.writeText(lastTranscript);
    btnCopy.classList.add("copied");
    btnCopy.querySelector("span").textContent = "✅";
    btnCopy.childNodes[1].textContent = " Copied!";
    setTimeout(() => {
      btnCopy.classList.remove("copied");
      btnCopy.querySelector("span").textContent = "📋";
      btnCopy.childNodes[1].textContent = " Copy Text";
    }, 2200);
  } catch {
    showError("Could not access clipboard. Please copy the text manually.");
  }
});

// ── Download as .txt ──────────────────────────────────
btnDownload.addEventListener("click", () => {
  if (!lastTranscript) return;
  const blob = new Blob([lastTranscript], { type: "text/plain;charset=utf-8" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `${lastFilename}_transcript.txt`;
  a.click();
  URL.revokeObjectURL(url);
});

// ── Reset / New File ──────────────────────────────────
btnReset.addEventListener("click", resetApp);

function resetApp() {
  selectedFile  = null;
  lastTranscript = "";
  lastFilename  = "";

  // Reset drop zone
  dropZone.classList.remove("file-ready", "drag-over");
  dropText.textContent = "Drag & drop your video or audio here";
  dropSub.textContent  = "or click to browse files";
  dropFileInfo.style.display = "none";
  fileName.textContent = "";
  fileSize.textContent = "";
  fileInput.value = "";

  btnTranscribe.disabled = true;

  // Hide result card
  resultCard.classList.add("hidden");
  hideError();

  // Scroll back up
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ── Error Helpers ─────────────────────────────────────
function showError(msg) {
  errorMsg.textContent = msg;
  errorBanner.classList.remove("hidden");
}

function hideError() {
  errorBanner.classList.add("hidden");
}

errorClose.addEventListener("click", hideError);

// ── Utility ───────────────────────────────────────────
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
