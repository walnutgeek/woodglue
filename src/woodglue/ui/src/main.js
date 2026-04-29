import { marked } from "marked";

// ============================================================
// Auth
// ============================================================

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

// ============================================================
// API Client
// ============================================================

let _reqId = 0;

async function rpcCall(method, params = {}) {
  const resp = await fetch("/rpc", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ jsonrpc: "2.0", method, params, id: ++_reqId }),
  });
  const data = await resp.json();
  if (data.error) {
    if (data.error.code === -32000) {
      clearToken();
      showTokenPrompt();
      throw new Error("Unauthorized");
    }
    throw new Error(data.error.message);
  }
  return data.result;
}

async function authFetch(url) {
  const resp = await fetch(url, { headers: authHeaders() });
  if (resp.status === 401) {
    clearToken();
    showTokenPrompt();
    return null;
  }
  return resp;
}

// ============================================================
// State
// ============================================================

const state = {
  view: "home",       // home | namespace | run | method
  namespaces: {},     // { prefix: { expose_api, run_engine, method_count, has_cache, methods: [...] } }
  selectedNs: null,
  selectedRun: null,
  selectedNode: null,
  selectedMethod: null,
  runs: [],           // runs for selectedNs
  runFilter: "all",   // all | running | completed | failed
  expandedSubDags: new Set(), // node labels with expanded sub-DAGs
  triggers: [],       // triggers for selectedNs
};

// ============================================================
// Data Loading
// ============================================================

async function loadAllData() {
  const nsInfos = (await rpcCall("system.list_namespaces")) || [];

  const nsMap = {};
  for (const info of nsInfos) {
    nsMap[info.prefix] = {
      expose_api: info.expose_api,
      run_engine: info.run_engine,
      method_count: info.method_count,
      has_cache: info.has_cache,
      methods: [],
    };
  }

  state.namespaces = nsMap;
}

async function loadRuns(prefix, statusFilter = null) {
  try {
    if (statusFilter === "running") {
      return (await rpcCall("system.active_runs", { namespace: prefix })) || [];
    }
    const params = { namespace: prefix, limit: 30 };
    if (statusFilter && statusFilter !== "all") params.status = statusFilter;
    return (await rpcCall("system.recent_runs", params)) || [];
  } catch {
    return [];
  }
}

async function loadRunDetail(prefix, runId) {
  try {
    return await rpcCall("system.inspect_run", { namespace: prefix, run_id: runId });
  } catch {
    return null;
  }
}

async function loadTriggers(prefix) {
  try {
    return (await rpcCall("system.list_triggers", { namespace: prefix })) || [];
  } catch {
    return [];
  }
}

async function loadChildRuns(prefix, parentRunId) {
  try {
    return (await rpcCall("system.child_runs", { namespace: prefix, parent_run_id: parentRunId })) || [];
  } catch {
    return [];
  }
}

// ============================================================
// Navigation
// ============================================================

function navigate(view, params = {}) {
  state.view = view;
  Object.assign(state, params);
  render();
}

// ============================================================
// Rendering
// ============================================================

function render() {
  renderSidebar();
  renderBreadcrumb();
  renderView();
}

function renderSidebar() {
  const nav = document.getElementById("nav-tree");
  let html = '<div class="nav-section-label">Namespaces</div>';

  const sorted = Object.entries(state.namespaces).sort((a, b) => a[0].localeCompare(b[0]));
  for (const [prefix, info] of sorted) {
    const isActive = state.selectedNs === prefix && state.view !== "method";
    const dotClass = info.run_engine ? "engine" : info.expose_api ? "api" : "idle";

    html += `<div class="ns-item ${isActive ? "active" : ""}" data-ns="${prefix}">
      <span class="ns-dot ${dotClass}"></span>
      <span>${prefix}</span>
      <span class="ns-badges">
        ${info.expose_api ? '<span class="badge badge-api">API</span>' : ""}
        ${info.run_engine ? '<span class="badge badge-engine">ENG</span>' : ""}
      </span>
    </div>`;

    // Show methods under active namespace
    if (isActive && info.expose_api && info.methods && info.methods.length > 0) {
      html += '<div class="method-group">';
      for (const m of info.methods) {
        const qualified = `${prefix}.${m.nsref}`;
        html += `<div class="method-link" data-method="${qualified}">${m.nsref}</div>`;
      }
      html += "</div>";
    }
  }

  nav.innerHTML = html;

  // Bind namespace clicks
  nav.querySelectorAll(".ns-item").forEach((el) => {
    el.addEventListener("click", () => {
      const ns = el.dataset.ns;
      navigate("namespace", { selectedNs: ns, selectedRun: null, selectedNode: null, expandedSubDags: new Set() });
      loadNamespaceData(ns);
    });
  });

  // Bind method clicks
  nav.querySelectorAll(".method-link").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      navigate("method", { selectedMethod: el.dataset.method });
      loadMethodDoc(el.dataset.method);
    });
  });
}

