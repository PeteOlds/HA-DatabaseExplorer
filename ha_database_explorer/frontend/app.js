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
  const configured = await api("/api/databases").catch(() => []);
  if (configured.length) {
    const cfgCard = el("div", { class: "card" }, "<h3>Configured databases</h3>");
    const cfgTable = el("table");
    cfgTable.innerHTML =
      "<thead><tr><th>Database</th><th>Engine</th><th>Host</th><th>Status</th><th>Last scanned</th><th>Duration</th><th></th></tr></thead>";
    const cfgBody = el("tbody");
    configured.forEach((d) => {
      const scanned = d.last_scanned ? new Date(d.last_scanned).toLocaleString() : "never";
      const duration = d.scan_duration_s != null ? `${d.scan_duration_s}s` : "—";
      const hostLabel = d.host ? `${d.host}${d.port ? ":" + d.port : ""}` : d.path || "—";
      const tr = el(
        "tr",
        {},
        `<td>${d.connection_name}</td><td>${d.engine}</td><td>${hostLabel}</td>` +
          `<td class="status ${d.status || "unknown"}">${d.status || "—"}</td>` +
          `<td>${scanned}</td><td>${duration}</td>`
      );
      const actions = el("td");
      const edit = el("button", { class: "action" }, "Edit");
      edit.onclick = () => editConnection(d);
      const del = el("button", { class: "action" }, "Remove");
      del.onclick = async () => {
        if (!confirm(`Remove ${d.connection_name}?`)) return;
        await api(`/api/databases/${encodeURIComponent(d.connection_name)}`, { method: "DELETE" });
        router();
      };
      actions.append(edit, " ", del);
      tr.append(actions);
      cfgBody.append(tr);
    });
    cfgTable.append(cfgBody);
    cfgCard.append(cfgTable);
    view.append(cfgCard);
  }
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
  const manualCard = manualForm();
  manualCard.id = "manual-card";
  view.append(card, manualCard);
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

function manualForm(existing) {
  const isEdit = !!existing;
  const card = el("div", { class: "card" }, `<h3>${isEdit ? "Edit connection" : "Manual connection"}</h3>`);
  const engine = el("input", { value: existing?.engine || "mysql", placeholder: "engine (sqlite|mysql|influxdb)" });
  const name = el("input", { value: existing?.connection_name || "", placeholder: "connection name" });
  if (isEdit) {
    name.disabled = true;
    name.title = "Connection name cannot be changed — remove and re-add to rename";
    engine.disabled = true;
    engine.title = "Engine cannot be changed — remove and re-add to change engine";
  }
  const host = el("input", { value: existing?.host || "core-mariadb", placeholder: "host" });
  const port = el("input", { value: existing?.port || "3306", placeholder: "port" });
  const user = el("input", { value: existing?.user || "", placeholder: "user" });
  const pass = el("input", { type: "password", value: "", placeholder: "password (blank = keep existing)" });
  const database = el("input", { value: existing?.database || "", placeholder: "database (optional)" });
  const status = el("span", { class: "muted" });
  const cfgFromInputs = () => ({
    engine: engine.value,
    connection_name: name.value || host.value,
    host: host.value,
    port: parseInt(port.value) || undefined,
    user: user.value,
    password: pass.value || undefined,
    database: database.value || undefined,
  });
  const test = el("button", { class: "action" }, "Test");
  test.onclick = async () => {
    status.textContent = "testing…";
    try {
      const r = await api("/api/databases/test-connection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cfgFromInputs()),
      });
      status.textContent = r.connected ? " connected ✓" : " failed ✗ " + (r.error || "");
    } catch (e) {
      status.textContent = e.message;
    }
  };
  const submit = el("button", { class: "action" }, isEdit ? "Save" : "Add");
  submit.onclick = async () => {
    const cfg = cfgFromInputs();
    try {
      if (isEdit) {
        await api(`/api/databases/${encodeURIComponent(existing.connection_name)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(cfg),
        });
      } else {
        await api("/api/databases", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(cfg),
        });
      }
      alert(isEdit ? "Saved" : "Added");
      router();
    } catch (e) {
      status.textContent = e.message;
    }
  };
  [engine, name, host, port, user, pass, database].forEach(
    (i) => ((i.style.margin = "4px"), (i.style.display = "block"))
  );
  const actions = el("div", { style: "margin-top:8px" });
  actions.append(submit, " ", test, " ", status);
  if (isEdit) {
    const cancel = el("button", { class: "action" }, "Cancel");
    cancel.onclick = () => {
      const cur = document.getElementById("manual-card");
      if (cur) cur.replaceWith(manualForm());
      cur?.scrollIntoView({ behavior: "smooth" });
    };
    actions.append(" ", cancel);
  }
  const guide = el("button", { class: "action" }, "Read-only user guide");
  guide.onclick = async () => {
    const blk = await guidanceBlock(engine.value);
    card.append(blk);
  };
  card.append(engine, name, host, port, user, pass, database, actions, guide);
  return card;
}

function editConnection(d) {
  const cur = document.getElementById("manual-card");
  const form = manualForm(d);
  form.id = "manual-card";
  if (cur) cur.replaceWith(form);
  else view.append(form);
  form.scrollIntoView({ behavior: "smooth" });
}

async function renderDashboard() {
  view.innerHTML = "<div class='card'><p class='muted'>Loading…</p></div>";
  const [global, dbs, last] = await Promise.all([
    api("/api/metrics/global"),
    api("/api/databases"),
    api("/api/scan/last").catch(() => null),
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

  const dbCard = el("div", { class: "card" }, "<h3>Databases</h3>");
  const dbTable = el("table");
  dbTable.innerHTML =
    "<thead><tr><th>Database</th><th>Engine</th><th>Status</th><th>Size</th><th>Last scanned</th><th>Duration</th></tr></thead>";
  const dbBody = el("tbody");
  dbs.forEach((d) => {
    const size = d.total_size_mb != null ? `${d.total_size_mb} MB` : "—";
    const scanned = d.last_scanned ? new Date(d.last_scanned).toLocaleString() : "never";
    const duration = d.scan_duration_s != null ? `${d.scan_duration_s}s` : "—";
    dbBody.append(
      el(
        "tr",
        {},
        `<td>${d.connection_name}</td><td>${d.engine}</td>` +
          `<td class="status ${d.status || "unknown"}">${d.status || "—"}</td>` +
          `<td>${size}</td><td>${scanned}</td><td>${duration}</td>`
      )
    );
  });
  dbTable.append(dbBody);
  dbCard.append(dbTable);
  view.append(dbCard);

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
  if (last) {
    const dur = last.duration_s != null ? ` in ${last.duration_s}s` : "";
    const when = last.finished ? new Date(last.finished).toLocaleString() : "";
    scanCard.append(el("p", { class: "muted" }, `Last scan: ${last.status}${dur} (${when})`));
  }
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
      status.textContent = `${j.status} — ${j.message} (${j.percent}%)${j.duration_s != null ? " in " + j.duration_s + "s" : ""}`;
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
