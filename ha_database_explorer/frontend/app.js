// Resolve the API base: under HA ingress, use the ingress path; else relative path
const BASE = (() => {
  const path = location.pathname;
  // HA ingress path: /api/hassio_ingress/<token>/
  if (path.includes("/api/hassio_ingress/")) {
    // Use the full ingress path as base (e.g., /api/hassio_ingress/token/)
    const match = path.match(/^(\/api\/hassio_ingress\/[^/]+\/)/);
    return match ? match[1] : "/api/";
  }
  return path.replace(/\/[^/]*$/, "") + "/";
})();

const VERSION = "0.3.7"; // INJECTED_AT_BUILD - update on release

function fmtMB(mb) {
  return mb != null && mb !== "" ? Math.round(mb).toLocaleString() + " MB" : "Unknown";
}

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
    about: renderAbout,
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
      "<thead><tr><th>Database</th><th>Engine</th><th>Host</th><th>Status</th><th>Last scanned</th><th>Duration</th><th>Retention</th><th></th></tr></thead>";
    const cfgBody = el("tbody");
    configured.forEach((d) => {
      const scanned = d.last_scanned ? new Date(d.last_scanned).toLocaleString() : "never";
      const duration = d.scan_duration_s != null ? `${d.scan_duration_s}s` : "—";
      const hostLabel = d.host ? `${d.host}${d.port ? ":" + d.port : ""}` : d.path || "—";
      const isInflux = d.engine === "influxdb";
      
      let retentionDisplay = "";
      let retentionActions = "";
      
      if (isInflux) {
        // InfluxDB: show RP list
        let rpList = [];
        try {
          rpList = d.influxdb_rp_json ? JSON.parse(d.influxdb_rp_json) : [];
        } catch (e) {}
        if (rpList.length) {
          retentionDisplay = rpList.map(rp => {
            const badge = rp.default ? " <span style='color:#fbbf24'>★</span>" : "";
            return `${rp.name} (${rp.duration})${badge}`;
          }).join(", ");
        } else {
          retentionDisplay = "Not Set";
        }
        retentionActions = `
          <td>
            <button class="action small" onclick="manageInfluxRPs('${d.connection_name}')">Manage RPs</button>
          </td>
          <td>
            <button class="action small" onclick="refreshRetention('${d.connection_name}')">⟳</button>
          </td>
        `;
      } else {
        // HA Recorder: show retention_days
        const retentionDays = d.retention_days;
        retentionDisplay = retentionDays !== null && retentionDays !== undefined
          ? `${retentionDays} days`
          : "Not Set";
        retentionActions = `
          <td>
            ${retentionDays !== null && retentionDays !== undefined
              ? `<button class="action small" onclick="editRetention('${d.connection_name}')">Edit</button>`
              : ""}
          </td>
          <td>
            ${retentionDays !== null && retentionDays !== undefined
              ? `<button class="action small" onclick="refreshRetention('${d.connection_name}')">⟳</button>`
              : ""}
          </td>
        `;
      }
      
      const tr = el(
        "tr",
        {},
        `<td>${d.connection_name}</td><td>${d.engine}</td><td>${hostLabel}</td>` +
          `<td class="status ${d.status || "unknown"}">${d.status || "—"}</td>` +
          `<td>${scanned}</td><td>${duration}</td>` +
          `<td>${retentionDisplay}</td>` +
          retentionActions +
          `<td>` +
          `<button class="action" onclick="editConnection(arguments[0])">Edit</button>` +
          `<button class="action" onclick="deleteConnection('${d.connection_name}')">Remove</button>` +
          `</td>`
      );
      cfgBody.append(tr);
    });
    cfgTable.append(cfgBody);
    cfgCard.append(cfgTable);
    view.append(cfgCard);
  }

  // Scan Schedule card
  const scheduleCard = el("div", { class: "card" }, "<h3>Scan Schedule</h3>");
  const cronInfo = await api("/api/config/scan-cron").catch(() => ({ cron: "30 3 * * *", next_run: null }));
  const cronDisplay = el("div", { style: "margin-bottom:12px" });
  cronDisplay.innerHTML = `<strong>Current:</strong> <code>${cronInfo.cron}</code> (HA local time)`;
  if (cronInfo.next_run) {
    cronDisplay.innerHTML += ` &nbsp; <span class="muted">Next run: ${new Date(cronInfo.next_run).toLocaleString()}</span>`;
  }
  const cronHelp = el("p", { class: "muted", style: "margin:8px 0" },
    "Format: <code>min hour day month dow</code>. Default <code>30 3 * * *</code> = 03:30 daily. " +
    "Examples: <code>0 * * * *</code> (hourly), <code>0 2 * * 0</code> (Sundays 02:00)."
  );
  const cronInput = el("input", { type: "text", value: cronInfo.cron, placeholder: "e.g. 30 3 * * *", style: "width:100%;padding:8px;margin-bottom:8px" });
  const saveCron = el("button", { class: "action" }, "Save Schedule");
  const cronStatus = el("span", { class: "muted" });
  saveCron.onclick = async () => {
    saveCron.disabled = true;
    cronStatus.textContent = "saving…";
    try {
      const r = await api("/api/config/scan-cron", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cron: cronInput.value.trim() }),
      });
      cronStatus.textContent = `saved ✓ (next: ${r.next_run ? new Date(r.next_run).toLocaleString() : "?"})`;
      cronInput.value = r.cron;
      cronDisplay.innerHTML = `<strong>Current:</strong> <code>${r.cron}</code> (HA local time)`;
      if (r.next_run) {
        cronDisplay.innerHTML += ` &nbsp; <span class="muted">Next run: ${new Date(r.next_run).toLocaleString()}</span>`;
      }
    } catch (e) {
      cronStatus.textContent = `failed ✗ ${e.message}`;
    } finally {
      saveCron.disabled = false;
    }
  };
  scheduleCard.append(cronDisplay, cronHelp, cronInput, saveCron, " ", cronStatus);
  view.append(scheduleCard);

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

