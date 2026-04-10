import { marked } from "marked";

const navTree = document.getElementById("nav-tree");
const docContent = document.getElementById("doc-content");
const appEl = document.getElementById("app");

function getToken() {
  const cookies = document.cookie.split(";").map((c) => c.trim());
  for (const c of cookies) {
    if (c.startsWith("wgl_token=")) return c.substring(10);
  }
  return null;
}

function setToken(token) {
  document.cookie = `wgl_token=${token};path=/;max-age=${365 * 86400};SameSite=Strict`;
}

function clearToken() {
  document.cookie = "wgl_token=;path=/;max-age=0";
}

function authHeaders() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function showTokenInput() {
  appEl.innerHTML = `
    <div class="token-prompt">
      <h2>Authentication Required</h2>
      <p>Enter your auth token (printed on server start):</p>
      <input type="text" id="token-input" placeholder="Paste token here" />
      <button id="token-submit">Connect</button>
    </div>`;
  document.getElementById("token-submit").addEventListener("click", () => {
    const val = document.getElementById("token-input").value.trim();
    if (val) {
      setToken(val);
      location.reload();
    }
  });
  document.getElementById("token-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") document.getElementById("token-submit").click();
  });
}

async function authFetch(url) {
  const resp = await fetch(url, { headers: authHeaders() });
  if (resp.status === 401) {
    clearToken();
    showTokenInput();
    return null;
  }
  return resp;
}

async function loadIndex() {
  const resp = await authFetch("/docs/llms.txt");
  if (!resp) return null;
  const text = await resp.text();
  return parseLlmsTxt(text);
}

function parseLlmsTxt(text) {
  const namespaces = {};
  for (const line of text.split("\n")) {
    const match = line.match(/^- \[(\S+?)]\([^)]+\):\s*(.*)/);
    if (!match) continue;
    const [, qualified, teaser] = match;
    const dot = qualified.indexOf(".");
    if (dot < 0) continue;
    const prefix = qualified.substring(0, dot);
    const method = qualified.substring(dot + 1);
    if (!namespaces[prefix]) namespaces[prefix] = [];
    namespaces[prefix].push({ method, teaser, qualified });
  }
  return namespaces;
}

function renderNav(namespaces) {
  navTree.innerHTML = "";
  for (const [prefix, methods] of Object.entries(namespaces).sort()) {
    const group = document.createElement("div");
    group.className = "ns-group";

    const title = document.createElement("div");
    title.className = "ns-name";
    title.textContent = prefix;
    group.appendChild(title);

    for (const { method, qualified } of methods) {
      const link = document.createElement("a");
      link.className = "method-link";
      link.textContent = method;
      link.dataset.qualified = qualified;
      link.addEventListener("click", () => loadMethod(qualified, link));
      group.appendChild(link);
    }

    navTree.appendChild(group);
  }
}

async function loadMethod(qualified, linkEl) {
  document.querySelectorAll(".method-link.active").forEach((el) => el.classList.remove("active"));
  if (linkEl) linkEl.classList.add("active");

  const resp = await authFetch(`/docs/methods/${qualified}.md`);
  if (!resp) return;
  const md = await resp.text();
  docContent.innerHTML = marked.parse(md);
}

async function init() {
  try {
    const namespaces = await loadIndex();
    if (!namespaces) return;
    renderNav(namespaces);
  } catch (err) {
    navTree.innerHTML = `<p>Failed to load API index.</p>`;
    console.error(err);
  }
}

init();
