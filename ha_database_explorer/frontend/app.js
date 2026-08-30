// Resolve the API base from the current page path so the app works whether it is
// opened directly or behind a Home Assistant ingress prefix (e.g. /api/hassio_ingress/TOKEN/).
const BASE = location.pathname.replace(/\/[^/]*$/, "") + "/";

const $ = (sel) => document.querySelector(sel);
const view = $("#view");

async function api(path, opts) {
  const url = BASE + String(path).replace(/^\//, "");
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

function el(tag, attrs = {}, html = "") {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (html) n.innerHTML = html;
  return n;
}

async function guidanceBlock(engine) {
  const code = el("code", { class: "block" }, "loading…");
  try {
    const g = await api(`/api/tools/read-only-guidance?engine=${encodeURIComponent(engine)}`);
    code.textContent = g.sql + "\n\n" + g.note;
  } catch (e) {
    code.textContent = e.message;
  }
  return code;
}

function router() {
  const hash = (location.hash || "#dashboard").slice(1);
  document.querySelectorAll(".topbar nav a").forEach((a) =>
    a.classList.toggle("active", a.dataset.view === hash)
  );
  const map = {
    discovery: renderDiscovery,
    dashboard: renderDashboard,
    entities: renderEntities,
    overlap: renderOverlap,
  };
  (map[hash] || renderDashboard)();
}
window.addEventListener("hashchange", router);

async function renderDiscovery() {
  view.innerHTML = "";
  const card = el("div", { class: "card" }, "<h2>Discovery & Setup</h2>");
  const btn = el("button", { class: "action" }, "Discover databases");
  const list = el("div", {});
  card.append(btn, list);
  btn.onclick = async () => {
    btn.disabled = true;
    list.innerHTML = "<p class='muted'>Scanning…</p>";
    try {
      const found = await api("/api/databases/discover", { method: "POST" });
      list.innerHTML = "";
      if (!found.length) list.innerHTML = "<p class='muted'>No databases auto-detected. Add manually below.</p>";
      found.forEach((d) => list.append(discoveryRow(d)));
    } catch (e) {
      list.innerHTML = `<p class='muted'>${e.message}</p>`;
    } finally {
      btn.disabled = false;
    }
  };
  view.append(card, manualForm());
}

function discoveryRow(d) {
  const row = el("div", { class: "card" });
  row.append(el("p", {}, `<strong>${d.connection_name}</strong> <span class='muted'>(${d.engine})</span>`));
  const save = el("button", { class: "action" }, "Save");
  const test = el("button", { class: "action" }, "Test");
  const status = el("span", { class: "muted" });
  test.onclick = async () => {
    const r = await api("/api/databases/test-connection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    status.textContent = r.connected ? " connected ✓" : " failed ✗ " + (r.error || "");
  };
  save.onclick = async () => {
    await api("/api/databases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d),
    });
    status.textContent = " saved ✓";
  };
  const guide = el("button", { class: "action" }, "Read-only guide");
  guide.onclick = async () => {
    const blk = await guidanceBlock(d.engine);
    row.append(blk);
  };
  row.append(test, " ", save, " ", guide, " ", status);
  return row;
}

function manualForm() {
  const card = el("div", { class: "card" }, "<h3>Manual connection</h3>");
  const engine = el("input", { value: "mysql", placeholder: "engine (sqlite|mysql|influxdb)" });
  const name = el("input", { value: "", placeholder: "connection name" });
  const host = el("input", { value: "core-mariadb", placeholder: "host" });
  const port = el("input", { value: "3306", placeholder: "port" });
  const user = el("input", { value: "", placeholder: "user" });
  const pass = el("input", { type: "password", value: "", placeholder: "password" });
  const submit = el("button", { class: "action" }, "Add");
  submit.onclick = async () => {
    const cfg = {
      engine: engine.value,
      connection_name: name.value || `${host.value}`,
      host: host.value,
      port: parseInt(port.value) || undefined,
      user: user.value,
      password: pass.value,
    };
    await api("/api/databases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    });
    alert("Saved");
  };
  [engine, name, host, port, user, pass].forEach((i) => (i.style.margin = "4px", (i.style.display = "block")));
  const guide = el("button", { class: "action" }, "Read-only user guide");
  guide.onclick = async () => {
    const blk = await guidanceBlock(engine.value);
    card.append(blk);
  };
  card.append(engine, name, host, port, user, pass, submit, guide);
  return card;
}

async function renderDashboard() {
  view.innerHTML = "<div class='card'><p class='muted'>Loading…</p></div>";
  const [global, dbs] = await Promise.all([
    api("/api/metrics/global"),
    api("/api/databases"),
  ]);
  view.innerHTML = "";
  const grid = el("div", { class: "grid" });
  grid.append(
    metricCard("Total disk footprint", `${global.total_size_mb} MB`),
    metricCard("Total records", global.total_records.toLocaleString()),
    metricCard("Databases", global.database_count),
    metricCard("Overlapping entities", global.overlap_entity_count)
  );
  view.append(grid);

  const engineCard = el("div", { class: "card" }, "<h3>Storage by engine</h3>");
  for (const [eng, mb] of Object.entries(global.by_engine_mb || {})) {
    const pct = global.total_size_mb ? (mb / global.total_size_mb) * 100 : 0;
    engineCard.append(el("p", {}, `${eng}: ${mb} MB (${pct.toFixed(1)}%)`));
    const bar = el("div", { class: "bar" });
    bar.append(el("i", { style: `width:${pct}%` }));
    engineCard.append(bar);
  }
  view.append(engineCard);

  const scanCard = el("div", { class: "card" });
  const trigger = el("button", { class: "action" }, "Trigger Deep Scan");
  const prog = el("div", { class: "bar" });
  const fill = el("i", {});
  prog.append(fill);
  const status = el("p", { class: "muted" });
  trigger.onclick = async () => {
    const { job_id } = await api("/api/scan/trigger", { method: "POST" });
    poll(job_id, fill, status);
  };
  scanCard.append(el("p", {}, `Last scanned sources: ${dbs.length}`), trigger, prog, status);
  view.append(scanCard);
}

function metricCard(label, value) {
  const c = el("div", { class: "card" });
  c.append(el("div", { class: "metric-big" }, value), el("div", { class: "muted" }, label));
  return c;
}

async function poll(job_id, fill, status) {
  const wsProto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${wsProto}://${location.host}${BASE}api/scan/ws`);
  let last = 0;
  const tick = setInterval(async () => {
    try {
      const j = await api(`/api/scan/status/${job_id}`);
      fill.style.width = j.percent + "%";
      status.textContent = `${j.status} — ${j.message} (${j.percent}%)`;
      if (j.status === "complete" || j.status === "failed") {
        clearInterval(tick);
        ws.close();
        router();
      }
    } catch (e) {
      if (Date.now() - last > 60000) clearInterval(tick);
    }
  }, 2000);
}

async function renderEntities() {
  view.innerHTML = "<div class='card'><p class='muted'>Loading…</p></div>";
  const rows = await api("/api/metrics/entities?sort=record_count&order=desc");
  view.innerHTML = "";
  const card = el("div", { class: "card" });
  const search = el("input", { placeholder: "search entity_id…", style: "width:100%;padding:8px" });
  card.append(el("h3", {}, "Entity Bloat Explorer"), search);

  const advisor = el("div", { class: "card" }, "<h4>Purge / Retention Advisor</h4>");
  const days = el("input", { type: "number", value: "30", style: "width:80px;padding:6px" });
  const calc = el("button", { class: "action" }, "Calculate freed space");
  const result = el("p", { class: "muted" });
  calc.onclick = async () => {
    result.textContent = "computing…";
    try {
      const r = await api("/api/tools/retention-advice", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ retention_days: parseInt(days.value) || 30 }),
      });
      result.textContent = `Keeping ${r.retention_days} days would free ~${r.total_freed_mb} MB across ${r.entities.length} entities.`;
    } catch (e) {
      result.textContent = e.message;
    }
  };
  advisor.append(el("p", { class: "muted" }, "Estimate disk savings if entities were purged beyond a retention window."), days, " days ", calc, result);
  card.append(advisor);
  const table = el("table");
  table.innerHTML =
    "<thead><tr><th>entity_id</th><th>records</th><th>oldest</th><th>updates/hr</th></tr></thead>";
  const tbody = el("tbody");
  table.append(tbody);
  const draw = (q) => {
    tbody.innerHTML = "";
    rows
      .filter((r) => !q || r.entity_id.includes(q))
      .slice(0, 500)
      .forEach((r) =>
        tbody.append(
          el(
            "tr",
            {},
            `<td>${r.entity_id}</td><td>${r.record_count}</td><td>${r.start_date || "—"}</td><td>${r.updates_per_hour}</td>`
          )
        )
      );
  };
  search.oninput = () => draw(search.value);
  draw("");
  card.append(table);
  view.append(card);
}

