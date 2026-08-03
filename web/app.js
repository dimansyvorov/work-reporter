const REPORT_CONFIG = window.REPORT_CONFIG || {};
const IS_PUBLISH = !!REPORT_CONFIG.publish;

function appBase() {
  if (REPORT_CONFIG.base != null && REPORT_CONFIG.base !== "") {
    return String(REPORT_CONFIG.base).replace(/\/+$/, "");
  }
  if (location.pathname === "/report" || location.pathname.startsWith("/report/")) {
    return "/report";
  }
  return "";
}

function apiUrl(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${appBase()}${p}`;
}

async function loadStatus() {
  const res = await fetch(apiUrl("/api/status"), { cache: "no-store" });
  if (!res.ok) throw new Error(`Не удалось получить статус: ${res.status}`);
  return res.json();
}

async function loadReport() {
  const candidates = IS_PUBLISH
    ? ["/api/report.json", "/api/report"]
    : ["/api/report", "/api/report.json"];
  let lastErr = null;
  for (const path of candidates) {
    try {
      const res = await fetch(apiUrl(path), { cache: "no-store" });
      if (res.status === 202) {
        throw Object.assign(new Error("pending"), { pending: true });
      }
      if (!res.ok) {
        lastErr = new Error(`Не удалось загрузить отчёт: ${res.status}`);
        continue;
      }
      return await res.json();
    } catch (err) {
      if (err?.pending) throw err;
      lastErr = err;
    }
  }
  throw lastErr || new Error("Не удалось загрузить отчёт");
}

async function requestRefresh() {
  if (IS_PUBLISH) {
    throw new Error("Обновление на сервере недоступно — публикуйте с Mac через --publish");
  }
  const res = await fetch(apiUrl("/api/refresh"), { method: "POST", cache: "no-store" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `Не удалось обновить: ${res.status}`);
  }
  return data;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function fmtNumber(value) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("ru-RU").format(value);
}

function fmtDay(value) {
  if (!value) return "—";
  const m = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[3]}.${m[2]}.${m[1]}`;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}.${d.getFullYear()}`;
}

function fmtDateTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return fmtDay(value);
  return `${fmtDay(value)} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function tip(label, detail, { as = "span", cls = "" } = {}) {
  const text = escapeHtml(label);
  const tipText = escapeHtml(detail || "");
  if (!detail) return cls ? `<${as} class="${cls}">${text}</${as}>` : text;
  const extra = cls ? ` ${cls}` : "";
  return `<${as} class="has-tip${extra}" tabindex="0" data-tip="${tipText}">${text}</${as}>`;
}

function ensureFloatTip() {
  let el = document.getElementById("float-tip");
  if (!el) {
    el = document.createElement("div");
    el.id = "float-tip";
    el.className = "float-tip hidden";
    document.body.appendChild(el);
  }
  return el;
}

function showFloatTip(anchor, text) {
  if (!text) return;
  const tipEl = ensureFloatTip();
  tipEl.textContent = text;
  tipEl.classList.remove("hidden");
  const pad = 8;
  const r = anchor.getBoundingClientRect();
  // Measure after visible
  const tw = tipEl.offsetWidth;
  const th = tipEl.offsetHeight;
  let left = r.left + r.width / 2 - tw / 2;
  let top = r.top - th - pad;
  if (top < pad) top = r.bottom + pad;
  if (left < pad) left = pad;
  if (left + tw > window.innerWidth - pad) left = window.innerWidth - pad - tw;
  if (top + th > window.innerHeight - pad) top = Math.max(pad, window.innerHeight - pad - th);
  tipEl.style.left = `${Math.round(left)}px`;
  tipEl.style.top = `${Math.round(top)}px`;
}

function hideFloatTip() {
  const tipEl = document.getElementById("float-tip");
  if (tipEl) tipEl.classList.add("hidden");
}

function syncCollapseLabel(btn, open) {
  const label = btn.querySelector("[data-collapse-label]");
  if (label) label.textContent = open ? "Свернуть" : "Развернуть";
  btn.setAttribute("aria-expanded", open ? "true" : "false");
}

function bindCollapseToggles(root = document) {
  root.querySelectorAll("[data-collapse-toggle]").forEach((btn) => {
    if (btn.dataset.boundCollapse === "1") return;
    btn.dataset.boundCollapse = "1";
    const panel = btn.closest(".collapsible-panel");
    syncCollapseLabel(btn, !!panel?.classList.contains("is-open"));
    btn.addEventListener("click", () => {
      const panelEl = btn.closest(".collapsible-panel");
      if (!panelEl) return;
      const open = panelEl.classList.toggle("is-open");
      syncCollapseLabel(btn, open);
    });
  });
}

function weekdayShort(value) {
  const m = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return "";
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  if (Number.isNaN(d.getTime())) return "";
  return ["вс", "пн", "вт", "ср", "чт", "пт", "сб"][d.getDay()];
}

function sortByDirectionOrder(items, order, nameKey = "name") {
  const rank = new Map((order || []).map((name, idx) => [name, idx]));
  return [...(items || [])].sort((a, b) => {
    const ia = rank.has(a[nameKey]) ? rank.get(a[nameKey]) : 999;
    const ib = rank.has(b[nameKey]) ? rank.get(b[nameKey]) : 999;
    if (ia !== ib) return ia - ib;
    return String(a[nameKey] || "").localeCompare(String(b[nameKey] || ""), "ru");
  });
}

function shortName(name) {
  if (!name) return "—";
  const parts = String(name).trim().split(/\s+/);
  if (parts.length === 1) return parts[0];
  if (parts.length === 2) return `${parts[0]} ${parts[1].charAt(0)}.`;
  return `${parts[0]} ${parts[1].charAt(0)}. ${parts[2].charAt(0)}.`;
}

let currentSprintReport = null;
let currentReportMeta = null;
let freshnessTimer = null;

function fmtRelativeAgo(iso) {
  if (!iso) return "—";
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return "—";
  const sec = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (sec < 45) return "только что";
  const min = Math.round(sec / 60);
  if (min < 60) {
    const n = Math.max(1, min);
    const mod10 = n % 10;
    const mod100 = n % 100;
    const unit =
      mod10 === 1 && mod100 !== 11
        ? "минуту"
        : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)
          ? "минуты"
          : "минут";
    return `${n} ${unit} назад`;
  }
  const hours = Math.round(min / 60);
  if (hours < 48) {
    const n = Math.max(1, hours);
    const mod10 = n % 10;
    const mod100 = n % 100;
    const unit =
      mod10 === 1 && mod100 !== 11
        ? "час"
        : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)
          ? "часа"
          : "часов";
    return `${n} ${unit} назад`;
  }
  const days = Math.round(hours / 24);
  const mod10 = days % 10;
  const mod100 = days % 100;
  const unit =
    mod10 === 1 && mod100 !== 11
      ? "день"
      : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)
        ? "дня"
        : "дней";
  return `${days} ${unit} назад`;
}

function updateDataFreshness(fetchedAt = currentReportMeta?.fetched_at) {
  const root = document.getElementById("data-freshness");
  const value = document.getElementById("data-freshness-value");
  if (!root || !value) return;
  if (!fetchedAt) {
    value.textContent = "—";
    root.classList.remove("is-stale", "is-old");
    root.title = "Время последнего успешного сбора данных";
    return;
  }
  const ts = Date.parse(fetchedAt);
  const ageMin = Number.isFinite(ts) ? (Date.now() - ts) / 60000 : 0;
  value.textContent = fmtRelativeAgo(fetchedAt);
  root.classList.toggle("is-stale", ageMin >= 30 && ageMin < 180);
  root.classList.toggle("is-old", ageMin >= 180);
  root.title = `Собрано: ${fmtDateTime(fetchedAt)}`;
}

function startFreshnessClock() {
  if (freshnessTimer) clearInterval(freshnessTimer);
  updateDataFreshness();
  freshnessTimer = setInterval(() => updateDataFreshness(), 30000);
}

function personCell(name, avatarUrl, { short = false, clickable = true, personKey = null } = {}) {
  const label = short ? shortName(name) : name;
  const initial = (name || "?").trim().charAt(0).toUpperCase();
  const avatar = avatarUrl
    ? `<img class="avatar" src="${escapeHtml(avatarUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" />`
    : `<span class="avatar placeholder">${escapeHtml(initial)}</span>`;
  const inner = `${avatar}<span>${escapeHtml(label)}</span>`;
  const key = personKey || name;
  if (!clickable || !key || key === "—" || name === "Без исполнителя") {
    return `<div class="person" title="${escapeHtml(name || "")}">${inner}</div>`;
  }
  return `<button type="button" class="person person-btn" data-person="${escapeHtml(key)}" title="${escapeHtml(name)}">${inner}</button>`;
}

function hoursCell(hours, level) {
  const cls =
    level === "bad" ? "hours bad" : level === "warn" ? "hours warn" : level === "ok" ? "hours ok" : "hours";
  return `<span class="${cls}">${fmtNumber(hours)}</span>`;
}

function statusClass(category, statusName) {
  const cat = (category || "").toLowerCase();
  const name = (statusName || "").toLowerCase();
  if (cat === "done" || name.includes("готов") || name.includes("закрыт") || name === "done") {
    return "status-done";
  }
  if (
    cat === "indeterminate" ||
    name.includes("работ") ||
    name.includes("ревью") ||
    name.includes("тест") ||
    name.includes("progress")
  ) {
    return "status-progress";
  }
  if (cat === "new" || name.includes("выполн") || name.includes("to do") || name.includes("открыт")) {
    return "status-todo";
  }
  return "status-unknown";
}

function statusChip(status, category) {
  if (!status) return `<span class="muted">—</span>`;
  return `<span class="status-chip ${statusClass(category, status)}" title="${escapeHtml(status)}">${escapeHtml(status)}</span>`;
}

function issueLink(issue) {
  if (!issue?.key) return "—";
  const label = escapeHtml(issue.key);
  const epicCls = issue.is_epic ? " issue-key-epic" : "";
  return issue.web_url
    ? `<a class="issue-key${epicCls}" href="${escapeHtml(issue.web_url)}" target="_blank" rel="noreferrer">${label}</a>`
    : `<span class="issue-key${epicCls}">${label}</span>`;
}

function tagsHtml(tags) {
  if (!tags?.length) return "";
  return tags
    .map((t) => {
      const hint = t.hint ? escapeHtml(t.hint) : "";
      const tipCls = hint ? " has-tip" : "";
      const tipAttr = hint ? ` tabindex="0" data-tip="${hint}"` : "";
      return `<span class="task-tag${tipCls} tag-${escapeHtml(t.tone || "muted")}"${tipAttr}>${escapeHtml(t.label)}</span>`;
    })
    .join("");
}

function fmtDuration(hours) {
  if (hours == null || Number.isNaN(Number(hours))) return "—";
  const totalMin = Math.round(Number(hours) * 60);
  if (totalMin <= 0) return "0ч";
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h > 0 && m > 0) return `${h}ч ${m}м`;
  if (h > 0) return `${h}ч`;
  return `${m}м`;
}