function renderBreadcrumb() {
  const bc = document.getElementById("breadcrumb");
  const crumbs = [{ label: "woodglue", view: "home" }];

  if (state.view !== "home") {
    crumbs.push({ label: state.selectedNs || state.selectedMethod?.split(".")[0], view: "namespace" });
  }
  if (state.view === "run" && state.selectedRun) {
    crumbs.push({
      label: state.selectedRun.run_id?.substring(0, 12) + "...",
      view: "run",
      current: true,
    });
  }
  if (state.view === "method") {
    const parts = state.selectedMethod?.split(".") || [];
    crumbs.push({ label: parts.slice(1).join("."), view: "method", current: true });
  }

  const last = crumbs.length - 1;
  bc.innerHTML = crumbs
    .map(
      (c, i) =>
        `<span class="crumb ${i === last ? "current" : ""}" data-view="${c.view}">${c.label}</span>` +
        (i < last ? '<span class="crumb-sep">/</span>' : "")
    )
    .join("");

  bc.querySelectorAll(".crumb:not(.current)").forEach((el) => {
    el.addEventListener("click", () => {
      if (el.dataset.view === "home") navigate("home", { selectedNs: null, selectedRun: null });
      else if (el.dataset.view === "namespace") navigate("namespace", { selectedRun: null });
    });
  });
}

function renderView() {
  const view = document.getElementById("view");
  switch (state.view) {
    case "home":
      renderHome(view);
      break;
    case "namespace":
      renderNamespaceDetail(view);
      break;
    case "run":
      renderDagView(view);
      break;
    case "method":
      view.innerHTML = '<div class="doc-content" id="doc-content"><p>Loading...</p></div>';
      break;
    default:
      renderHome(view);
  }
}

// ============================================================
// Home View
// ============================================================

function renderHome(el) {
  const sorted = Object.entries(state.namespaces).sort((a, b) => a[0].localeCompare(b[0]));

  let cards = "";
  for (const [prefix, info] of sorted) {
    const features = [];
    if (info.expose_api) features.push('<span class="feature-pill api">API</span>');
    if (info.run_engine) features.push('<span class="feature-pill engine">Engine</span>');

    const dotClass = info.run_engine ? "engine" : info.expose_api ? "api" : "idle";
    cards += `
      <div class="ns-card" data-ns="${prefix}">
        <div class="ns-card-header">
          <span class="ns-dot ${dotClass}"></span>
          <span class="ns-card-name">${prefix}</span>
        </div>
        <div class="ns-card-features">${features.join("")}</div>
        <div class="ns-card-stats">
          ${info.expose_api ? `<span><span class="stat-value">${info.method_count}</span> methods</span>` : ""}
          ${info.run_engine ? '<span class="stat-value">engine active</span>' : ""}
        </div>
      </div>`;
  }

  if (sorted.length === 0) {
    el.innerHTML = `
      <div class="home-title">Woodglue</div>
      <div class="home-subtitle">No namespaces loaded yet.</div>`;
    return;
  }

  el.innerHTML = `
    <div class="home-title">Mounted Namespaces</div>
    <div class="home-subtitle">${sorted.length} namespace${sorted.length !== 1 ? "s" : ""} active</div>
    <div class="ns-cards">${cards}</div>`;

  el.querySelectorAll(".ns-card").forEach((card) => {
    card.addEventListener("click", () => {
      const ns = card.dataset.ns;
      navigate("namespace", { selectedNs: ns, selectedRun: null });
      loadNamespaceData(ns);
    });
  });
}

// ============================================================
// Namespace Detail View
// ============================================================