async function deleteConnection(connectionName) {
  if (!confirm(`Remove ${connectionName}?`)) return;
  await api(`/api/databases/${encodeURIComponent(connectionName)}`, { method: "DELETE" });
  router();
}

// InfluxDB RP Management
async function manageInfluxRPs(connectionName) {
  // Fetch current RPs
  const resp = await api(`/api/databases/${connectionName}/retention`);
  const rps = resp.influxdb_rp || [];
  
  const modal = el("div", { class: "modal-overlay" });
  const content = el("div", { class: "modal", style: "max-width:600px;max-height:80vh;overflow:auto" });
  
  // Header
  const header = el("div", { style: "display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #eee;padding:12px 16px" });
  header.append(el("h3", {}, `Retention Policies — ${connectionName}`));
  const closeBtn = el("button", { class: "action muted", onclick: () => modal.remove() }, "Close");
  header.append(closeBtn);
  content.append(header);
  
  // RP List
  const rpList = el("div", { style: "padding:16px" });
  if (rps.length === 0) {
    rpList.append(el("p", { class: "muted" }, "No retention policies found."));
  } else {
    const table = el("table", { style: "width:100%;border-collapse:collapse" });
    table.innerHTML = "<thead><tr><th>Name</th><th>Duration</th><th>Shard Group</th><th>Replica N</th><th>Default</th><th>Actions</th></tr></thead>";
    const tbody = el("tbody");
    rps.forEach(rp => {
      const tr = el("tr", { style: "border-bottom:1px solid #eee" });
      tr.innerHTML = `
        <td style="padding:8px">${rp.name}</td>
        <td style="padding:8px">${rp.duration}</td>
        <td style="padding:8px">${rp.shard_group_duration || "—"}</td>
        <td style="padding:8px">${rp.replica_n || "—"}</td>
        <td style="padding:8px">${rp.default ? "★" : "—"}</td>
        <td style="padding:8px">
          <button class="action small" onclick="editInfluxRP('${connectionName}', ${JSON.stringify(rp).replace(/"/g, '"')})">Edit</button>
          <button class="action small muted" onclick="deleteInfluxRP('${connectionName}', '${rp.name}')">Delete</button>
        </td>
      `;
      tbody.append(tr);
    });
    table.append(tbody);
    rpList.append(table);
  }
  content.append(rpList);
  
  // Add RP Form
  const addForm = el("div", { style: "padding:16px;border-top:1px solid #eee" });
  addForm.append(el("h4", {}, "Create / Alter Retention Policy"));
  
  const row1 = el("div", { style: "display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap" });
  const nameInput = el("input", { type: "text", placeholder: "RP name (e.g., autogen)", style: "flex:1;min-width:150px;padding:8px" });
  const durationInput = el("input", { type: "text", placeholder: "Duration (e.g., 30d, 7d, INF)", style: "flex:1;min-width:150px;padding:8px" });
  row1.append(el("label", {}, "Name"), nameInput, el("label", {}, "Duration"), durationInput);
  
  const row2 = el("div", { style: "display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap" });
  const sgInput = el("input", { type: "text", placeholder: "Shard group duration (optional)", style: "flex:1;min-width:150px;padding:8px" });
  const replInput = el("input", { type: "number", placeholder: "Replication N (optional)", style: "width:100px;padding:8px" });
  const defaultCheck = el("input", { type: "checkbox", id: "rp-default" });
  const defaultLabel = el("label", { for: "rp-default", style: "display:flex;align-items:center;gap:4px" }, "Make default");
  defaultLabel.prepend(defaultCheck);
  row2.append(el("label", {}, "Shard Group"), sgInput, el("label", {}, "Replica N"), replInput, defaultLabel);
  
  const saveBtn = el("button", { class: "action", onclick: `saveInfluxRP('${connectionName}')` }, "Save RP");
  const statusSpan = el("span", { class: "muted", style: "margin-left:12px" });
  
  addForm.append(row1, row2, saveBtn, " ", statusSpan);
  content.append(addForm);
  
  modal.append(content);
  view.append(modal);
  
  // Attach save handler with closure
  saveBtn.onclick = async () => {
    const name = nameInput.value.trim();
    const duration = durationInput.value.trim();
    const shard_group_duration = sgInput.value.trim() || null;
    const replica_n = replInput.value ? parseInt(replInput.value) : null;
    const make_default = defaultCheck.checked;
    
    if (!name || !duration) {
      statusSpan.textContent = "Name and duration required";
      return;
    }
    
    // Validate duration
    if (duration !== "INF" && !/^\d+[dhw]$/.test(duration)) {
      statusSpan.textContent = "Duration must be 'INF' or format like '30d', '7d', '24h', '4w'";
      return;
    }
    
    saveBtn.disabled = true;
    statusSpan.textContent = "saving…";
    
    try {
      const r = await api(`/api/databases/${connectionName}/retention`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          influxdb_rp: {
            action: "create",
            name,
            duration,
            shard_group_duration,
            replica_n,
            make_default
          }
        })
      });
      statusSpan.textContent = "saved ✓";
      modal.remove();
      router();
    } catch (e) {
      statusSpan.textContent = `failed: ${e.message}`;
    } finally {
      saveBtn.disabled = false;
    }
  };
}