function timeCell(spent, estimate, { warnZero = false } = {}) {
  const spentN = Number(spent) || 0;
  const spentText = fmtDuration(spentN);
  const estText =
    estimate == null || Number.isNaN(Number(estimate))
      ? null
      : fmtDuration(estimate);
  const label = estText ? `${spentText} / ${estText}` : spentText;
  const zero = warnZero && spentN <= 0;
  const cls = zero ? "metric metric-danger time-zero" : "metric metric-info";
  return `<span class="${cls}">${escapeHtml(label)}</span>`;
}

function taskTitle(issue, { showTags = true } = {}) {
  if (!issue?.key && !issue?.summary) return "—";
  const summary = issue.summary
    ? `<span class="task-summary">${escapeHtml(issue.summary)}</span>`
    : "";
  const tags = showTags ? tagsHtml(issue.tags) : "";
  const tagsRow = tags ? `<div class="task-tags">${tags}</div>` : "";
  return `<div class="task-block"><div class="task-inline">${issueLink(issue)}${summary}</div>${tagsRow}</div>`;
}

function medalHtml(place) {
  const p = Number(place) || 0;
  if (p < 1 || p > 3) return `<span class="medal">${p || "—"}</span>`;
  return `<span class="medal medal-${p}" title="${p} место">${p}</span>`;
}

const DIRECTION_ICONS = {
  "Мобильная разработка": {
    color: "#1f7a4d",
    bg: "rgba(31,122,77,0.14)",
    svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="8" y="3" width="8" height="18" rx="2"/><path d="M11 18h2"/></svg>`,
  },
  "Веб-разработка": {
    color: "#1f5f8b",
    bg: "rgba(31,95,139,0.14)",
    svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.8 3.8 5.8 3.8 9s-1.3 6.2-3.8 9c-2.5-2.8-3.8-5.8-3.8-9s1.3-6.2 3.8-9z"/></svg>`,
  },
  Бэкенд: {
    color: "#6b4f9a",
    bg: "rgba(107,79,154,0.14)",
    svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/></svg>`,
  },
  Тестирование: {
    color: "#a85a12",
    bg: "rgba(168,90,18,0.14)",
    svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 3h6M10 3v5l-5.5 9.5A2 2 0 0 0 6.2 21h11.6a2 2 0 0 0 1.7-3.5L14 8V3"/><path d="M8.5 14h7"/></svg>`,
  },
  Дизайн: {
    color: "#b4236a",
    bg: "rgba(180,35,106,0.12)",
    svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/></svg>`,
  },
  Девопс: {
    color: "#0f766e",
    bg: "rgba(15,118,110,0.14)",
    svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 12a6 6 0 0 1 10.4-4M20 12a6 6 0 0 1-10.4 4"/><circle cx="8" cy="12" r="2"/><circle cx="16" cy="12" r="2"/></svg>`,
  },
  Аналитика: {
    color: "#0f4c81",
    bg: "rgba(15,76,129,0.12)",
    svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 19h16M7 16V9M12 16V5M17 16v-4"/></svg>`,
  },
};

