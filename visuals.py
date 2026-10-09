from __future__ import annotations

import hashlib
import re
import textwrap
from dataclasses import dataclass

import streamlit as st

MERMAID_MODULE_URL = "https://cdn.jsdelivr.net/npm/mermaid@11.12.0/dist/mermaid.esm.min.mjs"
DIAGRAM_MAX_HEIGHT = 560
VISUAL_MAX_HEIGHT = 640
MAX_BLOCK_CHARS = 60_000

FENCE_RE = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})[ \t]*(?P<info>.*)$")

@dataclass(frozen=True)
class Segment:
    kind: str
    content: str


def fenced(language: str, content: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", content)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{content}\n{fence}"


def split_segments(text: str, allow_visuals: bool = True) -> list[Segment]:
    special = {"mermaid", "visual"} if allow_visuals else {"mermaid"}
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    segments: list[Segment] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            joined = "\n".join(buffer)
            if joined.strip():
                segments.append(Segment("markdown", joined))
            buffer.clear()

    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        opening = FENCE_RE.match(line)
        if opening is None or (
            opening.group("fence")[0] == "`" and "`" in opening.group("info")
        ):
            buffer.append(line)
            index += 1
            continue

        fence = opening.group("fence")
        info_parts = opening.group("info").split()
        language = info_parts[0].lower() if info_parts else ""

        end = index + 1
        while end < total:
            closing = FENCE_RE.match(lines[end])
            if (
                closing is not None
                and closing.group("fence")[0] == fence[0]
                and len(closing.group("fence")) >= len(fence)
                and not closing.group("info").strip()
            ):
                break
            end += 1
        closed = end < total

        if language in special:
            body = textwrap.dedent("\n".join(lines[index + 1 : end])).strip("\n")
            if body.strip():
                flush()
                segments.append(Segment(language, body))
        else:
            buffer.extend(lines[index : (end + 1 if closed else total)])

        index = end + 1 if closed else total

    flush()
    return segments


def context_text(text: str) -> str:
    segments = split_segments(text, allow_visuals=True)
    if all(segment.kind == "markdown" for segment in segments):
        return text
    parts: list[str] = []
    for segment in segments:
        if segment.kind == "markdown":
            parts.append(segment.content)
        elif segment.kind == "mermaid":
            parts.append(fenced("mermaid", segment.content))
        else:
            parts.append("[Interactive visual omitted from history]")
    return "\n\n".join(parts)


COMPONENT_HTML = '<div class="ct-root"></div>'

COMPONENT_CSS = r"""
:host {
  display: block;
}

[hidden] {
  display: none !important;
}

.ct-block {
  margin: 0.25rem 0;
  overflow: hidden;
  border: 1px solid var(--line-2, #2a303c);
  border-radius: var(--r-lg, 12px);
  color: var(--ink, #e6e9ef);
  background: var(--surface, #10131a);
  font-family: var(--font, system-ui, "Segoe UI", sans-serif);
  font-size: 13px;
}

.ct-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.3rem 0.4rem 0.3rem 0.75rem;
  border-bottom: 1px solid var(--line, #1e232d);
}

.ct-label {
  color: var(--muted, #9099a8);
  font-size: 12px;
  font-weight: 600;
}

.ct-actions {
  display: flex;
  gap: 0.2rem;
}

.ct-actions button {
  padding: 0.22rem 0.6rem;
  border: 1px solid transparent;
  border-radius: 6px;
  color: var(--muted, #9099a8);
  background: transparent;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.2;
  cursor: pointer;
  transition: color 0.12s, background 0.12s;
}

.ct-actions button:hover {
  color: var(--ink, #e6e9ef);
  background: rgba(255, 255, 255, 0.06);
}

.ct-actions button:focus-visible {
  outline: 2px solid var(--accent, #8b7cf6);
  outline-offset: 1px;
}

.ct-note {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--bad-line, rgba(239, 123, 136, 0.28));
  color: var(--bad, #ef7b88);
  background: var(--bad-bg, rgba(239, 123, 136, 0.08));
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.ct-body {
  max-height: var(--ct-max, 560px);
  padding: 0.75rem;
  overflow: auto;
}

.ct-body svg {
  display: block;
  height: auto;
  margin: 0 auto;
}

.ct-loading {
  padding: 0.4rem 0;
  color: var(--faint, #667085);
  font-size: 12px;
}

.ct-block[data-kind="visual"] .ct-body {
  max-height: none;
  padding: 0;
  overflow: hidden;
}

.ct-frame {
  display: block;
  width: 100%;
  height: 160px;
  border: 0;
  color-scheme: dark;
  background: transparent;
}

.ct-source {
  max-height: var(--ct-max, 560px);
  margin: 0;
  padding: 0.7rem 0.85rem;
  overflow: auto;
  color: #dce2eb;
  background: #080a0f;
  font-family: var(--mono, ui-monospace, Consolas, monospace);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre;
}

.ct-source code {
  font-family: inherit;
}

.ct-block[data-view="source"] .ct-body {
  display: none;
}

.ct-block[data-view="render"] .ct-source {
  display: none;
}

.ct-block:fullscreen {
  display: flex;
  flex-direction: column;
  border: 0;
  border-radius: 0;
  background: var(--bg, #0b0d12);
}

.ct-block:fullscreen .ct-body,
.ct-block:fullscreen .ct-source {
  flex: 1 1 auto;
  min-height: 0;
  max-height: none;
}

.ct-block:fullscreen .ct-body svg {
  width: 100%;
  max-width: none !important;
}

.ct-block:fullscreen .ct-frame {
  height: 100% !important;
}
"""

COMPONENT_JS = r"""
const TEMPLATE = `
<div class="ct-block" data-view="render">
  <div class="ct-bar">
    <span class="ct-label"></span>
    <span class="ct-actions">
      <button type="button" class="ct-toggle">Source</button>
      <button type="button" class="ct-expand">Expand</button>
    </span>
  </div>
  <div class="ct-note" hidden></div>
  <div class="ct-body"><div class="ct-loading">Rendering…</div></div>
  <pre class="ct-source"><code></code></pre>
</div>`;

const FONT_STACK = 'Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

const MERMAID_THEME = {
  background: "#10131a",
  fontFamily: FONT_STACK,
  primaryColor: "#1b2030",
  primaryBorderColor: "#8b7cf6",
  primaryTextColor: "#e6e9ef",
  secondaryColor: "#151922",
  secondaryBorderColor: "#2a303c",
  secondaryTextColor: "#e6e9ef",
  tertiaryColor: "#0b0d12",
  tertiaryBorderColor: "#2a303c",
  tertiaryTextColor: "#e6e9ef",
  lineColor: "#9099a8",
  textColor: "#e6e9ef",
  mainBkg: "#1b2030",
  nodeBorder: "#8b7cf6",
  nodeTextColor: "#e6e9ef",
  clusterBkg: "#151922",
  clusterBorder: "#2a303c",
  titleColor: "#e6e9ef",
  edgeLabelBackground: "#10131a",
  noteBkgColor: "#2a2450",
  noteBorderColor: "#8b7cf6",
  noteTextColor: "#e6e9ef",
  actorBkg: "#1b2030",
  actorBorder: "#8b7cf6",
  actorTextColor: "#e6e9ef",
  actorLineColor: "#667085",
  signalColor: "#9099a8",
  signalTextColor: "#e6e9ef",
  labelBoxBkgColor: "#1b2030",
  labelBoxBorderColor: "#8b7cf6",
  labelTextColor: "#e6e9ef",
  loopTextColor: "#e6e9ef",
  activationBkgColor: "#2a2450",
  activationBorderColor: "#8b7cf6",
  sequenceNumberColor: "#0b0d12",
  classText: "#e6e9ef",
  attributeBackgroundColorOdd: "#151922",
  attributeBackgroundColorEven: "#1b2030"
};

const FRAME_CSP = [
  "default-src 'none'",
  "img-src data: blob:",
  "media-src data: blob:",
  "font-src data:",
  "style-src 'unsafe-inline'",
  "script-src 'unsafe-inline'"
].join("; ");

const FRAME_BASE_CSS = `
:root {
  color-scheme: dark;
  --bg: #0b0d12; --surface: #10131a; --raised: #151922; --raised-hover: #1b2030;
  --ink: #e6e9ef; --muted: #9099a8; --faint: #667085; --line: #1e232d; --line-2: #2a303c;
  --accent: #8b7cf6; --accent-hover: #a095ff; --accent-deep: #7565e6;
  --accent-tint: rgba(139, 124, 246, 0.1); --accent-line: rgba(139, 124, 246, 0.32);
  --good: #4cc9a4; --bad: #ef7b88;
  --font: ${FONT_STACK};
  --mono: ui-monospace, "SF Mono", "Cascadia Code", Consolas, "Liberation Mono", monospace;
}
*, *::before, *::after { box-sizing: border-box; }
html, body { margin: 0; background: transparent; color: var(--ink); }
body { padding: 12px; font: 14px/1.55 var(--font); overflow-x: hidden; }
button { font-family: inherit; }
`;

const FRAME_REPORTER = (token) => `
(function () {
  var token = ${JSON.stringify(token)};
  var last = 0;
  function send() {
    var height = Math.ceil(document.documentElement.scrollHeight);
    if (height !== last) {
      last = height;
      parent.postMessage({ ctToken: token, height: height }, "*");
    }
  }
  if (typeof ResizeObserver === "function") {
    var observer = new ResizeObserver(send);
    observer.observe(document.documentElement);
    observer.observe(document.body);
  }
  window.addEventListener("load", send);
  window.addEventListener("resize", send);
  window.addEventListener("message", function () { last = 0; send(); });
  send();
})();
`;

let mermaidPromise = null;
let renderCounter = 0;
const svgCache = new Map();

function hashText(text) {
  let hash = 5381;
  for (let i = 0; i < text.length; i += 1) {
    hash = ((hash * 33) ^ text.charCodeAt(i)) >>> 0;
  }
  return hash.toString(36);
}

function loadMermaid(moduleUrl) {
  if (!mermaidPromise) {
    mermaidPromise = import(moduleUrl)
      .then((module) => {
        const mermaid = module.default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          suppressErrorRendering: true,
          theme: "base",
          darkMode: true,
          fontFamily: FONT_STACK,
          themeVariables: MERMAID_THEME,
          flowchart: { curve: "basis", htmlLabels: true, useMaxWidth: true },
          sequence: { useMaxWidth: true },
          class: { useMaxWidth: true },
          state: { useMaxWidth: true },
          er: { useMaxWidth: true }
        });
        return mermaid;
      })
      .catch((error) => {
        mermaidPromise = null;
        throw error;
      });
  }
  return mermaidPromise;
}

function rememberSvg(source, svg) {
  if (svgCache.size >= 40) {
    svgCache.delete(svgCache.keys().next().value);
  }
  svgCache.set(source, svg);
}

function firstLine(error) {
  const message = String((error && error.message) || error || "").trim();
  return message.split("\n")[0].slice(0, 240);
}

function showFailure(block, message) {
  const note = block.querySelector(".ct-note");
  note.textContent = message;
  note.hidden = false;
  block.querySelector(".ct-body").textContent = "";
  block.dataset.view = "source";
  block.querySelector(".ct-toggle").hidden = true;
  block.querySelector(".ct-expand").hidden = true;
}

async function renderDiagram(root, block, data, key) {
  const body = block.querySelector(".ct-body");

  let mermaid;
  try {
    mermaid = await loadMermaid(data.moduleUrl);
  } catch (error) {
    if (root.dataset.key === key) {
      showFailure(
        block,
        "The diagram engine could not be loaded. Check your internet connection, or point MERMAID_MODULE_URL in visuals.py at a copy you host."
      );
    }
    return;
  }

  let svg = svgCache.get(data.source);
  if (!svg) {
    renderCounter += 1;
    const id = `ct-diagram-${renderCounter}`;
    try {
      const result = await mermaid.render(id, data.source);
      svg = result.svg;
    } catch (error) {
      if (root.dataset.key === key) {
        showFailure(
          block,
          `This diagram has a syntax error, so its source is shown instead. ${firstLine(error)}`
        );
      }
      return;
    } finally {
      const leftover = document.getElementById(`d${id}`);
      if (leftover) leftover.remove();
      const stray = document.getElementById(id);
      if (stray) stray.remove();
    }
    rememberSvg(data.source, svg);
  }

  if (root.dataset.key !== key) return;
  body.innerHTML = svg;
}

function buildDocument(source, token) {
  return (
    "<!doctype html><html><head><meta charset=\"utf-8\">" +
    "<meta name=\"color-scheme\" content=\"dark\">" +
    "<meta http-equiv=\"Content-Security-Policy\" content=\"" + FRAME_CSP + "\">" +
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">" +
    "<style>" + FRAME_BASE_CSS + "</style></head><body>" +
    source +
    "<script>" + FRAME_REPORTER(token) + "<\/script></body></html>"
  );
}

function mountVisual(block, data) {
  const body = block.querySelector(".ct-body");
  const frame = document.createElement("iframe");
  frame.className = "ct-frame";
  frame.title = "Interactive explanation";
  frame.setAttribute("sandbox", "allow-scripts");
  frame.setAttribute("referrerpolicy", "no-referrer");
  frame.dataset.token = "ct" + Math.random().toString(36).slice(2);
  frame.srcdoc = buildDocument(data.source, frame.dataset.token);
  body.textContent = "";
  body.appendChild(frame);
}

function listenForHeight(root, data) {
  const frame = root.querySelector(".ct-frame");
  if (!frame) return () => {};
  const limit = Number(data.maxHeight) || 640;

  const onMessage = (event) => {
    if (event.source !== frame.contentWindow) return;
    const message = event.data;
    if (!message || message.ctToken !== frame.dataset.token) return;
    const height = Number(message.height);
    if (!Number.isFinite(height) || height < 24) return;
    frame.style.height = `${Math.min(Math.ceil(height), limit)}px`;
  };

  window.addEventListener("message", onMessage);
  try {
    if (frame.contentWindow) frame.contentWindow.postMessage("ct-resize", "*");
  } catch (error) {}
  return () => window.removeEventListener("message", onMessage);
}

function mount(root, data, key) {
  const isVisual = data.kind === "visual";
  root.innerHTML = TEMPLATE;

  const block = root.querySelector(".ct-block");
  block.dataset.kind = isVisual ? "visual" : "diagram";
  block.dataset.view = "render";
  block.style.setProperty("--ct-max", `${Number(data.maxHeight) || 560}px`);
  root.querySelector(".ct-label").textContent = isVisual ? "Interactive view" : "Diagram";
  root.querySelector(".ct-source code").textContent = data.source;

  const toggle = root.querySelector(".ct-toggle");
  toggle.addEventListener("click", () => {
    const showingSource = block.dataset.view === "source";
    block.dataset.view = showingSource ? "render" : "source";
    toggle.textContent = showingSource ? "Source" : isVisual ? "Preview" : "Diagram";
  });

  const expand = root.querySelector(".ct-expand");
  if (typeof block.requestFullscreen !== "function") {
    expand.hidden = true;
  } else {
    expand.addEventListener("click", () => {
      try {
        if (block.matches(":fullscreen")) {
          document.exitFullscreen();
        } else {
          block.requestFullscreen().catch(() => {});
        }
      } catch (error) {}
    });
  }

  if (isVisual) {
    mountVisual(block, data);
  } else {
    renderDiagram(root, block, data, key);
  }
}

export default function (component) {
  const { data, parentElement } = component;
  if (!data || typeof data.source !== "string" || !parentElement) {
    return () => {};
  }

  let root = parentElement.querySelector(".ct-root");
  if (!root) {
    root = document.createElement("div");
    root.className = "ct-root";
    parentElement.appendChild(root);
  }

  const key = `${data.kind}:${data.source.length}:${hashText(data.source)}`;
  if (root.dataset.key !== key) {
    root.dataset.key = key;
    mount(root, data, key);
  }

  return data.kind === "visual" ? listenForHeight(root, data) : () => {};
}
"""

_COMPONENT_NAME = "codeterrain_visual_" + hashlib.sha1(
    (COMPONENT_HTML + COMPONENT_CSS + COMPONENT_JS).encode("utf-8")
).hexdigest()[:8]


def declare_component():
    try:
        from streamlit.components.v2 import component
    except Exception:
        return None
    try:
        return component(
            _COMPONENT_NAME,
            html=COMPONENT_HTML,
            css=COMPONENT_CSS,
            js=COMPONENT_JS,
        )
    except Exception:
        return None


def render_block(component, segment: Segment, key: str) -> None:
    is_visual = segment.kind == "visual"
    reason = ""

    if component is None:
        reason = (
            "Rendering needs a recent Streamlit with Custom Components v2. "
            "Run: pip install -U streamlit"
        )
    elif len(segment.content) > MAX_BLOCK_CHARS:
        reason = "This block is too large to render, so its source is shown."
    else:
        try:
            component(
                key=key,
                data={
                    "kind": segment.kind,
                    "source": segment.content,
                    "moduleUrl": MERMAID_MODULE_URL,
                    "maxHeight": VISUAL_MAX_HEIGHT if is_visual else DIAGRAM_MAX_HEIGHT,
                },
            )
            return
        except Exception as error:
            reason = f"Could not render this block ({type(error).__name__}), so its source is shown."

    st.code(segment.content, language="html" if is_visual else None)
    st.caption(reason)


def render_rich_markdown(
    text: str,
    key_prefix: str,
    allow_visuals: bool = True,
) -> None:
    segments = split_segments(text, allow_visuals)
    if all(segment.kind == "markdown" for segment in segments):
        st.markdown(text)
        return

    component = declare_component()
    prefix = re.sub(r"[^A-Za-z0-9_-]+", "-", key_prefix).strip("-") or "block"
    for index, segment in enumerate(segments):
        if segment.kind == "markdown":
            st.markdown(segment.content)
        else:
            render_block(component, segment, f"{prefix}-{index}")