async function renderDashboard() {
  view.innerHTML = "<div class='card'><p class='muted'>Loading…</p></div>";
  const [global, dbs, last] = await Promise.all([
    api("/api/metrics/global"),
    api("/api/databases"),
    api("/api/scan/last").catch(() => null),
  ]);
  if (!global || global.database_count === 0) {
    view.innerHTML = "<div class='card'><p class='muted'>No data available yet. Configure databases in Setup tab.</p></div>";
    return;
  }
  const grid = el("div", { class: "grid" });
  grid.append(
    metricCard("Total disk footprint", fmtMB(global.total_size_mb)),
    metricCard("Total records", global.total_records.toLocaleString()),
    metricCard("Databases", global.database_count),
    metricCard("Overlapping entities", global.overlap_entity_count)
  );
  view.append(grid);

  const engineCard = el("div", { class: "card" }, "<h3>Storage by engine</h3>");
  // Calculate total known size (exclude engines with null or 0 MB)
  const totalKnown = Object.values(global.by_engine_mb || {})
    .filter(mb => mb != null && mb > 0)
    .reduce((sum, mb) => sum + mb, 0);
  for (const [eng, mb] of Object.entries(global.by_engine_mb || {})) {
    const sizeText = fmtMB(mb);
    const pct = totalKnown && mb ? (mb / totalKnown) * 100 : 0;
    engineCard.append(el("p", {}, `${eng}: ${sizeText} (${pct.toFixed(1)}%)`));
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
    const size = fmtMB(d.total_size_mb);
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

function fmtDate(iso) { return iso ? new Date(iso).toLocaleString() : "—"; }

async function renderEntities() {
  view.innerHTML = "<div class='card'><p class='muted'>Loading…</p></div>";
  const rows = await api("/api/metrics/entities?sort=record_count&order=desc");
  view.innerHTML = "";
  if (!rows.length) {
    view.innerHTML = "<div class='card'><p class='muted'>No entities found. Run a scan in Setup tab.</p></div>";
    return;
  }
  const card = el("div", { class: "card" });
  const headerRow = el("div", { style: "display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:8px" });
  headerRow.append(el("h3", { style: "margin:0" }, "Entity Bloat Explorer"));
  const search = el("input", { placeholder: "search entity_id…", style: "flex:1;min-width:200px;padding:8px" });
  headerRow.append(search);
  const sources = [...new Set(rows.map(r => r.connection_name).filter(Boolean))].sort();
  const sourceFilter = el("select", { style: "padding:8px;min-width:180px" });
  sourceFilter.append(el("option", { value: "" }, "All sources"));
  sources.forEach(s => sourceFilter.append(el("option", { value: s }, s)));
  headerRow.append(sourceFilter);
  card.append(headerRow);

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
      result.textContent = `Keeping ${r.retention_days} days would free ~${fmtMB(r.total_freed_mb)} across ${r.entities.length} entities.`;
    } catch (e) {
      result.textContent = e.message;
    }
  };
  advisor.append(el("p", { class: "muted" }, "Estimate disk savings if entities were purged beyond a retention window."), days, " days ", calc, result);
  card.append(advisor);
  const table = el("table");
  table.innerHTML =
    "<thead><tr>" +
    "<th data-sort='entity_id'>entity_id ▲</th>" +
    "<th data-sort='record_count'>records ▼</th>" +
    "<th data-sort='start_date' title=\"Earliest recorded state change. Missing = no state changes recorded or query couldn't determine timestamp.\">oldest ⓘ</th>" +
    "<th data-sort='end_date'>latest</th>" +
    "<th data-sort='updates_per_day'>updates/day</th>" +
    "<th data-sort='connection_name'>source</th>" +
    "</tr></thead>";
  const tbody = el("tbody");
  table.append(tbody);
  let currentSort = "record_count";
  let currentOrder = "desc";
  let currentSourceFilter = "";
  const draw = (q) => {
    tbody.innerHTML = "";
    rows
      .filter((r) => (!q || r.entity_id.includes(q)) && (!currentSourceFilter || r.connection_name === currentSourceFilter))
      .slice(0, 500)
      .forEach((r) =>
        tbody.append(
          el(
            "tr",
            {},
            `<td><a href="#" class="entity-link" data-db="${r.db_id}" data-entity="${r.entity_id}">${r.entity_id}</a></td><td>${r.record_count}</td><td>${fmtDate(r.start_date)}</td><td>${fmtDate(r.end_date)}</td><td>${r.updates_per_day != null ? Math.round(r.updates_per_day) : "—"}</td><td>${r.connection_name || "—"}</td>`
          )
        )
      );
    // Attach click handlers for entity values drill-down
    tbody.querySelectorAll("a.entity-link").forEach(a => {
      a.onclick = async (e) => {
        e.preventDefault();
        await showEntityValues(a.dataset.db, a.dataset.entity);
      };
    });
  };
  table.querySelectorAll("th[data-sort]").forEach((th) => {
    th.style.cursor = "pointer";
    th.onclick = async () => {
      const sort = th.dataset.sort;
      if (sort === currentSort) {
        currentOrder = currentOrder === "desc" ? "asc" : "desc";
      } else {
        currentSort = sort;
        currentOrder = "desc";
      }
      const res = await api(`/api/metrics/entities?sort=${currentSort}&order=${currentOrder}`);
      rows.length = 0;
      rows.push(...res);
      draw("");
      table.querySelectorAll("th[data-sort]").forEach((h) => {
        const arrow = h.dataset.sort === currentSort ? (currentOrder === "desc" ? " ▼" : " ▲") : "";
        h.textContent = h.textContent.replace(/ [▲▼]$/, "") + arrow;
      });
    };
  });
  sourceFilter.onchange = () => {
    currentSourceFilter = sourceFilter.value;
    draw(search.value);
  };
  search.oninput = () => draw(search.value);
  draw("");
  card.append(table);
  view.append(card);
}