async function loadNamespaceData(prefix) {
  const info = state.namespaces[prefix];
  if (!info) return;

  // Load methods via system.list_methods if expose_api
  if (info.expose_api) {
    try {
      info.methods = (await rpcCall("system.list_methods", { namespace: prefix })) || [];
    } catch {
      info.methods = [];
    }
  }

  if (info.run_engine) {
    state.runs = await loadRuns(prefix);
    state.triggers = await loadTriggers(prefix);
  } else {
    state.runs = [];
    state.triggers = [];
  }
  render();
}

function renderNamespaceDetail(el) {
  const prefix = state.selectedNs;
  const info = state.namespaces[prefix];
  if (!info) {
    el.innerHTML = '<div class="empty-state">Namespace not found</div>';
    return;
  }

  let html = `
    <div class="ns-detail-header">
      <span class="ns-detail-name">${prefix}</span>
      <span class="ns-badges">
        ${info.expose_api ? '<span class="badge badge-api">API</span>' : ""}
        ${info.run_engine ? '<span class="badge badge-engine">Engine</span>' : ""}
      </span>
    </div>`;

  // Triggers
  if (state.triggers.length > 0) {
    html += '<div class="section-title">Triggers</div><div class="triggers-list">';
    for (const t of state.triggers) {
      html += `
        <div class="trigger-row">
          <span class="trigger-name">${t.name || t.trigger_name || "unnamed"}</span>
          <span class="trigger-schedule">${t.schedule || ""}</span>
          <span class="trigger-type">${t.type || "poll"}</span>
          <button class="trigger-fire-btn" data-trigger="${t.name || t.trigger_name}">fire</button>
        </div>`;
    }
    html += "</div>";
  }

  // DAG Runs
  if (info.run_engine) {
    const filtered = filterRuns(state.runs);
    html += `
      <div class="runs-header">
        <div class="section-title" style="margin:0;border:none;padding:0">DAG Runs</div>
        <div class="runs-filter">
          ${["all", "running", "completed", "failed"].map(
            (f) => `<button class="filter-btn ${state.runFilter === f ? "active" : ""}" data-filter="${f}">${f}</button>`
          ).join("")}
        </div>
      </div>`;

    if (filtered.length === 0) {
      html += '<div class="empty-state">No runs found</div>';
    } else {
      for (const run of filtered) {
        html += renderRunRow(run);
      }
    }
  }

  el.innerHTML = html;

  // Bind filter buttons
  el.querySelectorAll(".filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.runFilter = btn.dataset.filter;
      renderView();
    });
  });

  // Bind run rows
  el.querySelectorAll(".run-row").forEach((row) => {
    row.addEventListener("click", async () => {
      const runId = row.dataset.runId;
      const run = await loadRunDetail(prefix, runId);
      if (run) {
        navigate("run", { selectedRun: run, expandedSubDags: new Set() });
      }
    });
  });

  // Bind trigger fire buttons
  el.querySelectorAll(".trigger-fire-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const name = btn.dataset.trigger;
      try {
        await rpcCall("system.fire_trigger", { namespace: prefix, name });
        state.runs = await loadRuns(prefix);
        renderView();
      } catch (err) {
        console.error("Failed to fire trigger:", err);
      }
    });
  });
}

function filterRuns(runs) {
  if (state.runFilter === "all") return runs;
  return runs.filter((r) => r.status === state.runFilter);
}

function renderRunRow(run) {
  const elapsed = run.finished_at
    ? formatDuration(new Date(run.started_at), new Date(run.finished_at))
    : formatDuration(new Date(run.started_at), new Date());
  const timeStr = formatTime(run.started_at);

  return `
    <div class="run-row" data-run-id="${run.run_id}">
      <span class="run-status ${run.status}"></span>
      <span class="run-nsref">${run.dag_nsref || run.run_id}</span>
      <span class="run-id">${run.run_id.substring(0, 8)}</span>
      <span class="run-time">${timeStr}</span>
      <span class="run-duration">${elapsed}</span>
    </div>`;
}

// ============================================================
// DAG Graph View
// ============================================================