async function renderOverlap() {
  view.innerHTML = "<div class='card'><p class='muted'>Loading…</p></div>";
  const rows = await api("/api/metrics/overlap");
  view.innerHTML = "";
  const card = el("div", { class: "card" });
  card.append(el("h3", {}, "Overlap Matrix & Exclusion Generator"));
  const split = el("div", { class: "split" });
  const left = el("div");
  const right = el("div");
  const selected = new Set();
  const code = el("code", { class: "block" }, "recorder:\n  exclude:\n    entities:");
  if (!rows.length) left.append(el("p", { class: "muted" }, "No overlapping entities detected."));
  rows.forEach((r) => {
    const label = el("label", { style: "display:block" });
    const cb = el("input", { type: "checkbox" });
    cb.onchange = () => {
      cb.checked ? selected.add(r.entity_id) : selected.delete(r.entity_id);
      updateYaml();
    };
    label.append(cb, ` ${r.entity_id} (redundant: ${r.total_redundant_records})`);
    left.append(label);
  });
  function updateYaml() {
    const ids = [...selected];
    code.textContent =
      "recorder:\n  exclude:\n    entities:" + (ids.length ? "\n" + ids.map((i) => `      - ${i}`).join("\n") : "");
  }
  const copy = el("button", { class: "action" }, "Copy YAML");
  copy.onclick = () => navigator.clipboard.writeText(code.textContent);
  right.append(code, copy);
  split.append(left, right);
  card.append(split);
  view.append(card);
}

router();
