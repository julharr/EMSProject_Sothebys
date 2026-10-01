(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const CONF_RANK = { high: 3, medium: 2, low: 1 };

  const state = {
    articles: [],
    meta: { tags: [], events: [] },
    view: "signals",
    event: null,
    tags: new Set(),
    minConf: "medium",
    q: "",
    maxId: 0,
    newIds: new Set(),
    status: null,
  };

  const savedView = (() => { try { return localStorage.getItem("wsm.view"); } catch { return null; } })();
  if (savedView) state.view = savedView;

  // ---------------------------------------------------------------- data
  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(`${r.status} ${path}`);
    return r.json();
  }

  async function loadArticles(initial) {
    const rows = await api(`/api/articles?since_id=${state.maxId}`);
    if (!rows.length) return 0;
    if (!initial) rows.forEach((a) => state.newIds.add(a.id));
    const ids = new Set(state.articles.map((a) => a.id));
    state.articles = rows.filter((a) => !ids.has(a.id)).concat(state.articles);
    state.articles.sort((a, b) => (b.fetched_at.localeCompare(a.fetched_at)) || (b.published_at || "").localeCompare(a.published_at || ""));
    state.maxId = Math.max(state.maxId, ...rows.map((a) => a.id));
    return rows.length;
  }

  async function loadStatus() {
    state.status = await api("/api/status");
    renderStatus();
  }

  // ---------------------------------------------------------------- formatting
  const fmtTime = (iso) => iso ? new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "—";
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" }) : "";
  function ago(iso) {
    if (!iso) return "";
    const m = Math.round((Date.now() - new Date(iso)) / 60000);
    if (m < 1) return "just now";
    if (m < 60) return `${m}m ago`;
    const h = Math.round(m / 60);
    return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
  }
  function until(iso) {
    const s = Math.max(0, Math.round((new Date(iso) - Date.now()) / 1000));
    const m = Math.floor(s / 60);
    return m ? `${m}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
  }
  const eventColor = (name) => state.meta.events.find((e) => e.name === name)?.color || "var(--ink)";
  const initials = (n) => n.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();

  function confMeter(c) {
    const n = CONF_RANK[c] || 0;
    return `<span class="conf" title="${esc(c)} confidence">${[1, 2, 3].map((i) => `<b class="${i <= n ? "on" : ""}"></b>`).join("")} ${esc(c || "")}</span>`;
  }
  function badge(evt) {
    return `<span class="badge" style="--c:${eventColor(evt)}"><i></i>${esc(evt)}</span>`;
  }

  // ---------------------------------------------------------------- filtering
  function matches(a, { ignoreEvent = false } = {}) {
    if (state.view === "signals") {
      if (!a.event_type) return false;
      if ((CONF_RANK[a.event_confidence] || 0) < CONF_RANK[state.minConf]) return false;
    }
    if (!ignoreEvent && state.event && a.event_type !== state.event) return false;
    if (state.tags.size && !a.tags.some((t) => state.tags.has(t))) return false;
    if (state.q) {
      const hay = `${a.title} ${a.description || ""} ${a.source || ""} ${a.people.map((p) => p.name).join(" ")}`.toLowerCase();
      if (!hay.includes(state.q)) return false;
    }
    return true;
  }

  // ---------------------------------------------------------------- render
  function renderStatus() {
    const s = state.status;
    if (!s) return;
    const pill = $("#mode-pill");
    pill.textContent = s.demo ? "Demo data" : `Live · ${s.classifier === "claude" ? "AI tagging" : "keyword tagging"}`;
    pill.className = `pill ${s.demo ? "demo" : "live"}`;
    $("#interval").textContent = s.interval_minutes;
    const lp = s.last_pull;
    $("#last-pull").textContent = lp ? `${fmtTime(lp.started_at)} · ${lp.status === "error" ? "failed" : `${lp.n_new} new`}` : "never";
    $("#last-pull").title = lp?.error || "";
    $("#quota-used").textContent = s.requests_24h;
    $("#quota-limit").textContent = s.request_limit;
    $("#quota-fill").style.width = `${Math.min(100, (100 * s.requests_24h) / s.request_limit)}%`;
    tickCountdown();
  }

  function tickCountdown() {
    const s = state.status;
    if (!s) return;
    $("#next-pull").textContent = s.demo ? "manual (demo)" : `${fmtTime(s.next_pull_at)} · in ${until(s.next_pull_at)}`;
  }

  function renderSignalCards() {
    const base = state.articles.filter((a) => a.event_type && (CONF_RANK[a.event_confidence] || 0) >= CONF_RANK[state.minConf]);
    $("#signal-cards").innerHTML = state.meta.events.map((e) => {
      const n = base.filter((a) => a.event_type === e.name).length;
      return `<button class="sig-card ${state.event === e.name ? "active" : ""}" data-event="${esc(e.name)}" style="--c:${e.color}" title="${esc(e.blurb)}">
        <div class="n">${n}</div><div class="lbl"><i></i>${esc(e.name)}</div></button>`;
    }).join("");
  }

  function renderTagFilter() {
    $("#tag-filter").innerHTML = state.meta.tags.map((t) =>
      `<button class="chip ${state.tags.has(t) ? "on" : ""}" data-tag="${esc(t)}">${esc(t)}</button>`).join("");
  }

  function renderRows() {
    const rows = state.articles.filter((a) => matches(a));
    const signalCount = state.articles.filter((a) => a.event_type && (CONF_RANK[a.event_confidence] || 0) >= CONF_RANK[state.minConf]).length;
    $("#count-signals").textContent = signalCount;
    $("#count-all").textContent = state.articles.length;
    $("#empty").hidden = rows.length > 0;

    let lastPull = null;
    const html = [];
    for (const a of rows) {
      if (a.pull_id !== lastPull) {
        lastPull = a.pull_id;
        const inPull = rows.filter((r) => r.pull_id === a.pull_id).length;
        html.push(`<tr class="pull-divider"><td colspan="5">Pull · ${fmtDate(a.fetched_at)} ${fmtTime(a.fetched_at)} · ${inPull} ${inPull === 1 ? "story" : "stories"}</td></tr>`);
      }
      const isNew = state.newIds.has(a.id);
      html.push(`<tr class="story ${isNew ? "new" : ""}" data-id="${a.id}">
        <td class="time-cell"><div class="time">${fmtTime(a.fetched_at)}<span class="ago">pub. ${ago(a.published_at)}</span></div></td>
        <td>${a.event_type ? `<div class="signal">${badge(a.event_type)}${confMeter(a.event_confidence)}</div>` : `<span class="no-signal">—</span>`}</td>
        <td>
          <div class="headline">${isNew ? `<span class="new-badge">NEW</span>` : ""}${esc(a.title)}</div>
          ${a.description ? `<div class="desc">${esc(a.description)}</div>` : ""}
          <div class="byline"><span class="src">${esc(a.source)}</span></div>
        </td>
        <td class="tags-cell"><div class="tags">${a.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div></td>
        <td class="people-cell"><div class="people">${a.people.map((p) => `<span>${esc(p.name)}${p.role ? ` <span class="role">${esc(p.role)}</span>` : ""}</span>`).join("") || `<span class="no-signal">—</span>`}</div></td>
      </tr>`);
    }
    $("#rows").innerHTML = html.join("");
  }

  function renderAll() {
    renderSignalCards();
    renderTagFilter();
    renderRows();
    document.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === state.view));
    $("#confidence").disabled = state.view !== "signals";
  }

  // ---------------------------------------------------------------- drawer
  function familyRow(label, people) {
    const vals = people?.length
      ? people.map((p) => p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.name)}</a>` : `<span>${esc(p.name)}</span>`).join("")
      : `<span class="none">Not recorded</span>`;
    return `<span class="k">${label}</span><div class="vals">${vals}</div>`;
  }

  function personCard(p) {
    const avatar = p.image ? `<img class="avatar" src="${esc(p.image)}" alt="">` : `<div class="avatar">${esc(initials(p.name))}</div>`;
    const name = p.wikipedia_url ? `<a href="${esc(p.wikipedia_url)}" target="_blank" rel="noopener">${esc(p.name)}</a>` : esc(p.name);
    if (!p.found) {
      return `<div class="person">${avatar}<div>
        <div class="p-name">${name}</div>${p.role ? `<span class="p-role">${esc(p.role)}</span>` : ""}
        <div class="not-found">${esc(p.error || "No public profile found on Wikipedia/Wikidata. Likely a private individual — research manually.")}</div>
      </div></div>`;
    }
    const facts = [
      p.born && `<span><span class="k">Born</span>${esc(p.born)}</span>`,
      p.died && `<span><span class="k">Died</span>${esc(p.died)}</span>`,
      p.net_worth && `<span><span class="k">Net worth</span>${esc(p.net_worth)}</span>`,
      p.occupations?.length && `<span><span class="k">Occupation</span>${esc(p.occupations.join(", "))}</span>`,
    ].filter(Boolean).join("");
    const f = p.family || {};
    return `<div class="person">${avatar}<div>
      <div class="p-name">${name}</div>
      ${p.role ? `<span class="p-role">${esc(p.role)}</span>` : ""}
      ${p.description ? `<div class="p-desc">${esc(p.description)}</div>` : ""}
      ${facts ? `<div class="p-facts">${facts}</div>` : ""}
      ${p.bio ? `<p class="p-bio">${esc(p.bio)}</p>${p.bio.length > 320 ? `<button class="more">Read more</button>` : ""}` : ""}
      <div class="family">
        ${familyRow("Spouse", f.spouses)}
        ${familyRow("Parents", f.parents)}
        ${familyRow("Children", f.children)}
        ${f.siblings?.length ? familyRow("Siblings", f.siblings) : ""}
      </div>
    </div></div>`;
  }

  function drawerHeader(a) {
    return `
      <div class="d-kicker">${esc(a.source)} · ${fmtDate(a.published_at)} ${fmtTime(a.published_at)}</div>
      <h2 class="d-title">${esc(a.title)}</h2>
      <div class="d-meta"><a href="${esc(a.url)}" target="_blank" rel="noopener">Read the full article ↗</a>
        ${a.author ? ` · ${esc(a.author)}` : ""}</div>
      ${a.description ? `<p class="d-desc">${esc(a.description)}</p>` : ""}
      <div class="d-section"><h3>Tags</h3><div class="tags">${a.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div></div>`;
  }

  async function openDrawer(id) {
    const a = state.articles.find((x) => x.id === id);
    if (!a) return;
    state.newIds.delete(id);
    $("#drawer-body").innerHTML = drawerHeader(a) + `<div class="d-section"><h3>Key people</h3>
      <div class="skeleton" style="width:60%"></div><div class="skeleton"></div><div class="skeleton" style="width:80%"></div></div>`;
    $("#drawer").classList.add("open");
    $("#drawer").setAttribute("aria-hidden", "false");
    $("#scrim").hidden = false;
    history.replaceState(null, "", `#story-${id}`);

    let d;
    try { d = await api(`/api/articles/${id}`); } catch (e) { d = { ...a, profiles: [] }; }
    if (!$("#drawer").classList.contains("open")) return;
    const factor = d.event_type ? `
      <div class="d-section"><h3>Wealth factor</h3>
        <div class="factor" style="--c:${d.event_color || eventColor(d.event_type)}">
          ${badge(d.event_type)} &nbsp; ${confMeter(d.event_confidence)}
          <p>${esc(d.event_rationale || "")}</p>
          ${d.classifier === "claude" && d.event_blurb ? `<p class="why">${esc(d.event_blurb)}</p>` : ""}
        </div>
      </div>` : `<div class="d-section"><h3>Wealth factor</h3><p class="no-signal">No wealth-changing event detected.</p></div>`;
    const people = d.profiles?.length
      ? d.profiles.map(personCard).join("")
      : `<p class="no-signal">No named individuals identified in this story.</p>`;
    const sources = [...new Set((d.profiles || []).filter((p) => p.found).map((p) => p.source))];
    $("#drawer-body").innerHTML = drawerHeader(d) + factor +
      `<div class="d-section"><h3>Key people</h3>${people}</div>` +
      `<div class="provenance">Classified by ${d.classifier === "claude" ? "Claude" : "keyword rules"}${sources.length ? ` · Profiles: ${esc(sources.join(", "))}` : ""}.</div>`;
    renderRows();
  }

  function closeDrawer() {
    $("#drawer").classList.remove("open");
    $("#drawer").setAttribute("aria-hidden", "true");
    $("#scrim").hidden = true;
    history.replaceState(null, "", location.pathname);
  }

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast.t);
    toast.t = setTimeout(() => (t.hidden = true), 3500);
  }

  // ---------------------------------------------------------------- events
  document.addEventListener("click", (e) => {
    const row = e.target.closest("tr.story");
    if (row) return openDrawer(Number(row.dataset.id));
    const card = e.target.closest(".sig-card");
    if (card) {
      state.event = state.event === card.dataset.event ? null : card.dataset.event;
      if (state.event) state.view = "signals";
      return renderAll();
    }
    const chip = e.target.closest(".chip[data-tag]");
    if (chip) {
      const t = chip.dataset.tag;
      state.tags.has(t) ? state.tags.delete(t) : state.tags.add(t);
      return renderAll();
    }
    const seg = e.target.closest(".seg-btn");
    if (seg) {
      state.view = seg.dataset.view;
      if (state.view === "all") state.event = null;
      try { localStorage.setItem("wsm.view", state.view); } catch {}
      return renderAll();
    }
    if (e.target.classList.contains("more")) {
      const bio = e.target.previousElementSibling;
      bio.classList.toggle("expanded");
      e.target.textContent = bio.classList.contains("expanded") ? "Show less" : "Read more";
    }
  });
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  $("#search").addEventListener("input", (e) => { state.q = e.target.value.trim().toLowerCase(); renderRows(); });
  $("#confidence").addEventListener("change", (e) => { state.minConf = e.target.value; renderAll(); });

  $("#pull-btn").addEventListener("click", async () => {
    const btn = $("#pull-btn");
    btn.disabled = true;
    btn.textContent = "Pulling…";
    try {
      const r = await api("/api/pull", { method: "POST" });
      if (r.status === "quota") toast(`Daily quota reached — ${r.message}`);
      else if (r.status === "error") toast(`Pull failed: ${r.message}`);
      else if (r.status === "busy") toast("A pull is already running");
      else toast(r.message || `${r.new} new ${r.new === 1 ? "story" : "stories"} added`);
      await refresh();
    } catch (e) {
      toast(`Pull failed: ${e.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = "Pull now";
    }
  });

  async function refresh() {
    const [n] = await Promise.all([loadArticles(false), loadStatus()]);
    if (n) {
      renderAll();
      document.title = `(${state.newIds.size}) Wealth Signal Monitor`;
    }
  }

  // ---------------------------------------------------------------- boot
  (async () => {
    state.meta = await api("/api/meta");
    await Promise.all([loadArticles(true), loadStatus()]);
    renderAll();
    const m = location.hash.match(/^#story-(\d+)/);
    if (m) openDrawer(Number(m[1]));
    setInterval(refresh, 60_000);
    setInterval(tickCountdown, 1_000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) document.title = "Wealth Signal Monitor"; });
  })();
})();