function directionIcon(name) {
  const icon = DIRECTION_ICONS[name] || {
    color: "var(--accent)",
    bg: "var(--accent-soft)",
    svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="8"/></svg>`,
  };
  return `<span class="dir-icon" style="color:${icon.color};background:${icon.bg}">${icon.svg}</span>`;
}

function setRefreshEnabled(enabled) {
  const btn = document.getElementById("refresh-btn");
  if (!btn) return;
  if (IS_PUBLISH) {
    btn.classList.add("hidden");
    btn.disabled = true;
    return;
  }
  btn.classList.remove("hidden");
  btn.disabled = !enabled;
}

function showReport(show) {
  document.getElementById("report-root").classList.toggle("hidden", !show);
}

function renderLoading(status) {
  const loading = document.getElementById("loading");
  loading.classList.remove("hidden");
  document.getElementById("loading-title").textContent =
    status.status === "error" ? "Не удалось собрать отчёт" : "Готовим отчёт";

  const stale = Number(status.stale_seconds || 0);
  const staleHint =
    status.status === "collecting" && stale >= 45
      ? ` · нет обновлений ${Math.round(stale)} сек — проверьте VPN/терминал`
      : "";
  const message = `${status.message || "Загрузка…"}${staleHint}`;
  document.getElementById("loading-message").textContent = message;
  document.getElementById("meta").textContent = message;

  const steps = status.steps || [];
  const done = steps.filter((s) => s.state === "done" || s.state === "skipped").length;
  const total = Math.max(steps.length, 1);
  const running = steps.some((s) => s.state === "running");
  const pct =
    status.status === "ready"
      ? 100
      : status.status === "error"
        ? Math.round((done / total) * 100)
        : Math.min(95, Math.round(((done + (running ? 0.45 : 0)) / total) * 100));

  document.getElementById("loading-pct").textContent = `${pct}%`;
  document.getElementById("loading-count").textContent = `${done}/${steps.length || "…"} шагов`;
  document.getElementById("loading-bar-fill").style.width = `${pct}%`;

  document.getElementById("loading-steps").innerHTML = steps
    .map((step) => {
      const mark =
        step.state === "done"
          ? "✓"
          : step.state === "running"
            ? "…"
            : step.state === "error"
              ? "!"
              : step.state === "skipped"
                ? "–"
                : "○";
      const detail =
        step.state === "running"
          ? `<span class="step-detail">сейчас</span>`
          : step.state === "done"
            ? `<span class="step-detail">готово</span>`
            : step.state === "error"
              ? `<span class="step-detail">ошибка</span>`
              : step.state === "skipped"
                ? `<span class="step-detail">пропуск</span>`
                : `<span class="step-detail">ожидание</span>`;
      return `<li class="step step-${escapeHtml(step.state)}"><span class="step-mark">${mark}</span><span>${escapeHtml(step.label)}</span>${detail}</li>`;
    })
    .join("");

  const spinner = loading.querySelector(".spinner");
  if (spinner) {
    spinner.classList.toggle("hidden", status.status === "error" || status.status === "ready");
  }

  loading.classList.toggle("is-stale", status.status === "collecting" && stale >= 45);
}

function hideLoading() {
  document.getElementById("loading").classList.add("hidden");
}

let TASK_TABLE_PREVIEW = 5;

function applyTableCollapse(table) {
  if (!table?.classList.contains("js-collapsible")) return;
  const tbody = table.querySelector("tbody");
  const toggle = table.parentElement?.querySelector(".table-collapse-toggle");
  if (!tbody || !toggle) return;

  // Real task rows carry data-key; empty-state placeholders do not
  const dataRows = Array.from(tbody.querySelectorAll("tr")).filter((row) => row.dataset.key);
  const limit = Number(table.dataset.collapseLimit || TASK_TABLE_PREVIEW);
  const expanded = table.dataset.collapsed === "0";
  const total = dataRows.length;

  dataRows.forEach((row, idx) => {
    row.classList.toggle("row-collapsed", !expanded && idx >= limit);
  });

  if (total <= limit) {
    toggle.classList.add("hidden");
    return;
  }
  toggle.classList.remove("hidden");
  const hidden = Math.max(total - limit, 0);
  toggle.textContent = expanded
    ? "▴ Свернуть список"
    : `▾ Показать ещё ${hidden} строк`;
  toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
}

function bindCollapsibleTables(root = document) {
  root.querySelectorAll("table.js-collapsible").forEach((table) => {
    if (table.dataset.collapseBound === "1") return;
    table.dataset.collapseBound = "1";
    table.dataset.collapsed = table.dataset.collapsed || "1";

    let toggle = table.parentElement?.querySelector(".table-collapse-toggle");
    if (!toggle) {
      toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "table-collapse-toggle";
      table.parentElement?.appendChild(toggle);
    }
    toggle.addEventListener("click", () => {
      table.dataset.collapsed = table.dataset.collapsed === "0" ? "1" : "0";
      applyTableCollapse(table);
    });
    applyTableCollapse(table);
  });
}

function bindSortableTables(root = document) {
  root.querySelectorAll("table.js-sortable").forEach((table) => {
    if (table.dataset.sortBound === "1") return;
    table.dataset.sortBound = "1";
    const head = table.querySelector("thead");
    if (!head) return;
    head.addEventListener("click", (event) => {
      const th = event.target.closest("th.sortable");
      if (!th || !table.contains(th)) return;
      const key = th.dataset.sort;
      const type = th.dataset.type || "string";
      if (!key) return;

      const current = table.dataset.sortKey;
      const currentDir = table.dataset.sortDir || "desc";
      const nextDir = current === key && currentDir === "desc" ? "asc" : "desc";
      table.dataset.sortKey = key;
      table.dataset.sortDir = nextDir;

      head.querySelectorAll("th.sortable").forEach((el) => el.classList.remove("asc", "desc"));
      th.classList.add(nextDir);

      const tbody = table.querySelector("tbody");
      if (!tbody) return;
      const rows = Array.from(tbody.querySelectorAll("tr"));
      rows.sort((a, b) => {
        const av = a.dataset[key] ?? "";
        const bv = b.dataset[key] ?? "";
        let cmp = 0;
        if (type === "number") {
          cmp = (Number(av) || 0) - (Number(bv) || 0);
        } else {
          cmp = String(av).localeCompare(String(bv), "ru", { sensitivity: "base" });
        }
        return nextDir === "asc" ? cmp : -cmp;
      });
      rows.forEach((row) => tbody.appendChild(row));
      applyTableCollapse(table);
    });
  });
}

async function waitForReport() {
  if (IS_PUBLISH) {
    setRefreshEnabled(false);
    renderLoading({
      status: "collecting",
      message: "Загружаю опубликованный отчёт…",
      steps: [{ key: "load", label: "Чтение report.json", state: "running" }],
    });
    const report = await loadReport();
    return report;
  }
  while (true) {
    const status = await loadStatus();
    renderLoading(status);
    setRefreshEnabled(!(status.collecting || status.status === "collecting" || status.status === "starting"));

    if (status.status === "error") {
      const err = document.getElementById("error");
      err.classList.remove("hidden");
      err.textContent = status.error || "Неизвестная ошибка";
      setRefreshEnabled(true);
      throw new Error(status.error || "Ошибка сбора");
    }
    if (status.ready || status.status === "ready") return loadReport();
    await sleep(450);
  }
}

function openTeamMoodModal(mood) {
  if (!mood) return;
  const comps = mood.components || {};
  const compLabels = {
    schedule: "План vs факт",
    completion: "Закрытие задач",
    task_risks: "Риски задач",
    releases: "Релизы",
    epics_in_sprint: "Эпики (спринт)",
  };
  const componentsHtml = Object.entries(compLabels)
    .map(
      ([key, label]) => `
      <div class="mood-component">
        <span class="label">${escapeHtml(label)}</span>
        <strong>${fmtNumber(comps[key])}%</strong>
      </div>`
    )
    .join("");
  const drivers = mood.drivers || [];
  const driversHtml = drivers.length
    ? drivers
        .map(
          (d) => `
        <div class="mood-driver severity-${escapeHtml(d.severity || "warn")}">
          <p class="mood-driver-title">${escapeHtml(d.title || "Фактор")}</p>
          <p class="mood-driver-summary">${escapeHtml(d.summary || "")}</p>
          <p class="muted" style="margin:0.35rem 0 0">вклад −${fmtNumber(d.impact)} п.п.</p>
        </div>`
        )
        .join("")
    : `<p class="muted">Явных негативных факторов нет — настроение опирается на выполнение плана и отсутствие рисков.</p>`;
  const ctx = mood.context || {};
  openAppModal(
    `
    <div class="release-modal-head">
      <h2 id="app-modal-title">${escapeHtml(mood.emoji || "")} Настроение команды · ${fmtNumber(mood.score)}%</h2>
      <p class="muted">${escapeHtml(mood.recommendation || "")}</p>
    </div>
    <p class="muted" style="margin:0.75rem 0 0">
      Спринт: закрыто ${fmtNumber(ctx.tasks_done)}/${fmtNumber(ctx.tasks_total)} ·
      время ${fmtNumber(ctx.time_progress_pct)}% ·
      активных релизов ${fmtNumber(ctx.active_releases)} ·
      эпиков в скоупе ${fmtNumber(ctx.epics_in_scope)}
    </p>
    <div class="mood-components">${componentsHtml}</div>
    <div class="person-modal-section">
      <h3>Что повлияло на расчёт</h3>
      <div class="mood-modal-drivers">${driversHtml}</div>
    </div>
  `,
    { wide: true }
  );
}

function sprintTitleName(sprint) {
  const raw = String(sprint?.name || "").trim();
  if (!raw) return "Спринт";
  const short = raw.split("|")[0].trim();
  return short || raw;
}

function updateDocumentTitle(sprint) {
  const name = sprintTitleName(sprint);
  const pct = sprint?.time_progress_pct;
  document.title =
    pct == null || Number.isNaN(Number(pct))
      ? `Отчет ${name}`
      : `Отчет ${name} (${fmtNumber(pct)}%)`;
}

function renderSprintHeader(sprint, mood) {
  const root = document.getElementById("sprint-header");
  document.getElementById("sprint-title").textContent = sprint.name || "Спринт";
  updateDocumentTitle(sprint);

  const endsHint =
    sprint.days_left == null
      ? "дата окончания неизвестна"
      : sprint.days_left === 0
        ? "заканчивается сегодня"
        : `осталось ${fmtNumber(sprint.days_left)} дн.`;

  const tasksPct = sprint.tasks_progress_pct;
  const timePct = sprint.time_progress_pct;
  const tasksTone =
    tasksPct == null ? "" : tasksPct >= 70 ? "metric-ok" : tasksPct >= 40 ? "metric-info" : "metric-warn";
  const timeTone =
    timePct == null ? "" : timePct >= 80 ? "metric-warn" : timePct >= 50 ? "metric-info" : "metric-ok";

  const cards = [
    {
      label: "Окончание спринта",
      value: fmtDay(sprint.end_date),
      hint: `${fmtDay(sprint.start_date)} — ${fmtDay(sprint.end_date)} · ${endsHint}`,
      tone: "",
    },
    {
      label: "Прохождение спринта",
      value: timePct == null ? "—" : `${fmtNumber(timePct)}%`,
      hint:
        sprint.day_index != null && sprint.day_total != null
          ? `день ${sprint.day_index} из ${sprint.day_total} · календарные дни`
          : "календарные дни",
      tone: timeTone,
    },
    {
      label: "Выполнение задач",
      value: tasksPct == null ? "—" : `${fmtNumber(tasksPct)}%`,
      hint: `${fmtNumber(sprint.done)} / ${fmtNumber(sprint.total)} закрыто`,
      tone: tasksTone,
    },
    {
      label: "Статус",
      value: sprint.state_label || sprint.state || "—",
      hint: sprint.goal ? escapeHtml(sprint.goal) : "без цели",
      tone: sprint.state === "active" ? "metric-ok" : "",
    },
  ]
    .map(
      (item) => `
      <article class="card ${item.tone}">
        <p class="label">${item.label}</p>
        <p class="value">${item.value}</p>
        <p class="hint">${item.hint}</p>
      </article>`
    )
    .join("");

  const moodCard = mood
    ? `
    <article class="card mood-card tone-${escapeHtml(mood.tone || "ok")}" data-team-mood="1" role="button" tabindex="0" title="Открыть факторы расчёта">
      <p class="label">Настроение команды</p>
      <div class="mood-emoji" aria-hidden="true">${escapeHtml(mood.emoji || "🙂")}</div>
      <p class="mood-score">${fmtNumber(mood.score)}%</p>
      <p class="mood-reco">${escapeHtml(mood.recommendation || "—")}</p>
    </article>`
    : "";

  root.innerHTML = cards + moodCard;
}

function shortDirection(name) {
  const map = currentSprintReport?.settings?.direction_shorts || {};
  return map[name] || name;
}

function renderEpicTimeline(timeline) {
  const section = document.getElementById("epic-timeline-section");
  const root = document.getElementById("epic-timeline");
  const legend = document.getElementById("epic-timeline-legend");
  const note = document.getElementById("epic-timeline-note");
  const mini = document.getElementById("epics-mini");
  const epics = timeline?.epics || [];
  if (!epics.length) {
    section.classList.add("hidden");
    if (mini) mini.innerHTML = "";
    return;
  }
  section.classList.remove("hidden");
  section.classList.remove("is-open");
  const toggle = section.querySelector("[data-collapse-toggle]");
  if (toggle) syncCollapseLabel(toggle, false);

  if (mini) {
    mini.innerHTML = epics
      .slice(0, 6)
      .map(
        (e) =>
          `<span class="mini-chip"><strong>${escapeHtml(e.key)}</strong> · ${fmtNumber(e.progress_pct)}%</span>`
      )
      .join("");
    if (epics.length > 6) {
      mini.innerHTML += `<span class="mini-chip">+${epics.length - 6}</span>`;
    }
  }

  legend.innerHTML = (timeline.legend || [])
    .map(
      (item) => `
      <span class="epic-legend-item">
        <span class="epic-legend-swatch" style="background:${escapeHtml(item.color)};box-shadow:inset 0 0 0 1px ${escapeHtml(item.color)}"></span>
        ${escapeHtml(item.name)}
      </span>`
    )
    .join("");

  const omitted = Number(timeline?.omitted_count) || 0;
  if (note) {
    const focus = timeline?.focus || "active_sprint";
    if (focus === "release_window") {
      note.innerHTML = tip(
        "Эпики релизов отчёта · прогресс по полному объёму эпика",
        "Показаны только эпики, связанные с версиями в окне релизов (спринт + 2 недели). Прогресс секций считается по всем задачам эпика, не только по спринту."
      );
    } else if (focus === "release_window_sprint") {
      note.innerHTML = tip(
        "Эпики релизов · по задачам текущего спринта",
        "Полный объём эпиков временно недоступен — показываем эпики релизов по задачам спринта команды."
      );
    } else {
      note.innerHTML = omitted
        ? tip(
            `Фокус спринта: эпики с активной работой. Скрыто: ${omitted}.`,
            "Пока нет релизов в окне отчёта — показываем эпики с active-задачами спринта."
          )
        : tip(
            "Фокус спринта: эпики с активной работой",
            "Пока нет релизов в окне отчёта — показываем эпики с active-задачами спринта."
          );
    }
  }

  function epicCardHtml(epic) {
    const sections = (epic.sections || [])
      .map((s) => {
        const fill = Math.min(Number(s.closed_pct ?? s.progress_pct) || 0, 100);
        const grow = Math.max(Number(s.flex_grow) || Number(s.estimate_hours) || 1, 1);
        const tipText = `${s.direction}: ${fmtNumber(s.done_tasks)}/${fmtNumber(s.tasks)} закрыто · открытых ${fmtNumber(s.open_tasks ?? s.active_tasks)} · оценка ${fmtDuration(s.estimate_hours)}`;
        return `
          <div class="epic-section" title="${escapeHtml(tipText)}" style="flex:${grow} 1 4.8rem;--section-color:${escapeHtml(s.color)}">
            <div class="epic-section-fill" style="width:${fill}%"></div>
          </div>`;
      })
      .join("");
    const captions = (epic.sections || [])
      .map((s) => {
        const closed = Number(s.closed_pct ?? s.progress_pct) || 0;
        const grow = Math.max(Number(s.flex_grow) || Number(s.estimate_hours) || 1, 1);
        const short = shortDirection(s.direction);
        const tipText = `${s.direction}: закрыто ${fmtNumber(closed)}% (${fmtNumber(s.done_tasks)}/${fmtNumber(s.tasks)}), открытых ${fmtNumber(s.open_tasks ?? s.active_tasks)}`;
        return `
          <div class="epic-caption" style="flex:${grow} 1 4.8rem;--section-color:${escapeHtml(s.color)}">
            <span class="epic-caption-dot"></span>
            <span class="epic-caption-dir">${tip(short, tipText)}</span>
            <span class="epic-caption-pct">${tip(`${fmtNumber(closed)}%`, tipText)}</span>
          </div>`;
      })
      .join("");
    const scopeHint =
      epic.scope === "full_epic"
        ? tip(
            `${fmtDuration(epic.estimate_hours)} · ${fmtNumber(epic.progress_pct)}% закрыто`,
            "Оценка и % — по полному объёму эпика (все задачи эпика), не только спринт."
          )
        : `${fmtDuration(epic.estimate_hours)} · ${fmtNumber(epic.progress_pct)}% закрыто`;
    const conflictN = (epic.conflicts || []).length;
    const releaseChips = (epic.releases || [])
      .map((r) => (r.name || "").trim())
      .filter(Boolean)
      .map((name) => `<span class="mini-chip">${escapeHtml(name)}</span>`)
      .join("");
    const epicTitle = epic.summary
      ? `<div class="task-block"><div class="task-inline">${issueLink(epic)}<span class="task-summary epic-title-text">${escapeHtml(epic.summary)}</span></div></div>`
      : taskTitle(epic, { showTags: false });
    return `
      <article class="epic-row" data-epic-key="${escapeHtml(epic.key)}" role="button" tabindex="0">
        <div class="epic-row-meta">
          ${epicTitle}
          <div class="muted">${scopeHint}</div>
          <div class="epic-row-chips">
            ${
              Number.isFinite(Number(epic.age_days))
                ? `<span class="mini-chip">тянется ${fmtNumber(epic.age_days)} дн.</span>`
                : ""
            }
            ${releaseChips || `<span class="mini-chip muted-chip">без релиза</span>`}
            ${
              conflictN
                ? `<span class="mini-chip is-warn">риски ${fmtNumber(conflictN)}</span>`
                : ""
            }
          </div>
        </div>
        <div class="epic-track-wrap" style="width:${epic.bar_width_pct || 100}%">
          <div class="epic-sausage">${sections}</div>
          <div class="epic-captions">${captions}</div>
        </div>
      </article>`;
  }

  const withRelease = epics.filter((e) => e.has_release || (e.releases || []).length);
  const withoutRelease = epics.filter((e) => !(e.has_release || (e.releases || []).length));
  const groups = [
    { title: "С релизом", items: withRelease },
    { title: "Без релиза", items: withoutRelease },
  ].filter((g) => g.items.length);

  root.innerHTML = groups
    .map(
      (g) => `
      <div class="epic-group">
        <h3 class="epic-group-title">${escapeHtml(g.title)} · ${fmtNumber(g.items.length)}</h3>
        <p class="muted epic-group-note">Внутри группы — по % завершения (сначала меньший прогресс)</p>
        <div class="epic-rows">${g.items.map(epicCardHtml).join("")}</div>
      </div>`
    )
    .join("");
}

function resolveEpicForModal(epicKey) {
  const key = String(epicKey || "");
  if (!key) return null;
  const timeline = (currentSprintReport?.epic_timeline?.epics || []).find(
    (e) => String(e.key) === key
  );
  if (timeline) return timeline;

  const sr = currentSprintReport;
  if (!sr) return null;

  const dirRows = [];
  const tasksByDir = new Map();
  const seenTaskKeys = new Set();

  for (const d of sr.directions || []) {
    const dirName = d.name || "—";
    const row = (d.epics || []).find((e) => String(e.key) === key);
    if (row) dirRows.push({ ...row, direction: dirName });

    const pools = [
      ...(d.remaining_tasks || []),
      ...(d.tasks || []),
      ...(d.top_tasks_by_commits || []),
    ];
    for (const t of pools) {
      if (String(t.epic_key || "") !== key) continue;
      const tKey = String(t.key || "");
      if (!tKey || seenTaskKeys.has(`${dirName}:${tKey}`)) continue;
      seenTaskKeys.add(`${dirName}:${tKey}`);
      if (!tasksByDir.has(dirName)) tasksByDir.set(dirName, []);
      tasksByDir.get(dirName).push({
        ...t,
        direction: dirName,
        direction_state: t.direction_state || (t.done || t.jira_done ? "done" : "active"),
      });
    }
  }

  // People profiles may hold tasks missing from direction lists
  for (const profile of Object.values(sr.people || {})) {
    const dirName = profile.direction || "—";
    for (const t of [...(profile.tasks_active || []), ...(profile.tasks || [])]) {
      if (String(t.epic_key || "") !== key) continue;
      const tKey = String(t.key || "");
      if (!tKey || seenTaskKeys.has(`${dirName}:${tKey}`)) continue;
      seenTaskKeys.add(`${dirName}:${tKey}`);
      if (!tasksByDir.has(dirName)) tasksByDir.set(dirName, []);
      tasksByDir.get(dirName).push({
        ...t,
        direction: dirName,
        direction_state: t.direction_state || (t.done || t.jira_done ? "done" : "active"),
      });
    }
  }

  if (!dirRows.length && !tasksByDir.size) return null;

  const base = dirRows[0] || {
    key,
    summary: key,
    web_url: null,
    is_epic: true,
  };

  const dirNames = [
    ...new Set([
      ...dirRows.map((r) => r.direction),
      ...tasksByDir.keys(),
      ...(sr.direction_order || []),
    ]),
  ].filter((name) => dirRows.some((r) => r.direction === name) || tasksByDir.has(name));

  const sections = dirNames.map((direction) => {
    const row = dirRows.find((r) => r.direction === direction) || {};
    const detail = tasksByDir.get(direction) || [];
    const openDetail = detail.filter((t) => t.direction_state !== "done");
    return {
      direction,
      color: row.color,
      progress_pct: row.progress_pct ?? 0,
      closed_pct: row.progress_pct ?? 0,
      tasks: row.total ?? detail.length,
      done_tasks: row.done ?? detail.filter((t) => t.direction_state === "done").length,
      active_tasks: row.open ?? openDetail.length,
      open_tasks: openDetail.length,
      estimate_hours: row.estimate_hours,
      spent_hours: row.hours,
      tasks_detail: openDetail,
    };
  });

  const tasksTotal = dirRows.reduce((s, r) => s + (Number(r.total) || 0), 0);
  const tasksDone = dirRows.reduce((s, r) => s + (Number(r.done) || 0), 0);
  const tasksOpen = dirRows.reduce((s, r) => s + (Number(r.open) || 0), 0);
  const estimate = dirRows.reduce((s, r) => s + (Number(r.estimate_hours) || 0), 0);
  const spent = dirRows.reduce((s, r) => s + (Number(r.hours) || 0), 0);
  const progress =
    tasksTotal > 0
      ? Math.round((tasksDone / tasksTotal) * 100)
      : Number(base.progress_pct) || 0;

  const linkedReleases = (sr.releases || []).filter((r) => {
    const fromTasks = (r.tasks || []).some((t) => String(t.epic_key || "") === key);
    const fromSections = (r.sections || []).some((s) =>
      (s.tasks_detail || []).some((t) => String(t.epic_key || "") === key)
    );
    return fromTasks || fromSections;
  });

  return {
    key,
    summary: base.summary || key,
    web_url: base.web_url,
    is_epic: true,
    scope: "sprint",
    progress_pct: progress,
    tasks_total: tasksTotal || sections.reduce((s, x) => s + (x.tasks || 0), 0),
    tasks_done: tasksDone,
    tasks_active: tasksOpen,
    tasks_open: tasksOpen || sections.reduce((s, x) => s + (x.open_tasks || 0), 0),
    tasks_risk: sections
      .flatMap((s) => s.tasks_detail || [])
      .filter((t) => t.risk).length,
    estimate_hours: estimate || base.estimate_hours,
    spent_hours: spent || base.hours,
    sections,
    releases: linkedReleases.map((r) => ({
      id: r.id,
      name: r.name,
      release_date: r.release_date,
      released: r.released,
      risk: r.risk,
      risk_label: r.risk_label,
      progress_pct: r.progress_pct,
      days_left: r.days_left,
      tasks_active: r.tasks_active,
    })),
    conflicts: [],
    has_release: linkedReleases.length > 0,
  };
}

function openEpicModal(epicKey) {
  const epic = resolveEpicForModal(epicKey);
  if (!epic) return;

  const releases = (epic.releases || [])
    .map((r) => {
      const clickable = r.id != null && r.id !== "";
      return `
      <div class="epic-release-row ${clickable ? "is-clickable" : ""}"${
        clickable
          ? ` data-release-id="${escapeHtml(r.id)}" role="button" tabindex="0"`
          : ""
      }>
        <div>
          <strong>${escapeHtml(r.name || "—")}</strong>
          <div class="muted">${fmtDay(r.release_date)} · ${r.released ? "выпущен" : "не выпущен"}</div>
        </div>
        <span class="release-status risk-${escapeHtml(r.risk || "")}">${escapeHtml(r.risk_label || "—")}</span>
      </div>`;
    })
    .join("");

  const conflicts = (epic.conflicts || [])
    .map(
      (c) => `
      <div class="release-modal-risk severity-${escapeHtml(c.severity || "warn")}">
        <div class="release-modal-risk-title">${escapeHtml(c.title || "Риск")}</div>
        <p class="release-modal-risk-summary">${escapeHtml(c.summary || "")}</p>
      </div>`
    )
    .join("");

  const dirBlocks = (epic.sections || [])
    .map((s) => {
      const openTasks = (s.tasks_detail || []).filter((t) => t.direction_state !== "done");
      if (!openTasks.length) return "";
      const tasks = openTasks
        .map(
          (t) => `
          <div class="person-task-row ${t.risk ? "task-row-risk" : ""} ${t.in_sprint === false ? "task-row-out-of-sprint" : ""}">
            <div>${taskTitle(t, { showTags: true })}</div>
            <div class="muted">${escapeHtml(shortName(t.assignee || "—"))}</div>
          </div>`
        )
        .join("");
      return `
        <div class="person-modal-section">
          <h3>${escapeHtml(s.direction)} · ${fmtNumber(s.progress_pct)}% · открыто ${fmtNumber(s.open_tasks ?? openTasks.length)}</h3>
          <div class="progress" style="margin:0.35rem 0 0.55rem"><div class="progress-bar" style="width:${Math.min(s.progress_pct || 0, 100)}%"></div></div>
          <div class="person-task-list">${tasks}</div>
        </div>`;
    })
    .filter(Boolean)
    .join("");

  const ageLabel = Number.isFinite(Number(epic.age_days))
    ? `${fmtNumber(epic.age_days)} дн.`
    : "—";
  const spanLabel = Number.isFinite(Number(epic.span_days))
    ? `${fmtNumber(epic.span_days)} дн.`
    : "—";

  openAppModal(
    `
    <div class="release-modal-head">
      <h2 id="app-modal-title">${issueLink(epic)} ${escapeHtml(epic.summary || "")}</h2>
      <p class="muted">
        Создан ${fmtDay(epic.created)} · обновлён ${fmtDay(epic.updated)} ·
        тянется ${ageLabel}
        ${epic.scope === "full_epic" ? " · полный объём эпика" : " · по задачам спринта"}
      </p>
    </div>
    <div class="person-modal-stats" style="margin-top:0.85rem">
      <div class="person-stat"><span class="label">Задачи</span><span class="value">${fmtNumber(epic.tasks_done)}/${fmtNumber(epic.tasks_total)}</span></div>
      <div class="person-stat"><span class="label">Открыто</span><span class="value">${fmtNumber(epic.tasks_open ?? epic.tasks_active)}</span></div>
      <div class="person-stat"><span class="label">Прогресс</span><span class="value">${fmtNumber(epic.progress_pct)}%</span></div>
      <div class="person-stat"><span class="label">Оценка / списано</span><span class="value">${fmtDuration(epic.estimate_hours)} / ${fmtDuration(epic.spent_hours)}</span></div>
      <div class="person-stat"><span class="label">Срок жизни</span><span class="value">${spanLabel}</span></div>
      <div class="person-stat"><span class="label">С тегами риска</span><span class="value">${fmtNumber(epic.tasks_risk)}</span></div>
    </div>
    <div class="person-modal-section">
      <h3>Релизы</h3>
      <div class="epic-release-list">${releases || `<p class="muted">Нет привязки к версиям в окне отчёта</p>`}</div>
    </div>
    ${
      conflicts
        ? `<div class="person-modal-section"><h3>Конфликты и риски</h3><div class="release-modal-risks">${conflicts}</div></div>`
        : `<div class="person-modal-section"><h3>Конфликты и риски</h3><p class="muted">Явных пересечений и рисков нет</p></div>`
    }
    <div class="person-modal-section">
      <h3>Открытые задачи по направлениям</h3>
      <p class="muted" style="margin:0 0 0.55rem">Показаны все не done. Задачи вне текущего спринта помечены тегом «Не в спринте».</p>
    </div>
    ${dirBlocks || `<div class="person-modal-section"><p class="muted">Нет открытых задач</p></div>`}
  `,
    { wide: true }
  );
}

function renderDirections(directions) {
  const root = document.getElementById("directions-summary");
  const mini = document.getElementById("directions-mini");
  const items = directions || [];
  if (mini) {
    mini.innerHTML = items
      .map((d) => {
        const left = d.tasks_remaining ?? (d.tasks_total - d.tasks_done);
        return `<span class="mini-chip">${directionIcon(d.name)} ${escapeHtml(d.name)} <strong>${fmtNumber(d.tasks_progress_pct)}%</strong> · ост. ${fmtNumber(left)}</span>`;
      })
      .join("");
  }
  root.innerHTML = items
    .map((d) => {
      const progressTone =
        (d.tasks_progress_pct || 0) >= 70
          ? "metric-accent"
          : (d.tasks_progress_pct || 0) >= 40
            ? "metric-info"
            : "metric-warn";
      return `
      <article class="direction-card">
        <div class="direction-card-top">
          ${directionIcon(d.name)}
          <h3>${escapeHtml(d.name)}</h3>
        </div>
        <div class="direction-metrics">
          <div class="row">
            <span class="label">Прогресс</span>
            <span class="metric ${progressTone}">${fmtNumber(d.tasks_done)}/${fmtNumber(d.tasks_total)} · ${fmtNumber(d.tasks_progress_pct)}%</span>
          </div>
          <div class="row">
            <span class="label">Осталось</span>
            <span class="metric metric-warn">${fmtNumber(d.tasks_remaining ?? (d.tasks_total - d.tasks_done))}</span>
          </div>
          <div class="row">
            <span class="label">Часы спринта</span>
            <span class="metric metric-info">${fmtNumber(d.hours_sprint)}</span>
          </div>
          <div class="row">
            <span class="label">Эпики</span>
            <span class="metric">${fmtNumber((d.epics || []).length)}</span>
          </div>
        </div>
      </article>`;
    })
    .join("");
}

function renderRatings(ratings) {
  const section = document.getElementById("ratings-section");
  const root = document.getElementById("ratings");
  const items = (ratings || []).filter((c) => c.enabled);
  if (!items.length) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");

  // Keep "Топы" last visually
  const ordered = [
    ...items.filter((c) => c.id !== "tops"),
    ...items.filter((c) => c.id === "tops"),
  ];

  root.innerHTML = ordered
    .map((cat) => {
      const people = cat.people || [];
      const allCount = (cat.all_people || people).length;
      const list =
        people.length === 0
          ? `<p class="muted">Пока нет данных</p>`
          : `<div class="rating-list">${people
              .map(
                (p) => `
              <div class="rating-person">
                ${medalHtml(p.place)}
                ${personCell(p.name, p.avatar_url, { short: true })}
                <div class="rating-score">
                  <div class="rating-score-main">${escapeHtml(p.value || "")}</div>
                  <div class="muted rating-score-detail">${escapeHtml(p.detail || p.direction || "")}</div>
                </div>
              </div>`
              )
              .join("")}</div>`;
      return `
        <article class="rating-card ${cat.id === "tops" ? "tops" : ""}" data-rating-id="${escapeHtml(cat.id)}" role="button" tabindex="0">
          <h3>${escapeHtml(cat.title)}</h3>
          <p class="desc">${escapeHtml(cat.description || "")}</p>
          ${list}
          <p class="rating-card-hint">Весь список · ${fmtNumber(allCount)}</p>
        </article>`;
    })
    .join("");
}

function releaseStatusBadge(r) {
  const risk = r.risk || "";
  const label = r.risk_label || "—";
  return `<span class="release-status risk-${escapeHtml(risk)}">${escapeHtml(label)}</span>`;
}

function releaseRightPanel(r) {
  const released = !!r.released || r.risk === "done";
  if (released) {
    const hasActive = Number(r.tasks_active) > 0;
    const leftover = hasActive
      ? `<p class="release-status-note is-warn">Ещё ${fmtNumber(r.tasks_active)} active-задач на версии</p>`
      : `<p class="release-status-note">Версия выпущена в Jira</p>`;
    return `
      <div class="release-side release-side-status">
        <h4>Детали</h4>
        <div class="release-status-block">
          ${leftover}
        </div>
        <div class="release-kpi-grid compact">
          <div class="release-kpi">
            <span class="label">Задачи</span>
            <strong>${fmtNumber(r.tasks_done)}/${fmtNumber(r.tasks_total)}</strong>
          </div>
          <div class="release-kpi">
            <span class="label">Прогресс</span>
            <strong>${fmtNumber(r.progress_pct)}%</strong>
          </div>
        </div>
        <div class="muted release-more-hint">Нажмите для подробностей</div>
      </div>`;
  }

  const items = r.risk_items || [];
  const riskCards = items.length
    ? items
        .map(
          (item) => `
        <div class="release-risk-card severity-${escapeHtml(item.severity || "warn")}">
          <div class="release-risk-card-title">${escapeHtml(item.title || "Риск")}</div>
          <div class="release-risk-card-summary">${escapeHtml(item.summary || "")}</div>
        </div>`
        )
        .join("")
    : `<div class="release-risk-card severity-ok">
        <div class="release-risk-card-title">Без явных рисков</div>
        <div class="release-risk-card-summary">Прогресс и ёмкость в допустимых пределах</div>
      </div>`;

  return `
    <div class="release-side release-side-status">
      <h4>Риски</h4>
      <div class="release-risk-cards">${riskCards}</div>
      <div class="release-kpi-grid">
        <div class="release-kpi">
          <span class="label">Активные</span>
          <strong>${fmtNumber(r.tasks_active)}</strong>
        </div>
        <div class="release-kpi">
          <span class="label">С рисками</span>
          <strong class="${r.tasks_tagged ? "is-warn" : ""}">${fmtNumber(r.tasks_tagged)}</strong>
        </div>
        <div class="release-kpi wide">
          <span class="label">Ост. / ёмкость</span>
          <strong>${fmtDuration(r.active_estimate_hours)} / ${fmtDuration(r.capacity_hours)}</strong>
        </div>
      </div>
      <div class="muted release-more-hint">Нажмите для подробностей</div>
    </div>`;
}

function renderReleases(releases) {
  const section = document.getElementById("releases-section");
  const root = document.getElementById("releases");
  const mini = document.getElementById("releases-mini");
  const items = releases || [];
  if (!items.length) {
    section.classList.add("hidden");
    root.innerHTML = "";
    if (mini) mini.innerHTML = "";
    return;
  }
  section.classList.remove("hidden");
  if (mini) {
    mini.innerHTML = items
      .map(
        (r) =>
          `<span class="mini-chip"><strong>${escapeHtml(r.name)}</strong> · ${fmtDay(r.release_date)} · ${escapeHtml(r.risk_label || "")} · ${fmtNumber(r.progress_pct)}%</span>`
      )
      .join("");
  }

  root.innerHTML = items
    .map((r) => {
      const daysLeft = Number(r.days_left);
      const daysLabel =
        Number.isFinite(daysLeft)
          ? daysLeft < 0
            ? `просрочен на ${fmtNumber(Math.abs(daysLeft))} дн.`
            : daysLeft === 0
              ? "сегодня"
              : `через ${fmtNumber(daysLeft)} дн.`
          : "—";
      const dirs = (r.sections || [])
        .map((s) => {
          const tipText = `${s.direction}: закрыто ${fmtNumber(s.done_tasks)} из ${fmtNumber(s.tasks)} (${fmtNumber(s.progress_pct)}%), активных ${fmtNumber(s.active_tasks)}`;
          const lagCls = s.is_lagging ? " is-lagging" : "";
          return `
          <span class="release-dir-chip${lagCls} has-tip" tabindex="0" data-tip="${escapeHtml(tipText)}" style="--chip-color:${escapeHtml(s.color)}">
            ${escapeHtml(s.direction)}
            <strong>${fmtNumber(s.progress_pct)}%</strong>
            <span class="muted">${fmtNumber(s.active_tasks)} активных</span>
          </span>`;
        })
        .join("");
      const calendarTip =
        "Доля календарного времени от старта спринта до даты выпуска версии. 100% значит: дата релиза уже наступила. Это не прогресс задач.";
      return `
        <article class="release-card" data-risk="${escapeHtml(r.risk || "")}" data-released="${r.released ? "1" : "0"}" data-release-id="${escapeHtml(r.id)}" role="button" tabindex="0">
          <div class="release-grid">
            <div class="release-side">
              <div class="release-title-row">
                <h3 class="release-title">${escapeHtml(r.name)}</h3>
                ${releaseStatusBadge(r)}
              </div>
              <div class="release-meta">
                <span class="release-date has-tip" tabindex="0" data-tip="Плановая дата выпуска версии в Jira">${fmtDay(r.release_date)}</span>
                <span>${tip(daysLabel, "Сколько дней осталось до (или прошло после) даты выпуска")}</span>
                <span>${escapeHtml(r.project || "")}</span>
              </div>
              <div class="release-progress" style="margin-top:0.75rem">
                <div class="progress has-tip" tabindex="0" data-tip="Заливка — прогресс задач по версии">
                  <div class="progress-bar" style="width:${Math.min(Number(r.progress_pct) || 0, 100)}%"></div>
                </div>
                <div class="muted">
                  ${tip(`задачи ${fmtNumber(r.progress_pct)}%`, "Доля закрытых задач команды на версии")}
                  ·
                  ${tip(`времени до релиза прошло ${fmtNumber(r.time_pct)}%`, calendarTip)}
                  ${
                    Number(r.slip_gap_pp) > 0 && !r.released
                      ? ` · <span class="metric metric-warn">отставание ${fmtNumber(r.slip_gap_pp)} п.п.</span>`
                      : ""
                  }
                </div>
              </div>
              <div class="release-dirs" style="margin-top:0.65rem">${dirs || `<span class="muted">Нет задач команды</span>`}</div>
            </div>
            ${releaseRightPanel(r)}
          </div>
        </article>`;
    })
    .join("");
}

function closeAppModal() {
  const modal = document.getElementById("app-modal");
  const card = modal.querySelector(".modal-card");
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  if (card) card.classList.remove("modal-card-wide", "modal-card-rating");
  document.getElementById("app-modal-body").innerHTML = "";
  hideFloatTip();
}

function openAppModal(html, { wide = false, rating = false } = {}) {
  const modal = document.getElementById("app-modal");
  const card = modal.querySelector(".modal-card");
  const body = document.getElementById("app-modal-body");
  body.innerHTML = html;
  if (card) {
    card.classList.toggle("modal-card-wide", !!wide);
    card.classList.toggle("modal-card-rating", !!rating);
  }
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
}

function openPersonModal(name) {
  const profile = currentSprintReport?.people?.[name];
  if (!profile) {
    openAppModal(
      `<h2 id="app-modal-title">Сотрудник</h2><p class="muted">Нет данных по «${escapeHtml(name)}» в текущем спринте.</p>`
    );
    return;
  }

  const expected = Number(currentSprintReport?.worklogs?.expected_hours_per_day) || 8;
  const maxH = Math.max(expected, ...(profile.day_hours || []).map((d) => Number(d.hours) || 0), 0.1);
  const chartH = 88;
  const chart = (profile.day_hours || [])
    .map((d) => {
      const h = Number(d.hours) || 0;
      const wd = weekdayShort(d.date);
      const isWeekend = wd === "сб" || wd === "вс";
      const remain = isWeekend ? 0 : Math.max(0, expected - h);
      const spentPx = Math.round((h / maxH) * chartH);
      const remainPx = isWeekend ? 0 : Math.round((remain / maxH) * chartH);
      const over = !isWeekend && h > expected + 0.05;
      const barCls = h <= 0 ? "is-zero" : over ? "is-over" : h + 0.05 < expected && !isWeekend ? "is-low" : "is-ok";
      const issueLines = (d.issues || [])
        .map((issue) => {
          const summary = String(issue.summary || "").trim();
          const label = summary ? `${issue.key}: ${summary}` : issue.key;
          return `${label} — ${fmtNumber(issue.hours)} ч`;
        })
        .join("\n");
      const head = isWeekend
        ? `${wd} ${fmtDay(d.date)}: списано ${fmtNumber(h)} ч (выходной)`
        : `${wd} ${fmtDay(d.date)}: списано ${fmtNumber(h)} ч, до нормы ${fmtNumber(remain)} ч`;
      const tipText = issueLines
        ? `${head}\n${issueLines}`
        : h > 0
          ? `${head}\nНет разбивки по задачам`
          : head;
      const tipAttr = escapeHtml(tipText).replaceAll("\n", "&#10;");
      return `
        <div class="person-hours-col has-tip" tabindex="0" data-tip="${tipAttr}">
          <div class="person-hours-bar-wrap" style="height:${chartH}px">
            <div class="person-hours-stack">
              ${remainPx > 0 ? `<div class="person-hours-remain" style="height:${remainPx}px"></div>` : ""}
              <div class="person-hours-bar ${barCls}" style="height:${Math.max(h > 0 ? 6 : 3, spentPx)}px"></div>
            </div>
          </div>
          <div class="person-hours-label">
            <strong>${escapeHtml(wd)}</strong>
            <span class="person-hours-spent-val">${fmtNumber(h)}ч</span>
            <span>${isWeekend ? "вых." : `ост ${fmtNumber(remain)}`}</span>
          </div>
        </div>`;
    })
    .join("");

  const ratings = (profile.ratings || [])
    .map(
      (r) =>
        `<span class="person-rating-chip">#${escapeHtml(r.place)} ${escapeHtml(r.title)}${r.value ? ` · ${escapeHtml(r.value)}` : ""}</span>`
    )
    .join("");

  const activeTasks = (profile.tasks_active || profile.tasks || []).slice(0, 12);
  const taskRows = activeTasks
    .map(
      (t) => `
      <div class="person-task-row ${t.risk ? "task-row-risk" : ""}">
        <div>${taskTitle(t, { showTags: true })}</div>
        <div class="muted">${timeCell(t.hours, t.estimate_hours, { warnZero: true })}</div>
      </div>`
    )
    .join("");

  const initial = (profile.name || "?").trim().charAt(0).toUpperCase();
  const avatar = profile.avatar_url
    ? `<img class="person-modal-avatar" src="${escapeHtml(profile.avatar_url)}" alt="" />`
    : `<span class="person-modal-avatar placeholder">${escapeHtml(initial)}</span>`;

  openAppModal(`
    <div class="person-modal-head">
      ${avatar}
      <div>
        <h2 id="app-modal-title">${escapeHtml(profile.name)}</h2>
        <p class="muted">${escapeHtml(profile.direction || "")}</p>
      </div>
    </div>
    <div class="person-modal-stats">
      <div class="person-stat"><span class="label">${tip("Активные", "Задачи в active-статусе направления")}</span><span class="value">${fmtNumber(profile.tasks_open)}</span></div>
      <div class="person-stat"><span class="label">${tip("Закрыто / всего", "Закрытые по правилам направления / все задачи сотрудника в спринте")}</span><span class="value">${fmtNumber(profile.tasks_done)}/${fmtNumber(profile.tasks_total)}</span></div>
      <div class="person-stat"><span class="label">${tip("Часы спринта", "Сумма worklog за дни спринта")}</span><span class="value">${fmtNumber(profile.hours_sprint)}</span></div>
      <div class="person-stat"><span class="label">${tip("MR", "Merge request’ы, связанные с задачами сотрудника")}</span><span class="value">${fmtNumber(profile.mr_count)}</span></div>
    </div>
    <div class="person-modal-section">
      <h3>Списания по дням</h3>
      <p class="muted person-hours-legend">Столбик: списано · сверху до нормы — остаток · норма ${fmtNumber(expected)} ч</p>
      <div class="person-hours-chart">${chart || `<span class="muted">Нет списаний</span>`}</div>
    </div>
    <div class="person-modal-section">
      <h3>Рейтинги</h3>
      <div class="person-rating-chips">${ratings || `<span class="muted">Пока нет попаданий в топы</span>`}</div>
    </div>
    <div class="person-modal-section">
      <h3>Активные задачи</h3>
      <div class="person-task-list">${taskRows || `<p class="muted">Нет активных задач направления</p>`}</div>
    </div>`);
}