async function renderOverlap() {
  view.innerHTML = "<div class='card'><p class='muted'>Loading…</p></div>";
  const [rows, databases] = await Promise.all([
    api("/api/metrics/overlap"),
    api("/api/databases"),
  ]);
  view.innerHTML = "";
  if (!rows.length) {
    view.innerHTML = "<div class='card'><p class='muted'>No overlapping entities detected. Run a scan to detect overlap.</p></div>";
    return;
  }
  const card = el("div", { class: "card" });
  card.append(el("h3", {}, "Overlap Matrix & Exclusion Generator"));
  const split = el("div", { class: "split" });
  const left = el("div");
  const right = el("div");
  const selected = new Set();
  const code = el("code", { class: "block" }, "recorder:\n  exclude:\n    entities:");
  if (!rows.length) left.append(el("p", { class: "muted" }, "No overlapping entities detected."));
  
  // Build db_id -> connection_name map
  const dbNameMap = {};
  databases.forEach(d => { dbNameMap[d.id] = d.connection_name; });
  
  rows.forEach((r) => {
    const label = el("label", { style: "display:block;margin-bottom:4px" });
    const cb = el("input", { type: "checkbox" });
    cb.onchange = () => {
      cb.checked ? selected.add(r.entity_id) : selected.delete(r.entity_id);
      updateYaml();
    };
    
    // Determine primary DB (fewest records = first in present_in after sorting by count)
    // The overlap API already sorts by count, so present_in[0] is primary
    const sources = (r.present_in || []).map(dbId => {
      const name = dbNameMap[dbId] || dbId;
      return name;
    });
    
    const sourceSpans = sources.map((src, idx) => {
      const isPrimary = idx === 0;
      const star = isPrimary ? " <span style='color:#fbbf24'>★</span>" : "";
      return `<span title="${src}${isPrimary ? ' (primary — fewest records)' : ''}">${src}${star}</span>`;
    }).join(" → ");
    
    label.append(cb, ` ${r.entity_id} [${sourceSpans}] (redundant: ${r.total_redundant_records})`);
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

// Retention management for Setup tab
async function editRetention(connectionName) {
  const current = await api(`/api/databases/${connectionName}/retention`).then(r => r.retention_days);
  const input = document.createElement("input");
  input.type = "number";
  input.min = "1";
  input.max = "365";
  if (current !== null && current !== undefined) input.value = String(current);
  input.style.width = "80px";
  input.style.marginRight = "8px";
  const saveBtn = el("button", { class: "action", style: "margin-left:8px" }, "Save");
  const cancelBtn = el("button", { class: "muted", style: "margin-left:8px" }, "Cancel");
  const row = event.target.closest("tr");
  const cells = row.querySelectorAll("td");
  // cells[4] = Retention display, cells[6] = action buttons column
  cells[4].textContent = current !== null && current !== undefined ? String(current) : "—";
  cells[6].innerHTML = "";
  cells[6].append(input, saveBtn, cancelBtn);
  saveBtn.onclick = async () => {
    const days = parseInt(input.value);
    if (isNaN(days) || days < 1 || days > 365) return alert("Enter 1..365");
    const r = await api(`/api/databases/${connectionName}/retention`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ retention_days: days }),
    });
    if (r.saved) {
      cells[4].textContent = `${r.retention_days} days`;
      cells[6].innerHTML = `<button class="action small" onclick="editRetention('${connectionName}')">Edit</button>
<button class="action small" onclick="refreshRetention('${connectionName}')">⟳</button>`;
    }
  };
  cancelBtn.onclick = () => {
    // restore: re-read from API to get the correct value
    api(`/api/databases/${connectionName}/retention`).then(r => {
      cells[4].textContent = r.retention_days !== null ? `${r.retention_days} days` : "Not Set";
    });
    cells[6].innerHTML = `<button class="action small" onclick="editRetention('${connectionName}')">Edit</button>
<button class="action small" onclick="refreshRetention('${connectionName}')">⟳</button>`;
  };
}