function renderDagView(el) {
  const run = state.selectedRun;
  if (!run) {
    el.innerHTML = '<div class="empty-state">No run selected</div>';
    return;
  }

  const elapsed = run.finished_at
    ? formatDuration(new Date(run.started_at), new Date(run.finished_at))
    : formatDuration(new Date(run.started_at), new Date());

  el.innerHTML = `
    <div class="dag-view">
      <div class="dag-header">
        <span class="status-badge ${run.status}"><span class="run-status ${run.status}" style="width:8px;height:8px"></span> ${run.status}</span>
        <div class="dag-run-info">
          <span>${run.dag_nsref || ""}</span>
          <span>ID: ${run.run_id.substring(0, 12)}</span>
          <span>${formatTime(run.started_at)}</span>
          <span>${elapsed}</span>
        </div>
      </div>
      <div class="dag-legend">
        <span class="legend-item"><span class="legend-dot" style="background:var(--success)"></span> completed</span>
        <span class="legend-item"><span class="legend-dot" style="background:var(--warning)"></span> running</span>
        <span class="legend-item"><span class="legend-dot" style="background:var(--error)"></span> failed</span>
        <span class="legend-item"><span class="legend-dot" style="background:var(--text-tertiary)"></span> pending</span>
        <span class="legend-item"><span class="legend-dot" style="background:var(--skipped)"></span> skipped</span>
        <span class="legend-item"><span class="legend-dot" style="background:var(--source)"></span> source</span>
        <span class="legend-item"><span class="legend-dot" style="background:var(--sink)"></span> sink</span>
      </div>
      <div class="dag-graph-container" id="dag-graph"></div>
    </div>`;

  renderDagGraph(run);
}

// ============================================================
// DAG Graph Layout & SVG Rendering
// ============================================================

const NODE_W = 170;
const NODE_H = 52;
const LAYER_GAP = 140;
const NODE_GAP = 24;
const PAD = 40;

function renderDagGraph(run) {
  const container = document.getElementById("dag-graph");
  if (!container || !run.nodes) return;

  const nodes = run.nodes;
  const layout = layoutDag(nodes);

  const svgW = (layout.maxLayer + 1) * LAYER_GAP + NODE_W + PAD * 2;
  const svgH = layout.maxRow * (NODE_H + NODE_GAP) + NODE_H + PAD * 2;

  let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${svgW}" height="${svgH}" viewBox="0 0 ${svgW} ${svgH}">`;

  // Draw edges first (behind nodes)
  svg += '<g class="edges">';
  for (const [label, nodeInfo] of Object.entries(nodes)) {
    if (!nodeInfo.edges) continue;
    const fromPos = layout.positions[label];
    if (!fromPos) continue;
    for (const edge of nodeInfo.edges) {
      const toPos = layout.positions[edge.downstream_label];
      if (!toPos) continue;
      const edgeClass = nodeInfo.status === "completed" ? "done" : nodeInfo.status === "running" ? "active" : "";
      svg += drawEdge(fromPos, toPos, edgeClass);
    }
  }
  svg += "</g>";

  // Draw sub-DAG containers for expanded nodes
  svg += '<g class="subdags">';
  for (const label of state.expandedSubDags) {
    const nodeInfo = nodes[label];
    if (!nodeInfo?.sub_dags) continue;
    for (const [subKey, subRun] of Object.entries(nodeInfo.sub_dags)) {
      const subLayout = layoutDag(subRun.nodes);
      const pos = layout.positions[label];
      if (!pos) continue;

      const subOffsetX = pos.x + NODE_W + 20;
      const subOffsetY = pos.y - 10;
      const subW = (subLayout.maxLayer + 1) * LAYER_GAP + NODE_W + 30;
      const subH = subLayout.maxRow * (NODE_H + NODE_GAP) + NODE_H + 30;

      svg += `<g class="subdag-container" transform="translate(${subOffsetX},${subOffsetY})">`;
      svg += `<rect class="subdag-border" x="-10" y="-20" width="${subW}" height="${subH + 10}"/>`;
      svg += `<text class="subdag-label" x="0" y="-6">${subKey}</text>`;

      // Sub-DAG edges
      for (const [sl, sn] of Object.entries(subRun.nodes)) {
        if (!sn.edges) continue;
        const sp = subLayout.positions[sl];
        if (!sp) continue;
        for (const se of sn.edges) {
          const tp = subLayout.positions[se.downstream_label];
          if (!tp) continue;
          const ec = sn.status === "completed" ? "done" : sn.status === "running" ? "active" : "";
          svg += drawEdge(sp, tp, ec);
        }
      }

      // Sub-DAG nodes
      for (const [sl, sn] of Object.entries(subRun.nodes)) {
        const sp = subLayout.positions[sl];
        if (!sp) continue;
        svg += drawNode(sl, sn, sp, false);
      }

      // Collapse button
      svg += `<g class="collapse-btn" data-collapse="${label}" transform="translate(${subW - 60},-18)">
        <rect width="50" height="16" rx="3"/>
        <text x="25" y="11" text-anchor="middle">collapse</text>
      </g>`;

      svg += "</g>";
    }
  }
  svg += "</g>";

  // Draw nodes
  svg += '<g class="nodes">';
  for (const [label, nodeInfo] of Object.entries(nodes)) {
    const pos = layout.positions[label];
    if (!pos) continue;
    const hasSubDags = nodeInfo.sub_dags && Object.keys(nodeInfo.sub_dags).length > 0;
    const isExpanded = state.expandedSubDags.has(label);
    svg += drawNode(label, nodeInfo, pos, hasSubDags && !isExpanded);
  }
  svg += "</g>";

  svg += "</svg>";
  container.innerHTML = svg;

  // Bind node clicks
  container.querySelectorAll(".graph-node").forEach((nodeEl) => {
    nodeEl.addEventListener("click", () => {
      const label = nodeEl.dataset.label;
      const nodeInfo = nodes[label];
      if (!nodeInfo) return;

      // If has sub-DAGs and not expanded, expand
      const hasSubDags = nodeInfo.sub_dags && Object.keys(nodeInfo.sub_dags).length > 0;
      if (hasSubDags && !state.expandedSubDags.has(label)) {
        state.expandedSubDags.add(label);
        renderDagGraph(run);
        return;
      }

      // Show node detail
      showNodeDetail(label, nodeInfo);
    });
  });

  // Bind collapse buttons
  container.querySelectorAll(".collapse-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const label = btn.dataset.collapse;
      state.expandedSubDags.delete(label);
      renderDagGraph(run);
    });
  });
}

