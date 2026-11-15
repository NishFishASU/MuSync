const state = {
  devices: [],
  sources: [],
  transport: null,
  selectedId: null,
  scrubbing: false,
  patchTimers: {},
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return null;
  return res.json();
}

function fmtMs(ms) {
  const total = Math.max(0, Math.floor((ms || 0) / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function selectedDevice() {
  return state.devices.find((d) => d.id === state.selectedId) || null;
}

function renderHealth(health) {
  const el = $("health");
  const label = health.backend === "mock" ? "mock" : "pipewire";
  const sinks = `${health.enabled_count}/${health.sink_count} sinks`;
  el.textContent = `${label} · ${sinks}`;
  el.dataset.ok = String(Boolean(health.ok));
  el.title = health.detail || "";
}

function renderDevices() {
  const list = $("device-list");
  list.innerHTML = "";
  for (const d of state.devices) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "device-row";
    btn.setAttribute("aria-selected", String(d.id === state.selectedId));
    btn.innerHTML = `
      <span>
        <span class="device-title">${escapeHtml(d.name)}</span>
        <span class="device-sub">${escapeHtml(d.hci || "—")} · ${escapeHtml(d.mac || "no MAC")} · delay ${d.delay_ms} ms · vol ${Math.round(d.volume * 100)}%</span>
      </span>
      <span class="status-pill ${d.connected ? "linked" : "missing"}">${d.connected ? "linked" : "missing"}${d.enabled ? "" : " · off"}</span>
    `;
    btn.addEventListener("click", () => {
      state.selectedId = d.id;
      renderDevices();
      renderDetail();
    });
    li.appendChild(btn);
    list.appendChild(li);
  }
}

function renderDetail() {
  const panel = $("detail-panel");
  const d = selectedDevice();
  if (!d) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  $("detail-name").textContent = d.name;
  $("detail-meta").textContent = `${d.hci || "hci?"} · ${d.mac || "MAC unset"} · ${d.connected ? "linked" : "missing"}`;
  $("delay").value = String(d.delay_ms);
  $("delay-readout").textContent = `${d.delay_ms} ms`;
  $("volume").value = String(Math.round(d.volume * 100));
  $("volume-readout").textContent = `${Math.round(d.volume * 100)}%`;
  $("mute").checked = Boolean(d.mute);
  $("enabled").checked = Boolean(d.enabled);
}

function renderTransport() {
  const t = state.transport;
  if (!t) return;
  $("now-playing").textContent = t.title || "No track";
  $("pos-label").textContent = fmtMs(t.position_ms);
  $("dur-label").textContent = fmtMs(t.duration_ms);
  const slider = $("position");
  slider.max = String(t.duration_ms || 0);
  if (!state.scrubbing) {
    slider.value = String(t.position_ms || 0);
  }
  const playing = t.state === "playing";
  $("btn-play").disabled = playing;
  $("btn-pause").disabled = !playing;
}

function renderSources() {
  const list = $("source-list");
  list.innerHTML = "";
  const activeId = state.transport?.source_id;
  for (const s of state.sources) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "source-row";
    btn.disabled = !s.available;
    const active = s.id === activeId ? " · active" : "";
    const note = s.note ? ` · ${s.note}` : "";
    btn.innerHTML = `
      <span>
        <span class="source-title">${escapeHtml(s.title)}</span>
        <span class="source-sub">${escapeHtml(s.type)}${escapeHtml(active)}${escapeHtml(note)}</span>
      </span>
      <span class="status-pill ${s.available ? "linked" : "missing"}">${s.available ? "ready" : "later"}</span>
    `;
    if (s.available) {
      btn.addEventListener("click", async () => {
        state.transport = await api("/api/sources/active", {
          method: "PUT",
          body: JSON.stringify({ id: s.id }),
        });
        renderTransport();
        renderSources();
      });
    }
    li.appendChild(btn);
    list.appendChild(li);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function queueDevicePatch(partial) {
  const id = state.selectedId;
  if (!id) return;
  const device = selectedDevice();
  if (!device) return;
  Object.assign(device, partial);
  renderDevices();
  renderDetail();

  clearTimeout(state.patchTimers[id]);
  state.patchTimers[id] = setTimeout(async () => {
    try {
      const updated = await api(`/api/devices/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(partial),
      });
      const idx = state.devices.findIndex((d) => d.id === id);
      if (idx >= 0) state.devices[idx] = updated;
      renderDevices();
      renderDetail();
    } catch (err) {
      console.error(err);
      await refresh();
    }
  }, 180);
}

async function setTransport(body) {
  state.transport = await api("/api/transport", {
    method: "PUT",
    body: JSON.stringify(body),
  });
  renderTransport();
}

async function refresh() {
  const [health, devices, transport, sources] = await Promise.all([
    api("/api/health"),
    api("/api/devices"),
    api("/api/transport"),
    api("/api/sources"),
  ]);
  state.devices = devices;
  state.transport = transport;
  state.sources = sources;
  if (!state.selectedId && devices.length) {
    state.selectedId = devices[0].id;
  }
  renderHealth(health);
  renderDevices();
  renderDetail();
  renderTransport();
  renderSources();
}

function bindControls() {
  $("btn-play").addEventListener("click", () => setTransport({ state: "playing" }));
  $("btn-pause").addEventListener("click", () => setTransport({ state: "paused" }));
  $("btn-stop").addEventListener("click", () => setTransport({ state: "stopped" }));

  const position = $("position");
  position.addEventListener("pointerdown", () => {
    state.scrubbing = true;
  });
  position.addEventListener("pointerup", async () => {
    state.scrubbing = false;
    await setTransport({ position_ms: Number(position.value) });
  });
  position.addEventListener("change", async () => {
    state.scrubbing = false;
    await setTransport({ position_ms: Number(position.value) });
  });

  $("delay").addEventListener("input", (e) => {
    const delay_ms = Number(e.target.value);
    $("delay-readout").textContent = `${delay_ms} ms`;
    queueDevicePatch({ delay_ms });
  });
  $("volume").addEventListener("input", (e) => {
    const pct = Number(e.target.value);
    $("volume-readout").textContent = `${pct}%`;
    queueDevicePatch({ volume: pct / 100 });
  });
  $("mute").addEventListener("change", (e) => {
    queueDevicePatch({ mute: e.target.checked });
  });
  $("enabled").addEventListener("change", (e) => {
    queueDevicePatch({ enabled: e.target.checked });
  });
  $("btn-close-detail").addEventListener("click", () => {
    state.selectedId = null;
    renderDevices();
    renderDetail();
  });
}

async function pollTransport() {
  try {
    state.transport = await api("/api/transport");
    renderTransport();
  } catch (err) {
    console.error(err);
  }
}

bindControls();
refresh()
  .then(() => {
    setInterval(pollTransport, 1000);
  })
  .catch((err) => {
    $("health").textContent = "API offline";
    $("health").dataset.ok = "false";
    console.error(err);
  });