async function refreshRetention(connectionName) {
  const r = await api(`/api/databases/${connectionName}/retention/refresh`);
  // Find the row containing this connection name (first cell)
  const allRows = document.querySelectorAll("tr");
  let row = null;
  for (const tr of allRows) {
    if (tr.cells && tr.cells[0] && tr.cells[0].textContent.trim() === connectionName) {
      row = tr;
      break;
    }
  }
  if (!row) return;
  const cells = row.querySelectorAll("td");
  const display = r.retention_days !== null ? `${r.retention_days} days` : "Not Set";
  cells[4].textContent = display; // Retention column is index 4
  // Update action buttons
  cells[6].innerHTML = `<button class="action small" onclick="editRetention('${connectionName}')">Edit</button>
<button class="action small" onclick="refreshRetention('${connectionName}')">⟳</button>`;
}

// InfluxDB RP Edit/Delete (called from modal)
async function editInfluxRP(connectionName, rp) {
  // Pre-fill the form in the modal
  const nameInput = document.querySelector("input[placeholder='RP name (e.g., autogen)']");
  const durationInput = document.querySelector("input[placeholder='Duration (e.g., 30d, 7d, INF)']");
  const sgInput = document.querySelector("input[placeholder='Shard group duration (optional)']");
  const replInput = document.querySelector("input[placeholder='Replication N (optional)']");
  const defaultCheck = document.querySelector("#rp-default");
  const saveBtn = document.querySelector("button[onclick*='saveInfluxRP']");
  
  if (nameInput) nameInput.value = rp.name;
  if (durationInput) durationInput.value = rp.duration;
  if (sgInput) sgInput.value = rp.shard_group_duration || "";
  if (replInput) replInput.value = rp.replica_n || "";
  if (defaultCheck) defaultCheck.checked = rp.default;
  
  // Change button to update mode
  if (saveBtn) {
    saveBtn.textContent = "Update RP";
    saveBtn.onclick = async () => {
      const name = nameInput.value.trim();
      const duration = durationInput.value.trim();
      const shard_group_duration = sgInput.value.trim() || null;
      const replica_n = replInput.value ? parseInt(replInput.value) : null;
      const make_default = defaultCheck.checked;
      
      if (!name || !duration) return alert("Name and duration required");
      if (duration !== "INF" && !/^\d+[dhw]$/.test(duration)) return alert("Duration must be 'INF' or format like '30d', '7d', '24h', '4w'");
      
      saveBtn.disabled = true;
      saveBtn.textContent = "updating…";
      
      try {
        await api(`/api/databases/${connectionName}/retention`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            influxdb_rp: {
              action: "create", // InfluxDB uses CREATE for both create and alter
              name,
              duration,
              shard_group_duration,
              replica_n,
              make_default
            }
          })
        });
        document.querySelector(".modal-overlay")?.remove();
        router();
      } catch (e) {
        alert(`failed: ${e.message}`);
      } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = "Update RP";
      }
    };
  }
}