function openRatingModal(catId) {
  const cat = (currentSprintReport?.ratings || []).find((c) => c.id === catId);
  if (!cat) return;
  const people = cat.all_people || cat.people || [];
  const podium = people.filter((p) => Number(p.place) >= 1 && Number(p.place) <= 3);
  const podiumHtml = podium.length
    ? `<div class="rating-modal-podium">${[2, 1, 3]
        .map((place) => {
          const p = podium.find((x) => Number(x.place) === place);
          if (!p) return `<div class="rating-podium-slot empty place-${place}"></div>`;
          return `
            <div class="rating-podium-slot place-${place}">
              ${medalHtml(place)}
              ${personCell(p.name, p.avatar_url, { short: true })}
              <div class="rating-score">
                <div>${escapeHtml(p.value || "")}</div>
                <div class="muted">${escapeHtml(p.direction || "")}</div>
              </div>
            </div>`;
        })
        .join("")}</div>`
    : "";
  const list = people.length
    ? `<div class="rating-modal-list">${people
        .map((p) => {
          const place = Number(p.place) || 0;
          const placeCls = place >= 1 && place <= 3 ? ` place-${place}` : "";
          return `
        <div class="rating-modal-row${placeCls}">
          ${medalHtml(place)}
          <div class="rating-modal-person">
            ${personCell(p.name, p.avatar_url, { short: false })}
            <div class="muted">${escapeHtml(p.direction || "")}</div>
          </div>
          <div class="rating-score">
            <div class="rating-score-value">${escapeHtml(p.value || "")}</div>
            <div class="muted rating-score-detail">${escapeHtml(p.detail || "")}</div>
          </div>
        </div>`;
        })
        .join("")}</div>`
    : `<p class="muted">Нет сотрудников в этой категории</p>`;

  openAppModal(
    `
    <div class="rating-modal-head">
      <p class="eyebrow">Рейтинг спринта</p>
      <h2 id="app-modal-title">${escapeHtml(cat.title)}</h2>
      <p class="muted">${escapeHtml(cat.description || "")}</p>
      <div class="rating-modal-meta">${fmtNumber(people.length)} сотрудников в категории</div>
    </div>
    ${podiumHtml}
    ${list}
  `,
    { wide: true, rating: true }
  );
}

