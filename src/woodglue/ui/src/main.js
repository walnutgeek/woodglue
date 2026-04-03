import { marked } from "marked";

const navTree = document.getElementById("nav-tree");
const docContent = document.getElementById("doc-content");

async function loadIndex() {
  const resp = await fetch("/docs/llms.txt");
  const text = await resp.text();
  return parseLlmsTxt(text);
}

function parseLlmsTxt(text) {
  const namespaces = {};
  for (const line of text.split("\n")) {
    const match = line.match(/^- (\S+?):\s*(.*)/);
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

  const resp = await fetch(`/docs/methods/${qualified}.md`);
  if (!resp.ok) {
    docContent.innerHTML = `<p>Failed to load documentation for ${qualified}.</p>`;
    return;
  }
  const md = await resp.text();
  docContent.innerHTML = marked.parse(md);
}

async function init() {
  try {
    const namespaces = await loadIndex();
    renderNav(namespaces);
  } catch (err) {
    navTree.innerHTML = `<p>Failed to load API index.</p>`;
    console.error(err);
  }
}

init();