async function deleteInfluxRP(connectionName, rpName) {
  if (!confirm(`Delete retention policy "${rpName}"? This cannot be undone.`)) return;
  try {
    await api(`/api/databases/${connectionName}/retention`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        influxdb_rp: { action: "delete", name: rpName }
      })
    });
    document.querySelector(".modal-overlay")?.remove();
    router();
  } catch (e) {
    alert(`failed: ${e.message}`);
  }
}

// Entity Values Drill-down Modal
async function showEntityValues(dbId, entityId) {
  const modal = el("div", { class: "modal-overlay" });
  const content = el("div", { class: "modal", style: "max-width:900px;max-height:80vh;overflow:auto" });
  
  // Header
  const header = el("div", { style: "display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #eee;padding:12px 16px" });
  header.append(el("h3", {}, `Entity Values — ${entityId}`));
  const closeBtn = el("button", { class: "action muted", onclick: () => modal.remove() }, "Close");
  header.append(closeBtn);
  content.append(header);
  
  // Loading state
  const body = el("div", { style: "padding:16px" });
  body.append(el("p", { class: "muted" }, "Loading…"));
  content.append(body);
  modal.append(content);
  view.append(modal);
  
  let currentOffset = 0;
  const limit = 100;
  
  async function loadValues(append = false) {
    try {
      const resp = await api(`/api/entities/${dbId}/${entityId}/values?limit=${limit}&offset=${currentOffset}`);
      const values = resp.values || [];
      
      if (!append) {
        body.innerHTML = "";
      }
      
      if (values.length === 0 && currentOffset === 0) {
        body.append(el("p", { class: "muted" }, "No values found for this entity."));
        return false;
      }
      
      // Create/update table
      let table = body.querySelector("table");
      if (!table) {
        table = el("table", { style: "width:100%;border-collapse:collapse;font-size:13px" });
        table.innerHTML = "<thead><tr><th>Timestamp</th><th>State</th><th>Attributes</th></tr></thead>";
        const tbody = el("tbody");
        table.append(tbody);
        body.append(table);
      }
      const tbody = table.querySelector("tbody");
      
      // Filter attributes to show only relevant ones
      const relevantAttrs = ["unit_of_measurement", "friendly_name", "device_class", "icon", "state_class"];
      
      values.forEach(v => {
        const tr = el("tr", { style: "border-bottom:1px solid #eee" });
        
        // Format timestamp
        let ts = v.last_updated || v.time || "—";
        if (ts !== "—") {
          try {
            ts = new Date(ts).toLocaleString();
          } catch {}
        }
        
        // Format attributes
        let attrsDisplay = "—";
        try {
          const attrs = v.attributes ? JSON.parse(v.attributes) : {};
          if (v.value !== undefined && v.value !== v.state) {
            attrs.value = v.value;
          }
          const filtered = Object.entries(attrs)
            .filter(([k]) => relevantAttrs.includes(k))
            .map(([k, v]) => `${k}: ${v}`)
            .join(", ");
          if (filtered) attrsDisplay = filtered;
        } catch {}
        
        tr.innerHTML = `<td style="padding:6px;white-space:nowrap">${ts}</td><td style="padding:6px"><code>${v.state || v.value || "—"}</code></td><td style="padding:6px;font-size:12px;color:#666">${attrsDisplay}</td>`;
        tbody.append(tr);
      });
      
      // Load more button
      let loadMoreBtn = body.querySelector(".load-more-btn");
      if (values.length >= limit) {
        if (!loadMoreBtn) {
          loadMoreBtn = el("button", { class: "action load-more-btn", style: "margin-top:12px;width:100%" }, "Load More");
          loadMoreBtn.onclick = async () => {
            currentOffset += limit;
            loadMoreBtn.disabled = true;
            loadMoreBtn.textContent = "Loading…";
            await loadValues(true);
            loadMoreBtn.disabled = false;
            loadMoreBtn.textContent = "Load More";
          };
          body.append(loadMoreBtn);
        }
      } else if (loadMoreBtn) {
        loadMoreBtn.remove();
      }
      
      return values.length >= limit;
    } catch (e) {
      if (currentOffset === 0) {
        body.innerHTML = `<p class="muted">Error loading values: ${e.message}</p>`;
      } else {
        alert(`Error loading more: ${e.message}`);
      }
      return false;
    }
  }
  
  await loadValues();
}