function layoutDag(nodes) {
  if (!nodes) return { positions: {}, maxLayer: 0, maxRow: 0 };

  // Build adjacency and find in-degrees
  const incoming = {};
  const labels = Object.keys(nodes);
  for (const l of labels) incoming[l] = new Set();

  for (const [label, info] of Object.entries(nodes)) {
    if (!info.edges) continue;
    for (const e of info.edges) {
      if (incoming[e.downstream_label]) {
        incoming[e.downstream_label].add(label);
      }
    }
  }

  // Assign layers via BFS from sources
  const layers = {};
  const queue = [];
  for (const l of labels) {
    if (incoming[l].size === 0) {
      layers[l] = 0;
      queue.push(l);
    }
  }

  // If no sources found (cycle or flat), assign all to layer 0
  if (queue.length === 0) {
    for (const l of labels) {
      layers[l] = 0;
      queue.push(l);
    }
  }

  let qi = 0;
  while (qi < queue.length) {
    const label = queue[qi++];
    const info = nodes[label];
    if (!info?.edges) continue;
    for (const e of info.edges) {
      const newLayer = layers[label] + 1;
      if (layers[e.downstream_label] === undefined || layers[e.downstream_label] < newLayer) {
        layers[e.downstream_label] = newLayer;
        queue.push(e.downstream_label);
      }
    }
  }

  // Assign any remaining nodes
  for (const l of labels) {
    if (layers[l] === undefined) layers[l] = 0;
  }

  // Group by layer, sort within layer alphabetically
  const layerGroups = {};
  let maxLayer = 0;
  for (const [l, layer] of Object.entries(layers)) {
    if (!layerGroups[layer]) layerGroups[layer] = [];
    layerGroups[layer].push(l);
    if (layer > maxLayer) maxLayer = layer;
  }

  for (const group of Object.values(layerGroups)) {
    group.sort();
  }

  // Compute positions
  const positions = {};
  let maxRow = 0;
  for (let layer = 0; layer <= maxLayer; layer++) {
    const group = layerGroups[layer] || [];
    for (let i = 0; i < group.length; i++) {
      positions[group[i]] = {
        x: PAD + layer * LAYER_GAP,
        y: PAD + i * (NODE_H + NODE_GAP),
      };
      if (i > maxRow) maxRow = i;
    }
  }

  return { positions, maxLayer, maxRow };
}