function openPersonTasksModal(name) {
  const profile = currentSprintReport?.people?.[name];
  if (!profile) return;
  const tasks = profile.tasks_active || profile.tasks || [];
  const rows = tasks
    .map(
      (t) => `
      <div class="person-task-row ${t.risk ? "task-row-risk" : ""}">
        <div>${taskTitle(t, { showTags: true })}</div>
        <div class="muted">${timeCell(t.hours, t.estimate_hours, { warnZero: true })}</div>
      </div>`
    )
    .join("");
  openAppModal(
    `
    <h2 id="app-modal-title">Задачи · ${escapeHtml(shortName(name))}</h2>
    <p class="muted">${escapeHtml(profile.direction || "")} · активных ${fmtNumber(profile.tasks_open)} · с рисками ${fmtNumber(profile.risk_count)}</p>
    <div class="person-task-list" style="margin-top:0.85rem">${rows || `<p class="muted">Нет задач</p>`}</div>
  `,
    { wide: true }
  );
}

function isTaskDone(task) {
  if (!task) return false;
  if (task.direction_state === "done" || task.done || task.jira_done) return true;
  const cat = String(task.status_category || "").toLowerCase();
  return cat === "done";
}

function releaseTaskRowHtml(t, { forceRisk = false } = {}) {
  const done = isTaskDone(t);
  const rowCls = [
    "person-task-row",
    forceRisk || t.risk ? "task-row-risk" : "",
    done ? "task-row-done" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const status = done
    ? `<span class="task-done-badge">Выполнено</span>${statusChip(t.status, t.status_category)}`
    : statusChip(t.status, t.status_category);
  return `
    <div class="${rowCls}">
      <div>
        ${taskTitle(t, { showTags: true })}
        <div class="task-row-status">${status}</div>
      </div>
      <div class="muted">${escapeHtml(shortName(t.assignee || t.direction || "—"))}</div>
    </div>`;
}

function releaseRiskItemsHtml(release) {
  const items = release.risk_items || [];
  if (!items.length) {
    if (release.released || release.risk === "done") {
      return `<div class="release-modal-risk severity-ok"><div class="release-modal-risk-title">Релиз выпущен</div><p class="muted">Активных задач команды на версии нет</p></div>`;
    }
    if (release.risk === "ok" || release.risk === "on_track") {
      return `<div class="release-modal-risk severity-ok"><div class="release-modal-risk-title">${escapeHtml(release.risk_label || "В графике")}</div><p class="muted">Явных причин риска нет</p></div>`;
    }
    return "";
  }
  return items
    .map((item) => {
      const dirs = (item.directions || [])
        .map(
          (d) => `
          <span class="release-dir-chip ${Number(d.lag_pp) > 12 ? "is-lagging" : ""}" style="--chip-color:${escapeHtml(d.color || "#8a968c")}">
            ${escapeHtml(d.direction || "")}
            <strong>${fmtNumber(d.progress_pct)}%</strong>
            ${
              Number(d.lag_pp) > 0
                ? `<span class="muted">−${fmtNumber(d.lag_pp)} п.п.</span>`
                : `<span class="muted">${fmtNumber(d.active_tasks)} акт.</span>`
            }
          </span>`
        )
        .join("");
      const tasks = (item.tasks || [])
        .map((t) => releaseTaskRowHtml(t, { forceRisk: true }))
        .join("");
      return `
        <div class="release-modal-risk severity-${escapeHtml(item.severity || "warn")}">
          <div class="release-modal-risk-title">${escapeHtml(item.title || "Риск")}</div>
          <p class="release-modal-risk-summary">${escapeHtml(item.summary || "")}</p>
          ${item.detail ? `<p class="muted release-modal-risk-detail">${escapeHtml(item.detail)}</p>` : ""}
          ${dirs ? `<div class="release-dirs" style="margin-top:0.55rem">${dirs}</div>` : ""}
          ${tasks ? `<div class="person-task-list" style="margin-top:0.55rem">${tasks}</div>` : ""}
        </div>`;
    })
    .join("");
}

function openReleaseModal(releaseId) {
  const release = (currentSprintReport?.releases || []).find((r) => String(r.id) === String(releaseId));
  if (!release) return;
  const jiraBtn = release.web_url
    ? `<a class="jira-open-btn" href="${escapeHtml(release.web_url)}" target="_blank" rel="noreferrer">Открыть в Jira</a>`
    : "";
  const dirBlocks = (release.sections || [])
    .map((s) => {
      const lagBadge = s.is_lagging
        ? `<span class="release-lag-badge">отстаёт на ${fmtNumber(s.lag_pp)} п.п.</span>`
        : "";
      const taskList = (s.tasks_detail || [])
        .map((t) => releaseTaskRowHtml(t))
        .join("");
      return `
        <div class="person-modal-section ${s.is_lagging ? "is-lagging-section" : ""}">
          <h3>
            ${escapeHtml(s.direction)} · ${fmtNumber(s.progress_pct)}% · акт. ${fmtNumber(s.active_tasks)}
            ${lagBadge}
          </h3>
          <div class="progress" style="margin:0.35rem 0 0.55rem"><div class="progress-bar" style="width:${Math.min(s.progress_pct || 0, 100)}%"></div></div>
          <div class="person-task-list">${taskList || `<p class="muted">Нет задач направления</p>`}</div>
        </div>`;
    })
    .join("");
  const riskHtml = releaseRiskItemsHtml(release);
  openAppModal(
    `
    <div class="release-modal-head">
      <div class="release-modal-title-row">
        <h2 id="app-modal-title">${escapeHtml(release.name)}</h2>
        ${jiraBtn}
      </div>
      <div class="release-modal-head-meta">
        <span class="release-date">${fmtDay(release.release_date)}</span>
        <span class="muted">${escapeHtml(release.project || "")}</span>
        ${releaseStatusBadge(release)}
      </div>
    </div>
    <div class="person-modal-stats" style="margin-top:0.85rem">
      <div class="person-stat"><span class="label">Задачи</span><span class="value">${fmtNumber(release.tasks_done)}/${fmtNumber(release.tasks_total)}</span></div>
      <div class="person-stat"><span class="label">Активные</span><span class="value">${fmtNumber(release.tasks_active)}</span></div>
      <div class="person-stat"><span class="label">Прогресс</span><span class="value">${fmtNumber(release.progress_pct)}%</span></div>
      <div class="person-stat"><span class="label">Календарь</span><span class="value">${fmtNumber(release.time_pct)}%</span></div>
      ${
        release.released
          ? ""
          : `<div class="person-stat"><span class="label">Ост. / ёмкость</span><span class="value">${fmtDuration(release.active_estimate_hours)} / ${fmtDuration(release.capacity_hours)}</span></div>`
      }
      ${
        Number(release.slip_gap_pp) > 0 && !release.released
          ? `<div class="person-stat"><span class="label">Отставание</span><span class="value metric-warn">${fmtNumber(release.slip_gap_pp)} п.п.</span></div>`
          : ""
      }
    </div>
    ${riskHtml ? `<div class="person-modal-section"><h3>Риски релиза</h3><div class="release-modal-risks">${riskHtml}</div></div>` : ""}
    ${release.description ? `<div class="person-modal-section"><h3>Описание</h3><p class="muted">${escapeHtml(release.description)}</p></div>` : ""}
    ${dirBlocks || `<p class="muted">Нет задач команды на версии</p>`}
  `,
    { wide: true }
  );
}

function taskRow(t, { withCommits = true } = {}) {
  const commitsCell = withCommits
    ? `<td class="col-commits"><span class="metric">${t.commit_count == null ? "—" : fmtNumber(t.commit_count)}</span></td>`
    : "";
  return `
    <tr data-status="${escapeHtml(t.status || "")}" data-assignee="${escapeHtml(t.assignee || "")}" data-hours="${t.hours || 0}" data-commits="${t.commit_count || 0}" data-key="${escapeHtml(t.key || "")}">
      <td class="task-cell">${taskTitle(t)}</td>
      <td class="col-status">${statusChip(t.status, t.status_category)}</td>
      <td class="col-assignee">${personCell(t.assignee || "—", t.avatar_url, { short: true, personKey: t.assignee_canonical || t.assignee })}</td>
      <td class="col-hours">${timeCell(t.hours, t.estimate_hours, { warnZero: true })}</td>
      ${commitsCell}
    </tr>`;
}

function renderDirectionDetails(directions) {
  const root = document.getElementById("direction-details");
  root.innerHTML = (directions || [])
    .map((d) => {
      const withCommits = !!d.is_dev;
      const remainingCount = (d.remaining_tasks || []).length;
      const remainingRows = (d.remaining_tasks || [])
        .map((t) => taskRow(t, { withCommits }))
        .join("");

      const commitCols = withCommits
        ? `<th class="sortable col-commits" data-sort="commits" data-type="number">Коммиты</th>`
        : "";
      const emptyCols = withCommits ? 5 : 4;

      const epicRows = (d.epics || [])
        .map(
          (e) => `
          <tr class="epic-table-row" data-epic-key="${escapeHtml(e.key || "")}" data-hours="${e.hours || 0}" data-progress="${e.progress_pct || 0}" data-open="${e.open || 0}" data-key="${escapeHtml(e.key || "")}" role="button" tabindex="0">
            <td class="task-cell">${taskTitle(e, { showTags: false })}</td>
            <td class="col-progress">
              <div class="progress">
                <div class="progress-bar" style="width:${Math.min(e.progress_pct || 0, 100)}%"></div>
              </div>
              <div class="muted">${fmtNumber(e.done)}/${fmtNumber(e.total)} · осталось ${fmtNumber(e.open)} · <span class="metric metric-accent">${fmtNumber(e.progress_pct)}%</span></div>
            </td>
            <td class="col-hours">${timeCell(e.hours, e.estimate_hours, { warnZero: false })}</td>
          </tr>`
        )
        .join("");

      return `
        <section class="panel direction-detail collapsible-panel">
          <button type="button" class="collapsible-head" data-collapse-toggle aria-expanded="false">
            <div class="section-head tight">
              <h2>${directionIcon(d.name)} ${escapeHtml(d.name)}</h2>
              <p class="muted">Осталось ${fmtNumber(remainingCount)} · ${fmtNumber(d.tasks_progress_pct)}% · эпиков ${fmtNumber((d.epics || []).length)}</p>
            </div>
            <span class="collapse-toggle-btn">
              <span class="collapse-chevron" aria-hidden="true">▾</span>
              <span class="collapse-label" data-collapse-label>Развернуть</span>
            </span>
          </button>
          <div class="collapsible-summary">
            <span class="mini-chip">осталось <strong>${fmtNumber(remainingCount)}</strong></span>
            <span class="mini-chip">прогресс <strong>${fmtNumber(d.tasks_progress_pct)}%</strong></span>
            <span class="mini-chip">часы <strong>${fmtNumber(d.hours_sprint)}</strong></span>
            <span class="mini-chip">эпики <strong>${fmtNumber((d.epics || []).length)}</strong></span>
          </div>
          <div class="direction-stacks collapsible-body">
            <div class="subpanel full">
              <h4>Прогресс спринта · осталось ${fmtNumber(remainingCount)}</h4>
              <div class="table-wrap">
                <table class="js-sortable js-collapsible table-tasks" data-collapse-limit="${TASK_TABLE_PREVIEW}">
                  <thead><tr>
                    <th class="col-task">Задача</th>
                    <th class="sortable col-status" data-sort="status" data-type="string">Статус</th>
                    <th class="sortable col-assignee" data-sort="assignee" data-type="string">Исполнитель</th>
                    <th class="sortable col-hours" data-sort="hours" data-type="number">Время</th>
                    ${commitCols}
                  </tr></thead>
                  <tbody>${remainingRows || `<tr><td colspan="${emptyCols}" class="muted">Активных задач направления не осталось</td></tr>`}</tbody>
                </table>
              </div>
            </div>
            <div class="subpanel full">
              <h4>Эпики направления</h4>
              <p class="muted" style="margin:0 0 0.7rem">Прогресс по правилам статусов направления</p>
              <div class="table-wrap">
                <table class="js-sortable table-epics">
                  <thead><tr>
                    <th class="col-task">Эпик</th>
                    <th class="sortable col-progress" data-sort="progress" data-type="number">Прогресс направления</th>
                    <th class="sortable col-hours" data-sort="hours" data-type="number">Время</th>
                  </tr></thead>
                  <tbody>${epicRows || `<tr><td colspan="3" class="muted">Нет связанных эпиков</td></tr>`}</tbody>
                </table>
              </div>
            </div>
          </div>
        </section>`;
    })
    .join("");
  bindCollapseToggles(root);
  bindSortableTables(root);
  bindCollapsibleTables(root);
}

function renderTeam(report) {
  const worklogs = report.worklogs || {};
  const days = worklogs.days || [];
  let index = Math.max(0, days.indexOf(worklogs.selected_date));
  if (index < 0) index = Math.max(0, days.length - 1);

  const prevBtn = document.getElementById("day-prev");
  const nextBtn = document.getElementById("day-next");
  const label = document.getElementById("worklog-day-label");
  const note = document.getElementById("worklog-day-note");
  const root = document.getElementById("team-by-direction");
  const mini = document.getElementById("worklog-mini");
  const people = report.people || {};

  function paint() {
    const date = days[index];
    const day = (worklogs.by_day || []).find((d) => d.date === date);
    label.textContent = fmtDay(date);
    note.textContent = day?.is_weekend
      ? "выходной — контроль списания отключён"
      : `норма ${fmtNumber(worklogs.expected_hours_per_day)} ч`;
    prevBtn.disabled = index <= 0;
    nextBtn.disabled = index >= days.length - 1;

    const hoursByName = {};
    (day?.people || []).forEach((p) => {
      hoursByName[p.name] = p;
    });

    if (mini) {
      const dayHours = (day?.people || []).reduce((s, p) => s + (Number(p.hours) || 0), 0);
      const riskPeople = Object.values(people).filter((p) => (p.risk_count || 0) > 0).length;
      mini.innerHTML = `
        <span class="mini-chip"><strong>${fmtDay(date)}</strong></span>
        <span class="mini-chip">списано за день <strong>${fmtNumber(dayHours)} ч</strong></span>
        <span class="mini-chip">с рисками <strong>${fmtNumber(riskPeople)}</strong> чел.</span>`;
    }

    root.innerHTML = (report.directions || [])
      .map((direction) => {
        const rows = (direction.members || [])
          .map((person) => {
            const profile = people[person.name] || {};
            const dayRow = hoursByName[person.name];
            const hours = dayRow ? dayRow.hours : person.hours_today;
            const level = day?.is_weekend
              ? "skip"
              : dayRow
                ? dayRow.level
                : person.hours_level;
            const tasksTone =
              person.tasks_total > 0 && person.tasks_done === person.tasks_total
                ? "metric-accent"
                : "metric-info";
            const riskCount = Number(profile.risk_count) || 0;
            const remain = Number(profile.remaining_hours) || 0;
            return `
              <tr data-name="${escapeHtml(person.name || "")}" data-tasks="${person.tasks_open || 0}" data-hours="${hours || 0}" data-sprint="${person.hours_sprint || 0}" data-risks="${riskCount}" data-remain="${remain}">
                <td>${personCell(person.name, person.avatar_url, { short: true })}</td>
                <td>
                  <button type="button" class="tasks-cell-btn metric ${tasksTone}" data-person-tasks="${escapeHtml(person.name)}">
                    ${fmtNumber(person.tasks_done)}/${fmtNumber(person.tasks_total)}
                  </button>
                </td>
                <td><span class="metric ${riskCount ? "metric-warn" : ""}">${fmtNumber(riskCount)}</span></td>
                <td>${hoursCell(hours, level)}</td>
                <td><span class="metric metric-info">${fmtNumber(person.hours_sprint)}</span></td>
                <td><span class="metric">${tip(fmtDuration(remain), "Сумма remaining estimate по active-задачам")}</span></td>
              </tr>`;
          })
          .join("");
        return `
          <div class="direction-block">
            <h3>${directionIcon(direction.name)} ${escapeHtml(direction.name)}</h3>
            <div class="table-wrap">
              <table class="js-sortable table-people">
                <thead>
                  <tr>
                    <th class="sortable col-person" data-sort="name" data-type="string">Сотрудник</th>
                    <th class="sortable col-tasks" data-sort="tasks" data-type="number">Задачи</th>
                    <th class="sortable col-risks" data-sort="risks" data-type="number">Риски</th>
                    <th class="sortable col-hours-day" data-sort="hours" data-type="number">Часы за день</th>
                    <th class="sortable col-hours-sprint" data-sort="sprint" data-type="number">Часы за спринт</th>
                    <th class="sortable col-remain" data-sort="remain" data-type="number">Ост. оценка</th>
                  </tr>
                </thead>
                <tbody>${rows || `<tr><td colspan="6" class="muted">Нет сотрудников</td></tr>`}</tbody>
              </table>
            </div>
          </div>`;
      })
      .join("");
    bindSortableTables(root);
  }

  prevBtn.onclick = () => {
    if (index > 0) {
      index -= 1;
      paint();
    }
  };
  nextBtn.onclick = () => {
    if (index < days.length - 1) {
      index += 1;
      paint();
    }
  };
  paint();
}

function renderRisks(risks) {
  function block(title, items, empty) {
    const count = (items || []).length;
    const rows = (items || [])
      .map(
        (item) => `
      <tr data-assignee="${escapeHtml(item.assignee || "")}" data-direction="${escapeHtml(item.direction || "")}" data-status="${escapeHtml(item.status || "")}" data-hours="${item.hours || 0}" data-key="${escapeHtml(item.key || "")}">
        <td class="task-cell">${taskTitle(item)}</td>
        <td class="col-assignee">${personCell(item.assignee, item.avatar_url, { short: true, personKey: item.assignee_canonical || item.assignee })}</td>
        <td class="col-direction">${escapeHtml(item.direction || "—")}</td>
        <td class="col-status">${statusChip(item.status, item.status_category)}</td>
        <td class="col-hours">${timeCell(item.hours, item.estimate_hours, { warnZero: true })}</td>
        <td class="col-reason"><span class="reason-pill">${escapeHtml(item.reason || "—")}</span></td>
      </tr>`
      )
      .join("");
    return `
      <div class="risk-block">
        <h3>${title}</h3>
        <p class="risk-count">${count ? `${count} задач` : "пусто"}</p>
        ${
          rows
            ? `<div class="table-wrap"><table class="js-sortable js-collapsible table-risks" data-collapse-limit="${TASK_TABLE_PREVIEW}">
                <thead>
                  <tr>
                    <th class="col-task">Задача</th>
                    <th class="sortable col-assignee" data-sort="assignee" data-type="string">Исполнитель</th>
                    <th class="sortable col-direction" data-sort="direction" data-type="string">Направление</th>
                    <th class="sortable col-status" data-sort="status" data-type="string">Статус</th>
                    <th class="sortable col-hours" data-sort="hours" data-type="number">Время</th>
                    <th class="col-reason">Причина</th>
                  </tr>
                </thead>
                <tbody>${rows}</tbody>
              </table></div>`
            : `<p class="muted">${empty}</p>`
        }
      </div>`;
  }

  const root = document.getElementById("risks");
  root.innerHTML = [
    block(
      "Могут не закрыться до конца спринта",
      risks.at_risk,
      "Критичных задач не видно"
    ),
    block(
      `Застрявшие (нет обновлений ≥ ${fmtNumber(risks.stale_days ?? currentSprintReport?.settings?.metrics?.stale_days ?? 3)} дн.)`,
      risks.stale,
      "Нет застрявших задач"
    ),
    block("Открыты без списаний", risks.no_worklogs, "Нет таких задач"),
    block(
      "Задачи без оценки",
      risks.no_estimate,
      "У всех открытых задач спринта есть оценка"
    ),
  ].join("");
  bindSortableTables(root);
  bindCollapsibleTables(root);
}

function paintReport(report) {
  const meta = document.getElementById("meta");
  meta.textContent = [
    `источники: ${(report.meta?.sources || []).join(", ") || "—"}`,
    `команда: ${fmtNumber(report.meta?.team_size)}`,
    `собрано: ${fmtDateTime(report.meta?.fetched_at)}`,
  ].join(" · ");
  currentReportMeta = report.meta || null;
  startFreshnessClock();

  const err = document.getElementById("error");
  if (report.error) {
    err.classList.remove("hidden");
    err.textContent = report.error;
  } else {
    err.classList.add("hidden");
    err.textContent = "";
  }

  const sr = report.sprint_report;
  if (!sr) {
    showReport(false);
    if (!report.error) {
      err.classList.remove("hidden");
      err.textContent = "Спринтовый отчёт пуст.";
    }
    return;
  }

  currentSprintReport = sr;
  const directions = sortByDirectionOrder(sr.directions || [], sr.direction_order || []);
  sr.directions = directions;
  TASK_TABLE_PREVIEW = Math.max(
    1,
    Number(sr.settings?.ui?.task_table_preview) || 5
  );
  showReport(true);
  renderSprintHeader(sr.sprint, sr.team_mood);
  renderDirections(directions);
  renderReleases(sr.releases || []);
  renderEpicTimeline(sr.epic_timeline || {});
  renderDirectionDetails(directions);
  renderTeam(sr);
  renderRisks(sr.risks || {});
  renderRatings(sr.ratings || []);
  bindCollapseToggles(document.getElementById("report-root") || document);
}

async function refreshData() {
  const err = document.getElementById("error");
  err.classList.add("hidden");
  err.textContent = "";
  showReport(false);
  setRefreshEnabled(false);
  renderLoading({
    status: "collecting",
    message: "Запускаю обновление…",
    steps: [{ key: "ui", label: "Запрос на обновление", state: "running" }],
  });

  try {
    await requestRefresh();
    const report = await waitForReport();
    hideLoading();
    paintReport(report);
    setRefreshEnabled(true);
  } catch (e) {
    setRefreshEnabled(true);
    if (!document.getElementById("error").textContent) {
      err.classList.remove("hidden");
      err.textContent = String(e.message || e);
    }
  }
}

async function main() {
  const refreshBtn = document.getElementById("refresh-btn");
  if (IS_PUBLISH) {
    setRefreshEnabled(false);
  } else if (refreshBtn) {
    refreshBtn.addEventListener("click", () => {
      refreshData();
    });
  }

  document.addEventListener("mouseover", (event) => {
    const el = event.target.closest("[data-tip]");
    if (el) showFloatTip(el, el.getAttribute("data-tip"));
  });
  document.addEventListener("mouseout", (event) => {
    const el = event.target.closest("[data-tip]");
    if (!el) return;
    const next = event.relatedTarget;
    if (next && el.contains(next)) return;
    hideFloatTip();
  });
  document.addEventListener("scroll", hideFloatTip, true);

  document.addEventListener("click", (event) => {
    const closeEl = event.target.closest("[data-modal-close]");
    if (closeEl) {
      closeAppModal();
      return;
    }
    const tasksBtn = event.target.closest("[data-person-tasks]");
    if (tasksBtn) {
      event.preventDefault();
      event.stopPropagation();
      openPersonTasksModal(tasksBtn.getAttribute("data-person-tasks"));
      return;
    }
    const personBtn = event.target.closest(".person-btn[data-person]");
    if (personBtn) {
      event.preventDefault();
      event.stopPropagation();
      openPersonModal(personBtn.getAttribute("data-person"));
      return;
    }
    const epicRow = event.target.closest(
      ".epic-row[data-epic-key], tr.epic-table-row[data-epic-key]"
    );
    if (epicRow && !event.target.closest("a")) {
      openEpicModal(epicRow.getAttribute("data-epic-key"));
      return;
    }
    const releaseCard = event.target.closest(".release-card[data-release-id]");
    if (releaseCard && !event.target.closest("a")) {
      openReleaseModal(releaseCard.getAttribute("data-release-id"));
      return;
    }
    const epicRelease = event.target.closest(
      ".epic-release-row.is-clickable[data-release-id]"
    );
    if (epicRelease && !event.target.closest("a")) {
      event.preventDefault();
      openReleaseModal(epicRelease.getAttribute("data-release-id"));
      return;
    }
    const moodCard = event.target.closest(".mood-card[data-team-mood]");
    if (moodCard) {
      openTeamMoodModal(currentSprintReport?.team_mood);
      return;
    }
    const ratingCard = event.target.closest(".rating-card[data-rating-id]");
    if (ratingCard && !event.target.closest(".person-btn")) {
      openRatingModal(ratingCard.getAttribute("data-rating-id"));
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeAppModal();
      hideFloatTip();
    }
    if (event.key === "Enter" || event.key === " ") {
      const epicRow = event.target.closest?.(
        ".epic-row[data-epic-key], tr.epic-table-row[data-epic-key]"
      );
      if (epicRow) {
        event.preventDefault();
        openEpicModal(epicRow.getAttribute("data-epic-key"));
        return;
      }
      const epicRelease = event.target.closest?.(
        ".epic-release-row.is-clickable[data-release-id]"
      );
      if (epicRelease) {
        event.preventDefault();
        openReleaseModal(epicRelease.getAttribute("data-release-id"));
        return;
      }
      const moodCard = event.target.closest?.(".mood-card[data-team-mood]");
      if (moodCard) {
        event.preventDefault();
        openTeamMoodModal(currentSprintReport?.team_mood);
        return;
      }
      const ratingCard = event.target.closest?.(".rating-card[data-rating-id]");
      if (ratingCard) {
        event.preventDefault();
        openRatingModal(ratingCard.getAttribute("data-rating-id"));
        return;
      }
      const releaseCard = event.target.closest?.(".release-card[data-release-id]");
      if (releaseCard) {
        event.preventDefault();
        openReleaseModal(releaseCard.getAttribute("data-release-id"));
      }
    }
  });

  renderLoading({
    status: "starting",
    message: "Открываю интерфейс…",
    steps: [
      { key: "ui", label: "Открытие страницы", state: "done" },
      { key: "init", label: "Подготовка", state: "pending" },
      { key: "jira", label: "Jira: спринт, задачи, worklogs", state: "pending" },
      { key: "gitlab", label: "GitLab: merge requests", state: "pending" },
      { key: "commits", label: "GitLab: коммиты по MR", state: "pending" },
      { key: "metrics", label: "Расчёт метрик, рейтингов и рисков", state: "pending" },
      { key: "save", label: "Сохранение отчёта на диск", state: "pending" },
    ],
  });

  try {
    const report = await waitForReport();
    hideLoading();
    paintReport(report);
    setRefreshEnabled(true);
  } catch (err) {
    setRefreshEnabled(true);
    if (!document.getElementById("error").textContent) {
      document.getElementById("meta").textContent = String(err.message || err);
    }
  }
}

main();