window.showEntityValues = showEntityValues;

// About page
async function renderAbout() {
  view.innerHTML = "";
  const card = el("div", { class: "card" });
  
  card.append(
    el("h2", {}, "About HA Database Explorer"),
    el("p", {}, `Version ${VERSION}`),
    el("p", { class: "muted" }, "Audit storage, retention and cross-database entity overlap across your Home Assistant databases (SQLite, MariaDB/MySQL, InfluxDB 1.8). Privacy-first, local-only."),
    el("hr", {}),
    el("h3", {}, "Resources"),
    el("ul", {}, `
      <li><a href="https://github.com/PeteOlds/HA-DatabaseExplorer" target="_blank" rel="noopener">GitHub Repository</a></li>
      <li><a href="https://github.com/PeteOlds/HA-DatabaseExplorer#usage" target="_blank" rel="noopener">Usage Instructions</a></li>
      <li><a href="https://github.com/PeteOlds/HA-DatabaseExplorer/issues" target="_blank" rel="noopener">Report Issues</a></li>
    `),
    el("hr", {}),
    el("h3", {}, "Quick Start"),
    el("p", {}, "1. Open the <strong>Setup</strong> tab to configure your database connections"),
    el("p", {}, "2. Click <strong>Discover databases</strong> to auto-detect HA Recorder + InfluxDB"),
    el("p", {}, "3. Use <strong>Dashboard</strong> for overview, <strong>Entities</strong> for per-entity analysis"),
    el("p", {}, "4. Click any <strong>entity_id</strong> in Entities tab to see recent state values"),
    el("p", {}, "5. Use <strong>Retention</strong> column in Setup to manage purge policies"),
    el("hr", {}),
    el("p", { class: "muted" }, "Built for Home Assistant. Local-only, privacy-first.")
  );
  view.append(card);
}
router();