function drawNode(label, info, pos, expandable) {
  const statusColor = getStatusColor(info);
  const roleColor = info.is_source ? "var(--source)" : info.is_sink ? "var(--sink)" : null;
  const borderColor = roleColor || statusColor;

  const shortLabel = label.length > 20 ? label.substring(0, 18) + ".." : label;

  return `
    <g class="graph-node ${expandable ? "expandable" : ""}" data-label="${label}" transform="translate(${pos.x},${pos.y})">
      <rect width="${NODE_W}" height="${NODE_H}" rx="6" fill="var(--bg-elevated)" stroke="${borderColor}" stroke-width="2"/>
      ${info.status === "running" ? `<rect width="${NODE_W}" height="${NODE_H}" rx="6" fill="none" stroke="${statusColor}" stroke-width="2" opacity="0.4"><animate attributeName="opacity" values="0.4;0.1;0.4" dur="2s" repeatCount="indefinite"/></rect>` : ""}
      <text x="12" y="22" font-size="12">${shortLabel}</text>
      <text class="node-status-text" x="12" y="38">${info.status}${expandable ? "  [+]" : ""}</text>
      ${info.is_source ? `<circle cx="${NODE_W - 14}" cy="14" r="4" fill="var(--source)" opacity="0.6"/>` : ""}
      ${info.is_sink ? `<circle cx="${NODE_W - 14}" cy="14" r="4" fill="var(--sink)" opacity="0.6"/>` : ""}
    </g>`;
}

function drawEdge(from, to, className) {
  const x1 = from.x + NODE_W;
  const y1 = from.y + NODE_H / 2;
  const x2 = to.x;
  const y2 = to.y + NODE_H / 2;
  const cx = (x1 + x2) / 2;

  return `<path class="graph-edge ${className}" d="M${x1},${y1} C${cx},${y1} ${cx},${y2} ${x2},${y2}"/>
    <polygon class="graph-edge-arrow ${className}" points="${x2 - 6},${y2 - 4} ${x2},${y2} ${x2 - 6},${y2 + 4}"/>`;
}

function getStatusColor(info) {
  switch (info.status) {
    case "completed": return "var(--success)";
    case "running": return "var(--warning)";
    case "failed": return "var(--error)";
    case "skipped": return "var(--skipped)";
    case "paused": return "var(--source)";
    default: return "var(--text-tertiary)";
  }
}

// ============================================================
// Node Detail Panel
// ============================================================

function showNodeDetail(label, nodeInfo) {
  const panel = document.getElementById("detail-panel");
  const title = document.getElementById("detail-title");
  const body = document.getElementById("detail-body");

  title.textContent = label;
  panel.classList.remove("hidden");

  let html = `
    <div class="detail-section">
      <div class="detail-label">Status</div>
      <span class="status-badge ${nodeInfo.status}">${nodeInfo.status}</span>
    </div>`;

  if (nodeInfo.is_source) html += '<div class="detail-section"><div class="detail-label">Role</div><div class="detail-value">Source node</div></div>';
  if (nodeInfo.is_sink) html += '<div class="detail-section"><div class="detail-label">Role</div><div class="detail-value">Sink node</div></div>';

  if (nodeInfo.started_at) {
    html += `<div class="detail-section"><div class="detail-label">Started</div><div class="detail-value">${formatTimeFull(nodeInfo.started_at)}</div></div>`;
  }

  if (nodeInfo.finished_at) {
    html += `<div class="detail-section"><div class="detail-label">Finished</div><div class="detail-value">${formatTimeFull(nodeInfo.finished_at)}</div></div>`;
  }

  if (nodeInfo.error) {
    html += `<div class="detail-section"><div class="detail-label">Error</div><div class="detail-json" style="color:var(--error)">${escapeHtml(nodeInfo.error)}</div></div>`;
  }

  if (nodeInfo.edges?.length > 0) {
    html += `<div class="detail-section"><div class="detail-label">Downstream</div>`;
    for (const e of nodeInfo.edges) {
      html += `<div class="detail-value" style="margin-bottom:4px">${e.downstream_label}</div>`;
    }
    html += "</div>";
  }

  if (nodeInfo.io) {
    if (nodeInfo.io.input_json) {
      html += `<div class="detail-section"><div class="detail-label">Input</div><div class="detail-json">${formatJson(nodeInfo.io.input_json)}</div></div>`;
    }
    if (nodeInfo.io.output_json) {
      html += `<div class="detail-section"><div class="detail-label">Output</div><div class="detail-json">${formatJson(nodeInfo.io.output_json)}</div></div>`;
    }
  } else {
    html += `<div class="detail-section"><button class="io-load-btn" data-node-label="${label}">Load I/O</button></div>`;
  }

  if (nodeInfo.sub_dags && Object.keys(nodeInfo.sub_dags).length > 0) {
    html += `<div class="detail-section"><div class="detail-label">Sub-DAGs</div>`;
    for (const [key, subRun] of Object.entries(nodeInfo.sub_dags)) {
      const nodeCount = subRun.nodes ? Object.keys(subRun.nodes).length : 0;
      html += `<div class="detail-value" style="margin-bottom:4px">${key} (${nodeCount} nodes, ${subRun.status})</div>`;
    }
    html += "</div>";
  }

  body.innerHTML = html;

  // Bind Load I/O button
  const ioBtn = body.querySelector(".io-load-btn");
  if (ioBtn) {
    ioBtn.addEventListener("click", async () => {
      const run = state.selectedRun;
      const prefix = state.selectedNs;
      if (!run || !prefix) return;
      ioBtn.disabled = true;
      ioBtn.textContent = "Loading...";
      try {
        const result = await rpcCall("system.load_io", {
          namespace: prefix,
          run_id: run.run_id,
          node_labels: [label],
        });
        if (result?.nodes?.[label]?.io) {
          nodeInfo.io = result.nodes[label].io;
          showNodeDetail(label, nodeInfo);
        } else {
          ioBtn.textContent = "No I/O recorded";
        }
      } catch {
        ioBtn.textContent = "Failed to load";
      }
    });
  }
}

function closeDetailPanel() {
  document.getElementById("detail-panel").classList.add("hidden");
}

// ============================================================
// Method Docs View
// ============================================================

async function loadMethodDoc(qualified) {
  const docPath = qualified.replaceAll(":", "/");
  const resp = await authFetch(`/docs/methods/${docPath}.md`);
  if (!resp) return;
  const md = await resp.text();
  const docEl = document.getElementById("doc-content");
  if (docEl) docEl.innerHTML = marked.parse(md);
}

// ============================================================
// Token Prompt
// ============================================================

function showTokenPrompt() {
  const view = document.getElementById("view");
  view.innerHTML = `
    <div class="token-prompt">
      <h2>Authentication Required</h2>
      <p>Enter your auth token (printed on server start):</p>
      <input type="text" id="token-input" placeholder="Paste token here" />
      <button id="token-submit">Connect</button>
    </div>`;
  document.getElementById("token-submit").addEventListener("click", () => {
    const val = document.getElementById("token-input").value.trim();
    if (val) { setToken(val); location.reload(); }
  });
  document.getElementById("token-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") document.getElementById("token-submit").click();
  });
}

// ============================================================
// Utilities
// ============================================================

function formatTime(isoStr) {
  if (!isoStr) return "";
  const d = new Date(isoStr);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatTimeFull(isoStr) {
  if (!isoStr) return "";
  return new Date(isoStr).toLocaleString();
}

function formatDuration(start, end) {
  const ms = end - start;
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return `${m}m ${rs}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function formatJson(str) {
  try {
    return escapeHtml(JSON.stringify(JSON.parse(str), null, 2));
  } catch {
    return escapeHtml(str);
  }
}

function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ============================================================
// Init
// ============================================================

document.getElementById("detail-close").addEventListener("click", closeDetailPanel);

async function init() {
  try {
    await loadAllData();
    render();
  } catch (err) {
    if (err.message === "Unauthorized") return; // token prompt already shown
    console.error("Init failed:", err);
    document.getElementById("view").innerHTML = '<div class="empty-state">Failed to connect to server.</div>';
  }
}

init();
