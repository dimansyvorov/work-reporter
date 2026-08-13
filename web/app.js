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

function fmtTimePart(d) {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

const MONTHS_RU_GENITIVE = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];

function calendarDayKey(d) {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

/** Today → «11:57»; yesterday → «вчера, 09:56»; else → «27 июля, 10:16» */
function fmtRelativeDateTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return fmtDay(value);
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const time = fmtTimePart(d);
  if (calendarDayKey(d) === calendarDayKey(now)) return time;
  if (calendarDayKey(d) === calendarDayKey(yesterday)) return `вчера, ${time}`;
  return `${d.getDate()} ${MONTHS_RU_GENITIVE[d.getMonth()]}, ${time}`;
}

function fmtDateTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return fmtDay(value);
  return `${fmtDay(value)} ${fmtTimePart(d)}`;
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

/** «Иван И.» from «Иванов Иван Иванович» */
function shortGivenSurname(name) {
  const parts = String(name || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (!parts.length) return "—";
  if (parts.length === 1) return parts[0];
  return `${parts[1]} ${parts[0].charAt(0)}.`;
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

function teamAvatarOverride(name) {
  const map = currentSprintReport?.settings?.avatars || {};
  if (!name || !map || typeof map !== "object") return null;
  if (map[name]) return map[name];
  const profile = findPersonByName(name);
  if (profile?.name && map[profile.name]) return map[profile.name];
  return null;
}

function personCell(
  name,
  avatarUrl,
  {
    short = false,
    givenSurname = false,
    clickable = true,
    personKey = null,
    load = null,
    withLoad = true,
  } = {}
) {
  const key = personKey || name;
  const profile = findPersonByName(key) || findPersonByName(name);
  const label = givenSurname
    ? shortGivenSurname(name)
    : short
      ? shortName(name)
      : name;
  const resolvedUrl =
    teamAvatarOverride(profile?.name || key || name) || avatarUrl || profile?.avatar_url || null;
  const resolvedLoad = withLoad ? load || profile?.load || null : null;
  const avatar = avatarWithLoad(avatarImgHtml(name, resolvedUrl), resolvedLoad, { size: "sm" });
  const inner = `${avatar}<span class="person-name">${escapeHtml(label)}</span>`;
  if (!clickable || !key || key === "—" || name === "Без исполнителя") {
    return `<div class="person" title="${escapeHtml(name || "")}">${inner}</div>`;
  }
  return `<button type="button" class="person person-btn" data-person="${escapeHtml(profile?.name || key)}" title="${escapeHtml(name)}">${inner}</button>`;
}

function avatarInitial(name) {
  const parts = String(name || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (!parts.length) return "?";
  // FIO «Фамилия Имя …» → letter of given name; otherwise first token
  const token = parts.length >= 2 ? parts[1] : parts[0];
  return (token.charAt(0) || "?").toUpperCase();
}

function avatarImgHtml(name, avatarUrl, { cls = "avatar" } = {}) {
  const initial = avatarInitial(name);
  const placeholderCls = cls.includes("act-chip-avatar")
    ? `${cls} is-placeholder`
    : `${cls} placeholder`;
  if (!avatarUrl) {
    return `<span class="${placeholderCls}">${escapeHtml(initial)}</span>`;
  }
  const safeInitial = escapeHtml(initial);
  return `<img class="${cls}" src="${escapeHtml(avatarUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.outerHTML='<span class=\\'${placeholderCls}\\'>${safeInitial}</span>'" />`;
}

function avatarWithLoad(avatarHtml, load, { size = "sm" } = {}) {
  // Always show a ring when we know the load state (including empty).
  // Skip only when there is no person load data at all.
  // No has-tip here: dashed underline + float tip break the circular ring visually.
  if (!load || load.level === "unknown") {
    return `<span class="avatar-wrap size-${size}">${avatarHtml}</span>`;
  }
  const pct = load.load_pct;
  const level = load.level || "empty";
  const fill = pct == null ? 0 : Math.max(0, Math.min(100, Number(pct) || 0));
  return `<span class="avatar-wrap size-${size} has-ring load-${escapeHtml(level)}" style="--load:${fill}" aria-hidden="true">${avatarHtml}</span>`;
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
  const key = String(issue.key).trim().toUpperCase();
  const label = escapeHtml(key);
  const epicCls = issue.is_epic ? " issue-key-epic" : "";
  const jiraUrl =
    issue.web_url ||
    jiraBrowseUrlForKey(key) ||
    "";
  const jiraAttr = jiraUrl ? ` data-jira-url="${escapeHtml(jiraUrl)}"` : "";
  const known = !!currentSprintReport?.issues?.[key];
  const title = known
    ? "Открыть карточку задачи"
    : jiraUrl
      ? "Открыть задачу в Jira"
      : "Открыть карточку задачи";
  return `<button type="button" class="issue-key issue-key-btn${epicCls}" data-issue="${label}"${jiraAttr} title="${title}">${label}</button>`;
}

function tagsHtml(tags, { clickableReleases = false } = {}) {
  if (!tags?.length) return "";
  return tags
    .map((t) => {
      const hint = t.hint ? escapeHtml(t.hint) : "";
      const tipCls = hint ? " has-tip" : "";
      const tipAttr = hint ? ` tabindex="0" data-tip="${hint}"` : "";
      const label = escapeHtml(t.label || "");
      if (clickableReleases && t.id === "release") {
        const rid = t.release_id != null ? String(t.release_id) : "";
        const rname = t.label || "";
        return `<button type="button" class="task-tag tag-release release-tag-btn${tipCls}" data-release-id="${escapeHtml(rid)}" data-release-name="${escapeHtml(rname)}"${tipAttr} title="Открыть релиз">${label}</button>`;
      }
      return `<span class="task-tag${tipCls} tag-${escapeHtml(t.tone || "muted")}"${tipAttr}>${label}</span>`;
    })
    .join("");
}

function fmtDuration(hours) {
  if (hours == null || Number.isNaN(Number(hours))) return "—";
  const totalMin = Math.max(0, Math.round(Number(hours) * 60));
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return `${h}ч ${m}м`;
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
  const meta = mood.components_meta || {};
  const weights = mood.weights || {
    schedule: 28,
    completion: null,
    task_risks: 24,
    releases: 20,
    epics_in_sprint: 10,
  };
  const defaults = {
    schedule: {
      label: "Соблюдение плана",
      hint: "Насколько % закрытых задач не отстаёт от % прошедшего времени спринта. 100 — идём в ногу или опережаем; ниже — отстаём.",
    },
    completion: {
      label: "Темп закрытия",
      hint: "Насколько фактическое закрытие задач соответствует ожидаемому к текущему моменту спринта. Не «сколько всего закрыли», а «успеваем ли по календарю».",
    },
    task_risks: {
      label: "Здоровье задач",
      hint: "Штраф за доли открытых задач с рисками: угроза срока, неактивность, без списаний, без оценки, без релиза. 100 — таких почти нет.",
    },
    releases: {
      label: "Готовность релизов",
      hint: "Доля активных релизов без риска срыва/просрочки минус среднее отставание прогресса. Это не «% выпущенных релизов».",
    },
    epics_in_sprint: {
      label: "Эпики в спринте",
      hint: "Средний прогресс эпиков с задачами текущего спринта с учётом доли рисковых задач в них.",
    },
  };
  const order = ["schedule", "completion", "task_risks", "releases", "epics_in_sprint"];
  const componentsHtml = order
    .map((key) => {
      const info = meta[key] || defaults[key] || {};
      const label = info.label || key;
      const hint = info.hint || "";
      const w =
        info.weight_pct != null
          ? info.weight_pct
          : weights[key] != null
            ? weights[key]
            : null;
      const wHtml =
        w != null
          ? `<span class="mood-comp-weight">вес ${fmtNumber(w)}%</span>`
          : "";
      return `
      <div class="mood-component has-tip" tabindex="0" data-tip="${escapeHtml(hint)}">
        <span class="label">${escapeHtml(label)}</span>
        <strong>${fmtNumber(comps[key])} <span class="mood-comp-unit">/ 100</span></strong>
        ${wHtml}
      </div>`;
    })
    .join("");
  const drivers = mood.drivers || [];
  const driversHtml = drivers.length
    ? drivers
        .map(
          (d) => `
        <div class="mood-driver severity-${escapeHtml(d.severity || "warn")}">
          <p class="mood-driver-title">${escapeHtml(d.title || "Фактор")}</p>
          <p class="mood-driver-summary">${escapeHtml(d.summary || "")}</p>
          <p class="muted" style="margin:0.35rem 0 0">оценка влияния −${fmtNumber(d.impact)} п.п. к итогу</p>
        </div>`
        )
        .join("")
    : `<p class="muted">Явных негативных факторов нет — итог опирается на взвешенные компоненты выше.</p>`;
  const ctx = mood.context || {};
  const formula =
    mood.formula_hint ||
    "Итог = взвешенное среднее пяти оценок (каждая 0…100). Вес «Темпа закрытия» растёт к концу спринта. В начале спринта высокий балл слегка приглушается, чтобы отсутствие рисков не выглядело как победа.";
  openAppModal(
    `
    <div class="release-modal-head">
      <h2 id="app-modal-title">${escapeHtml(mood.emoji || "")} Настроение команды · ${fmtNumber(mood.score)}</h2>
      <p class="muted">${escapeHtml(mood.recommendation || "")}</p>
    </div>
    <p class="mood-score-legend muted">
      Итоговый балл <strong>${fmtNumber(mood.score)}</strong> из 100 — индекс здоровья спринта, не процент выпущенных релизов и не % закрытых задач.
    </p>
    <p class="muted" style="margin:0.55rem 0 0">
      Контекст: закрыто ${fmtNumber(ctx.tasks_done)}/${fmtNumber(ctx.tasks_total)} задач ·
      прошло ${fmtNumber(ctx.time_progress_pct)}% времени спринта ·
      активных релизов ${fmtNumber(ctx.active_releases)} ·
      эпиков в скоупе ${fmtNumber(ctx.epics_in_scope)}
    </p>
    <div class="person-modal-section">
      <h3>Из чего складывается балл</h3>
      <p class="muted mood-formula-hint">${escapeHtml(formula)}</p>
      <div class="mood-components">${componentsHtml}</div>
      <p class="muted" style="margin:0.65rem 0 0;font-size:0.82rem">Наведите на карточку — короткое пояснение расчёта.</p>
    </div>
    <div class="person-modal-section">
      <h3>Что сильнее всего тянет вниз</h3>
      <div class="mood-modal-drivers">${driversHtml}</div>
    </div>
  `,
    { wide: true, type: "mood" }
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

function corporateAuthorLabel(brief) {
  if (!brief) return "corporate unknown";
  if (brief.author) return String(brief.author);
  const model = brief.model_label || String(brief.model || "").replace(/^\/?models\//i, "");
  return `corporate ${model || "unknown"}`;
}

function humanizeAiText(text) {
  let out = String(text || "");
  const replacements = [
    [/\btasks_pct\b/gi, "выполнение задач"],
    [/\btime_pct\b/gi, "прохождение спринта"],
    [/\bprogress_pct\b/gi, "прогресс"],
    [/\bload_pct\b/gi, "нагрузка"],
    [/\bdays_left\b/gi, "дней осталось"],
    [/\bactive_tasks\b/gi, "активных задач"],
    [/\bstale-задач/gi, "неактивных задач"],
    [/\bstale\b/gi, "неактивные"],
    [/`?at_risk`?/gi, "риск срыва"],
    [/`?on_track`?/gi, "в графике"],
    [/`?overdue`?/gi, "просрочен"],
  ];
  for (const [re, repl] of replacements) out = out.replace(re, repl);
  out = out.replace(
    /\(\s*выполнение задач\s+([0-9]+(?:[.,][0-9]+)?)\s*%\s*[<>]\s*прохождение спринта\s+([0-9]+(?:[.,][0-9]+)?)\s*%\s*\)/gi,
    "(задачи закрыты на $1%, по календарю прошло $2%)"
  );
  return out;
}

function extractAiVerdict(markdown) {
  const text = String(markdown || "").trim();
  if (!text) return null;
  const match = text.match(/##\s*Вердикт\s*\n+([\s\S]*?)(?=\n##\s|$)/i);
  const chunk = (match ? match[1] : text).trim();
  const lines = chunk
    .split("\n")
    .map((ln) => ln.replace(/^[-*]\s+/, "").trim())
    .filter(Boolean);
  if (!lines.length) return null;
  let verdict = humanizeAiText(lines.slice(0, 3).join(" ").trim());
  if (verdict.length > 220) verdict = `${verdict.slice(0, 217).trim()}…`;
  return verdict || null;
}

function isKnownEpicKey(key) {
  const label = String(key || "").trim().toUpperCase();
  if (!label) return false;
  const epics = currentSprintReport?.epic_timeline?.epics || [];
  if (epics.some((e) => String(e?.key || "").toUpperCase() === label)) return true;
  const issue = currentSprintReport?.issues?.[label];
  return !!(issue && (issue.is_epic || issue.issue_type === "Epic"));
}

function shortTaskLabel(summary, words = 3) {
  let text = String(summary || "").trim();
  if (!text) return "";
  text = text.replace(/^\[[^\]]+\]\s*/g, "").trim();
  const parts = text.split(/\s+/).filter(Boolean);
  if (!parts.length) return "";
  return parts.slice(0, words).join(" ");
}

function aiTaskTitle(summary, maxLen = 72) {
  let text = String(summary || "").trim();
  if (!text) return "";
  text = text.replace(/^\[[^\]]+\]\s*/g, "").trim();
  if (text.length <= maxLen) return text;
  return `${text.slice(0, maxLen - 1).trim()}…`;
}

function lookupIssueSummary(key) {
  const k = String(key || "").trim().toUpperCase();
  if (!k) return null;
  const issue = currentSprintReport?.issues?.[k];
  if (issue?.summary) return issue.summary;
  for (const rel of currentSprintReport?.releases || []) {
    for (const t of rel?.tasks || []) {
      if (String(t?.key || "").toUpperCase() === k && t?.summary) return t.summary;
    }
    for (const s of rel?.sections || []) {
      for (const t of s?.tasks_detail || []) {
        if (String(t?.key || "").toUpperCase() === k && t?.summary) return t.summary;
      }
    }
  }
  for (const epic of currentSprintReport?.epic_timeline?.epics || []) {
    if (String(epic?.key || "").toUpperCase() === k && epic?.summary) return epic.summary;
    for (const s of epic?.sections || []) {
      for (const t of s?.tasks_detail || []) {
        if (String(t?.key || "").toUpperCase() === k && t?.summary) return t.summary;
      }
    }
  }
  return null;
}

function stripRedundantIssueTitleParens(text) {
  // Model often repeats titles: KEY (title) (title) — UI adds the title inside the link.
  let out = String(text || "").replace(
    /(`?[A-Z][A-Z0-9]+-\d+`?)(?:\s*\([^)]{1,120}\))+/g,
    "$1"
  );
  // Also drop bold wrappers around bare/backticked keys: **`SPRINT-1`** / **SPRINT-1**
  out = out.replace(
    /\*\*\s*(`?[A-Z][A-Z0-9]+-\d+`?)\s*\*\*/gi,
    "$1"
  );
  out = out.replace(
    /__\s*(`?[A-Z][A-Z0-9]+-\d+`?)\s*__/gi,
    "$1"
  );
  return out;
}

function issueTitleVariants(summary) {
  const raw = String(summary || "").trim();
  if (!raw) return [];
  const noBracket = raw.replace(/^\[[^\]]+\]\s*/g, "").trim();
  const clipped = aiTaskTitle(raw);
  return [...new Set([clipped, noBracket, raw].filter((t) => t && t.length >= 4))];
}

/** Word-by-word title prefix match; returns chars consumed (0 if no match). */
function matchAiTitlePrefix(text, title) {
  const words = String(title || "")
    .split(/\s+/)
    .filter(Boolean);
  if (!words.length) return 0;
  let i = 0;
  const src = String(text || "");
  while (i < src.length && /\s/.test(src[i])) i += 1;
  if (i >= src.length) return 0;
  // optional wrapping parens around the whole title
  const openParen = src[i] === "(";
  if (openParen) {
    i += 1;
    while (i < src.length && /\s/.test(src[i])) i += 1;
  }
  for (const word of words) {
    while (i < src.length && /\s/.test(src[i])) i += 1;
    const chunk = src.slice(i, i + word.length);
    if (chunk.toLowerCase() !== word.toLowerCase()) return 0;
    i += word.length;
  }
  if (openParen) {
    while (i < src.length && /\s/.test(src[i])) i += 1;
    if (src[i] === ")") i += 1;
    else return 0;
  }
  return i;
}

function consumeDuplicateIssueTitles(after, summary) {
  let rest = String(after || "");
  if (!summary) {
    return rest.replace(/^(?:\s*\([^)]{1,120}\))+/, "");
  }
  const titles = issueTitleVariants(summary);
  for (let round = 0; round < 4; round += 1) {
    let consumed = 0;
    for (const title of titles) {
      consumed = matchAiTitlePrefix(rest, title);
      if (consumed > 0) break;
    }
    if (consumed <= 0) break;
    rest = rest.slice(consumed);
  }
  return rest;
}

function stripIssueTitlesFromAiText(text) {
  // Remove model-written titles after keys before UI injects its own title.
  const src = String(text || "");
  let out = "";
  let last = 0;
  const re = /`?([A-Z][A-Z0-9]+-\d+)`?/gi;
  let m;
  while ((m = re.exec(src))) {
    out += src.slice(last, m.index);
    out += m[0];
    const summary = lookupIssueSummary(m[1]);
    const rest = src.slice(m.index + m[0].length);
    const kept = consumeDuplicateIssueTitles(rest, summary);
    const consumed = rest.length - kept.length;
    last = m.index + m[0].length + consumed;
    re.lastIndex = last;
  }
  out += src.slice(last);
  return out;
}

function stripDuplicateIssueTitlesAfterButtons(html) {
  // Safety net if a title still follows the rendered issue link.
  return String(html || "").replace(
    /(<button\b[^>]*\b(?:data-issue|data-epic)="([^"]+)"[^>]*>[\s\S]*?<\/button>)([^<]*)/gi,
    (whole, button, key, after) => {
      if (!after || !after.trim()) return button;
      return button + consumeDuplicateIssueTitles(after, lookupIssueSummary(key));
    }
  );
}

function issueKeyButtonHtml(key) {
  const raw = String(key || "").trim().toUpperCase();
  if (!raw) return "";
  const keyHtml = `<span class="ai-issue-key">${escapeHtml(raw)}</span>`;
  const title = aiTaskTitle(lookupIssueSummary(raw));
  const titleHtml = title
    ? `<span class="ai-issue-sep"> | </span><span class="ai-issue-title">${escapeHtml(title)}</span>`
    : "";
  const label = `${keyHtml}${titleHtml}`;
  if (isKnownEpicKey(raw)) {
    return `<button type="button" class="ai-issue-link issue-key-btn issue-key-epic" data-epic="${escapeHtml(raw)}" title="Открыть эпик">${label}</button>`;
  }
  const known = !!currentSprintReport?.issues?.[raw];
  const jiraUrl = jiraBrowseUrlForKey(raw);
  const jiraAttr = jiraUrl ? ` data-jira-url="${escapeHtml(jiraUrl)}"` : "";
  const tip = known ? "Открыть карточку задачи" : "Открыть задачу в Jira";
  return `<button type="button" class="ai-issue-link issue-key-btn" data-issue="${escapeHtml(raw)}"${jiraAttr} title="${tip}">${label}</button>`;
}

function formatAiInlineMarkdown(text) {
  const cleaned = stripIssueTitlesFromAiText(stripRedundantIssueTitleParens(text));
  // Escape first, then restore a small safe subset of markdown emphasis.
  let html = escapeHtml(cleaned);
  // Bold: **text** or __text__
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__(.+?)__/g, "<strong>$1</strong>");
  // Italic: *text* or _text_ (avoid matching inside words like snake_case)
  html = html.replace(/(^|[\s(])\*(?!\s)(.+?)(?!\s)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
  html = html.replace(/(^|[\s(])_(?!\s)(.+?)(?!\s)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
  // Inline code: `code` — issue keys become clickable, not dead <code>
  html = html.replace(/`([^`]+)`/g, (_, code) => {
    if (/^[A-Z][A-Z0-9]+-\d+$/i.test(code)) {
      return issueKeyButtonHtml(code);
    }
    return `<code class="ai-brief-code">${code}</code>`;
  });
  // Bare issue keys → open task modal / Jira (skip text already inside issue buttons)
  html = linkifyIssueKeysInHtml(html);
  html = stripDuplicateIssueTitlesAfterButtons(html);
  // Person names → open profile modal (skip names inside issue titles)
  return linkifyAiPersonNames(html);
}

/** Approximate Russian surname genitive for short mentions in AI text. */
function surnameGenitiveForms(surname) {
  const s = String(surname || "").trim();
  if (!s) return [];
  const forms = new Set([s]);
  const lower = s.toLowerCase();
  if (lower.endsWith("ова") || lower.endsWith("ева") || lower.endsWith("ина")) {
    forms.add(s.slice(0, -1) + "ой");
  } else if (lower.endsWith("ая")) {
    forms.add(s.slice(0, -2) + "ой");
  } else if (lower.endsWith("я")) {
    forms.add(s.slice(0, -1) + "и");
  } else if (lower.endsWith("а")) {
    forms.add(s.slice(0, -1) + "ы");
  } else if (/[бвгджзклмнпрстфхцчшщ]$/i.test(s)) {
    forms.add(s + "а");
    forms.add(s + "у");
  }
  return [...forms];
}

function buildAiPersonLinkPatterns() {
  const team = currentSprintReport?.team || [];
  const people = currentSprintReport?.people || {};
  const names = new Set([
    ...team.map((p) => p?.name).filter(Boolean),
    ...Object.keys(people),
  ]);
  const patterns = [];
  const seen = new Set();
  for (const full of names) {
    const short = shortName(full);
    const givenShort = shortGivenSurname(full);
    const parts = String(full).trim().split(/\s+/).filter(Boolean);
    const surname = parts[0] || "";
    const given = parts[1] || "";
    const initials =
      parts.length >= 3
        ? `${parts[1].charAt(0)}\\.?\\s*${parts[2].charAt(0)}\\.?`
        : parts.length === 2
          ? `${parts[1].charAt(0)}\\.?`
          : "";
    const variants = new Set([full, short, givenShort]);
    if (given && surname) {
      variants.add(`${given} ${surname.charAt(0)}.`);
      variants.add(`${given} ${surname.charAt(0)}`);
    }
    for (const sur of surnameGenitiveForms(surname)) {
      variants.add(sur);
      if (initials) {
        variants.add(`${sur} ${parts.length >= 3 ? `${parts[1].charAt(0)}.${parts[2].charAt(0)}.` : `${parts[1].charAt(0)}.`}`);
        variants.add(`${sur} ${parts.length >= 3 ? `${parts[1].charAt(0)}. ${parts[2].charAt(0)}.` : `${parts[1].charAt(0)}.`}`);
      }
    }
    for (const text of variants) {
      const key = text.toLowerCase();
      if (!text || seen.has(key) || text.length < 3) continue;
      seen.add(key);
      const escaped = text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
      patterns.push({
        re: new RegExp(`(?<![\\wА-Яа-яЁё])${escaped}(?![\\wА-Яа-яЁё])`, "giu"),
        name: full,
        len: text.length,
      });
    }
  }
  patterns.sort((a, b) => b.len - a.len);
  return patterns;
}

function linkifyAiPersonNames(html) {
  const patterns = buildAiPersonLinkPatterns();
  if (!patterns.length) return html;
  let issueBtnDepth = 0;
  return String(html || "").replace(/(<[^>]+>)|([^<]+)/g, (whole, tag, text) => {
    if (tag) {
      if (/^<button\b/i.test(tag) && /\b(?:issue-key-btn|ai-issue-link)\b/i.test(tag)) {
        issueBtnDepth += 1;
      } else if (/^<\/button\s*>/i.test(tag) && issueBtnDepth > 0) {
        issueBtnDepth -= 1;
      }
      return tag;
    }
    if (issueBtnDepth > 0 || text == null) return whole;
    let out = text;
    const slots = [];
    for (const { re, name } of patterns) {
      out = out.replace(re, () => {
        const token = `\u0000AI_PERSON_${slots.length}\u0000`;
        const label = escapeHtml(shortGivenSurname(name));
        slots.push(
          `<button type="button" class="ai-person-link person-btn" data-person="${escapeHtml(name)}" title="Открыть профиль">${label}</button>`
        );
        return token;
      });
    }
    return out.replace(/\u0000AI_PERSON_(\d+)\u0000/g, (_, idx) => slots[Number(idx)] || "");
  });
}

function renderAiMarkdown(markdown) {
  const lines = stripRedundantIssueTitleParens(humanizeAiText(markdown || ""))
    .replace(/\r\n/g, "\n")
    .split("\n");
  const parts = [];
  let listType = null; // "ul" | "ol"
  let listItems = [];
  const flushList = () => {
    if (!listItems.length || !listType) return;
    const tag = listType;
    parts.push(
      `<${tag} class="ai-brief-list">${listItems
        .map((item) => `<li>${item}</li>`)
        .join("")}</${tag}>`
    );
    listItems = [];
    listType = null;
  };
  const pushListItem = (type, html) => {
    if (listType && listType !== type) flushList();
    listType = type;
    listItems.push(html);
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    const trimmed = line.trim();
    if (!trimmed) {
      flushList();
      continue;
    }
    const heading = trimmed.match(/^##\s+(.+)$/);
    if (heading) {
      flushList();
      parts.push(
        `<h3 class="ai-brief-h">${formatAiInlineMarkdown(heading[1].trim())}</h3>`
      );
      continue;
    }
    const ordered = trimmed.match(/^\d+\.\s+(.+)$/);
    if (ordered) {
      pushListItem("ol", formatAiInlineMarkdown(ordered[1].trim()));
      continue;
    }
    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      pushListItem("ul", formatAiInlineMarkdown(bullet[1].trim()));
      continue;
    }
    flushList();
    parts.push(`<p class="ai-brief-p">${formatAiInlineMarkdown(trimmed)}</p>`);
  }
  flushList();
  return parts.join("") || `<p class="muted">Пустой ответ модели</p>`;
}

function openAiBriefModal(brief) {
  if (!brief) return;
  const author = corporateAuthorLabel(brief);
  const when = brief.generated_at ? fmtDateTime(brief.generated_at) : "—";
  const title = brief.reason === "reused" ? "Прошлая AI-оценка спринта" : "AI-оценка спринта";
  if (brief.status === "ok" && brief.markdown) {
    openAppModal(
      `
      <div class="release-modal-head">
        <h2 id="app-modal-title">${escapeHtml(title)}</h2>
        <p class="muted">Автор: ${escapeHtml(author)} · ${escapeHtml(when)}${brief.reason === "reused" ? " · переиспользована" : ""}</p>
      </div>
      <div class="ai-brief-body">${renderAiMarkdown(brief.markdown)}</div>
      `,
      { wide: true, type: "ai_brief" }
    );
    return;
  }
  const detail =
    brief.status === "error"
      ? brief.error || "Ошибка генерации"
      : brief.reason
        ? `Пропущено: ${brief.reason}`
        : "Оценка недоступна";
  openAppModal(
    `
    <div class="release-modal-head">
      <h2 id="app-modal-title">${escapeHtml(title)}</h2>
      <p class="muted">Автор: ${escapeHtml(author)}</p>
    </div>
    <p class="muted">${escapeHtml(detail)}</p>
    `,
    { wide: true, type: "ai_brief" }
  );
}

function renderSprintHeader(sprint, mood, aiBrief = null) {
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
      <p class="mood-score">${fmtNumber(mood.score)}<span class="mood-score-unit">/100</span></p>
      <p class="mood-reco">${escapeHtml(mood.recommendation || "—")}</p>
    </article>`
    : "";

  let aiCard = "";
  if (aiBrief) {
    const author = corporateAuthorLabel(aiBrief);
    const tone =
      aiBrief.status === "ok"
        ? "tone-good"
        : aiBrief.status === "error"
          ? "tone-warn"
          : "tone-ok";
    const verdict =
      humanizeAiText(aiBrief.verdict || "") ||
      extractAiVerdict(aiBrief.markdown) ||
      (aiBrief.status === "error"
        ? "Оценка временно недоступна"
        : aiBrief.status === "skipped"
          ? "Оценка не сформирована"
          : "Открыть полный анализ");
    const when = aiBrief.generated_at ? fmtRelativeAgo(aiBrief.generated_at) : "—";
    const reused = aiBrief.reason === "reused";
    aiCard = `
    <article class="card mood-card ai-brief-card ${tone}" data-ai-brief="1" role="button" tabindex="0" title="Открыть AI-оценку спринта">
      <p class="label">${reused ? "Прошлая AI-оценка спринта" : "AI-оценка спринта"}</p>
      <p class="ai-brief-author">${escapeHtml(author)}</p>
      <p class="mood-reco ai-inline">${formatAiInlineMarkdown(verdict)}</p>
      <p class="hint">${reused ? "переиспользована · " : ""}${escapeHtml(when)}</p>
    </article>`;
  }

  root.innerHTML = cards + moodCard + aiCard;
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
        "Только эпики релизного окна · прогресс по полному объёму эпика",
        "В блоке показаны эпики, связанные с версиями релизного окна (спринт + 2 недели). Остальные эпики спринта сюда не попадают. Прогресс секций — по всем задачам эпика, не только спринта."
      );
    } else if (focus === "release_window_sprint") {
      note.innerHTML = tip(
        "Только эпики релизного окна · по задачам текущего спринта",
        "В блоке — эпики релизного окна; полный объём временно недоступен, считаем по задачам спринта команды."
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

  const sorted = sortEpicsForDisplay(epics);
  const withRelease = sorted.filter((e) => e.has_release || (e.releases || []).length);
  const withoutRelease = sorted.filter((e) => !(e.has_release || (e.releases || []).length));
  const groups = [
    { title: "С релизом", items: withRelease },
    { title: "Без релиза", items: withoutRelease },
  ].filter((g) => g.items.length);
  const sortNote =
    "Сначала ближайшая дата релиза, затем сила связи по задачам, затем меньший прогресс";

  root.innerHTML = groups
    .map(
      (g) => `
      <div class="epic-group">
        <h3 class="epic-group-title">${escapeHtml(g.title)} · ${fmtNumber(g.items.length)}</h3>
        <p class="muted epic-group-note">${escapeHtml(sortNote)}</p>
        <div class="epic-rows">${g.items.map(epicTimelineCardHtml).join("")}</div>
      </div>`
    )
    .join("");
}

function epicNearestReleaseDate(epic) {
  if (epic?.nearest_release_date) return String(epic.nearest_release_date).slice(0, 10);
  const dates = [];
  for (const r of epic?.releases || []) {
    if (r?.released) continue;
    const raw = String(r?.release_date || "").trim();
    if (raw) dates.push(raw.slice(0, 10));
  }
  if (!dates.length) {
    for (const r of epic?.releases || []) {
      const raw = String(r?.release_date || "").trim();
      if (raw) dates.push(raw.slice(0, 10));
    }
  }
  return dates.length ? dates.sort()[0] : "9999-12-31";
}

function epicReleaseLinkStrength(epic) {
  if (epic?.release_link_strength != null && epic.release_link_strength !== "") {
    return Number(epic.release_link_strength) || 0;
  }
  // Fallback for older snapshots without the field.
  const releases = epic?.releases || [];
  if (!releases.length) return 0;
  return Math.min(1, 0.35 + releases.length * 0.2);
}

function sortEpicsForDisplay(epics) {
  return [...(epics || [])].sort((a, b) => {
    const aRel = a.has_release || (a.releases || []).length ? 0 : 1;
    const bRel = b.has_release || (b.releases || []).length ? 0 : 1;
    if (aRel !== bRel) return aRel - bRel;
    const aDate = epicNearestReleaseDate(a);
    const bDate = epicNearestReleaseDate(b);
    if (aDate !== bDate) return aDate.localeCompare(bDate);
    const aStr = epicReleaseLinkStrength(a);
    const bStr = epicReleaseLinkStrength(b);
    if (aStr !== bStr) return bStr - aStr;
    const aProg = Number(a.progress_pct) || 0;
    const bProg = Number(b.progress_pct) || 0;
    if (aProg !== bProg) return aProg - bProg;
    return String(a.key || "").localeCompare(String(b.key || ""));
  });
}

function epicTimelineCardHtml(epic) {
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
  const releaseChips = (epic.releases || [])
    .map((r) => {
      const name = (r.name || "").trim();
      if (!name) return "";
      const rid = r.id != null && r.id !== "" ? String(r.id) : "";
      if (rid) {
        return `<button type="button" class="mini-chip epic-release-chip" data-release-id="${escapeHtml(rid)}" data-release-name="${escapeHtml(name)}" title="Открыть релиз">${escapeHtml(name)}</button>`;
      }
      return `<span class="mini-chip">${escapeHtml(name)}</span>`;
    })
    .filter(Boolean)
    .join("");
  const ageChip = Number.isFinite(Number(epic.age_days))
    ? `<span class="mini-chip epic-age-chip">Тянется ${fmtNumber(epic.age_days)} дн.</span>`
    : "";
  const epicTitle = epic.summary
    ? `<div class="task-block"><div class="task-inline">${issueLink(epic)}<span class="task-summary epic-title-text">${escapeHtml(epic.summary)}</span></div></div>`
    : taskTitle(epic, { showTags: false });
  return `
      <article class="epic-row" data-epic-key="${escapeHtml(epic.key)}" role="button" tabindex="0">
        <div class="epic-row-top">
          <div class="epic-row-meta">${epicTitle}</div>
          <div class="epic-row-top-right">
            ${releaseChips}
            ${ageChip}
          </div>
        </div>
        <div class="epic-track-wrap" style="width:${Number(epic.bar_width_pct) || 64}%">
          <div class="epic-sausage">${sections}</div>
          <div class="epic-captions">${captions}</div>
        </div>
      </article>`;
}

function epicCardForDisplay(epic) {
  if (!epic) return null;
  if ((epic.sections || []).length) return epic;
  const resolved = resolveEpicForModal(epic.key);
  if (!resolved) return epic;
  return {
    ...epic,
    ...resolved,
    summary: epic.summary || resolved.summary,
    bar_width_pct: epic.bar_width_pct || resolved.bar_width_pct || 64,
  };
}

function directionColorForEpic(name) {
  const legend = currentSprintReport?.epic_timeline?.legend || [];
  const fromLegend = legend.find((item) => item?.name === name);
  if (fromLegend?.color) return fromLegend.color;
  return DIRECTION_ICONS[name]?.color || "#8a968c";
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

  const pushTask = (t, dirName) => {
    if (!t || typeof t !== "object") return;
    const tKey = String(t.key || "").trim().toUpperCase();
    if (!tKey) return;
    const direction = dirName || t.direction || "—";
    const dedupe = `${direction}:${tKey}`;
    if (seenTaskKeys.has(dedupe)) return;
    seenTaskKeys.add(dedupe);
    if (!tasksByDir.has(direction)) tasksByDir.set(direction, []);
    tasksByDir.get(direction).push({
      ...t,
      key: tKey,
      direction,
      // Keep hidden_from_display tasks — they still shape the epic sausage.
      direction_state: t.direction_state || (t.done || t.jira_done ? "done" : "active"),
    });
  };

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
      pushTask(t, dirName);
    }
  }

  // People profiles may hold tasks missing from direction lists
  // (including display_task_filters / hidden_from_display).
  for (const profile of Object.values(sr.people || {})) {
    const dirName = profile.direction || "—";
    for (const t of [...(profile.tasks_active || []), ...(profile.tasks || [])]) {
      if (String(t.epic_key || "") !== key) continue;
      pushTask(t, dirName);
    }
  }

  // Issues dossier is another source for hidden/auxiliary tasks.
  for (const issue of Object.values(sr.issues || {})) {
    if (String(issue?.epic_key || "") !== key) continue;
    pushTask(issue, issue.direction || "—");
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
    const doneFromDetail = detail.filter((t) => t.direction_state === "done").length;
    const total = Number(row.total);
    const done = Number(row.done);
    const tasksCount = Number.isFinite(total) && total > 0 ? total : detail.length || 1;
    const doneCount = Number.isFinite(done) ? done : doneFromDetail;
    const closed =
      tasksCount > 0 ? Math.round((doneCount / tasksCount) * 100) : Number(row.progress_pct) || 0;
    return {
      direction,
      color: row.color || directionColorForEpic(direction),
      progress_pct: closed,
      closed_pct: closed,
      tasks: tasksCount,
      done_tasks: doneCount,
      active_tasks: row.open ?? openDetail.length,
      open_tasks: openDetail.length,
      estimate_hours:
        row.estimate_hours ??
        (detail.reduce((s, t) => s + (Number(t.estimate_hours) || 0), 0) || 1),
      spent_hours:
        row.hours ?? detail.reduce((s, t) => s + (Number(t.hours) || 0), 0),
      flex_grow: row.estimate_hours || 1,
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
        .map((t) =>
          releaseTaskRowHtml(t, {
            showReleaseTags: true,
            outOfSprint: t.in_sprint === false,
          })
        )
        .join("");
      return `
        <div class="person-modal-section">
          <h3>${escapeHtml(s.direction)} · ${fmtNumber(s.progress_pct)}% · открыто ${fmtNumber(s.open_tasks ?? openTasks.length)}</h3>
          <div class="progress" style="margin:0.35rem 0 0.55rem"><div class="progress-bar" style="width:${Math.min(s.progress_pct || 0, 100)}%"></div></div>
          <div class="person-task-list release-task-list">${tasks}</div>
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
    { wide: true, release: true, type: "epic" }
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
      const people = (d.members || [])
        .map((m) => {
          const profile = findPersonByName(m.name);
          const load = profile?.load || m.load || null;
          const avatarUrl =
            teamAvatarOverride(profile?.name || m.name) || m.avatar_url || profile?.avatar_url || null;
          return `
          <button type="button" class="dir-person-mini person-btn" data-person="${escapeHtml(profile?.name || m.name)}" title="${escapeHtml(m.name)}">
            ${avatarWithLoad(avatarImgHtml(m.name, avatarUrl), load, { size: "sm" })}
            <span class="dir-person-mini-name">${escapeHtml(shortGivenSurname(m.name))}</span>
          </button>`;
        })
        .join("");
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
        <div class="dir-people-mini">${people || `<span class="muted">Нет сотрудников</span>`}</div>
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

  const stabilityPct = (value) => {
    if (value === null || value === undefined) return "—";
    const number = Number(value);
    return Number.isFinite(number) ? `${number.toFixed(1)}%` : "—";
  };
  const compactDetail = (category, person) => {
    if (category.id !== "stability" || !person.stability) {
      return person.detail || person.direction || "";
    }
    return `темп ${stabilityPct(person.stability.pace_pct)} · вовремя ${stabilityPct(person.stability.timely_pct)}`;
  };

  root.innerHTML = ordered
    .map((cat) => {
      const people = cat.people || [];
      const allCount = (cat.all_people || people).length;
      const list =
        people.length === 0
          ? `<p class="muted">Пока нет данных</p>`
          : `<div class="rating-list">${people
              .map((p) => {
                const detail = compactDetail(cat, p);
                return `
              <div class="rating-person">
                ${medalHtml(p.place)}
                ${personCell(p.name, p.avatar_url, { short: true, load: p.load || findPersonByName(p.name)?.load })}
                <div class="rating-score">
                  <div class="rating-score-main">${escapeHtml(p.value || "")}</div>
                  <div class="muted rating-score-detail">${escapeHtml(detail)}</div>
                </div>
              </div>`;
              })
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

function releaseCardHtml(r, { showDate = false } = {}) {
  const desc = String(r.description || "").trim();
  const descHtml = desc ? `<p class="release-desc">${escapeHtml(desc)}</p>` : "";
  const dateBadge = showDate
    ? `<span class="release-date-badge">${fmtDay(r.release_date)}</span>`
    : "";
  return `
        <article class="release-card release-card-compact" data-risk="${escapeHtml(r.risk || "")}" data-released="${r.released ? "1" : "0"}" data-release-id="${escapeHtml(r.id)}" role="button" tabindex="0">
          <div class="release-title-row">
            ${dateBadge}
            <h3 class="release-title">${escapeHtml(r.name)}</h3>
            ${releaseStatusBadge(r)}
          </div>
          ${descHtml}
          <div class="release-progress">
            <div class="progress">
              <div class="progress-bar" style="width:${Math.min(Number(r.progress_pct) || 0, 100)}%"></div>
            </div>
          </div>
        </article>`;
}

function releaseDateGroupKey(release) {
  const raw = String(release?.release_date || "").trim();
  if (!raw) return "undated";
  return raw.slice(0, 10);
}

function groupReleasesByDate(releases) {
  const map = new Map();
  for (const r of releases || []) {
    const key = releaseDateGroupKey(r);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(r);
  }
  const keys = [...map.keys()].sort((a, b) => {
    if (a === "undated") return 1;
    if (b === "undated") return -1;
    return a.localeCompare(b);
  });
  return keys.map((key) => ({
    key,
    date: key === "undated" ? null : key,
    label: key === "undated" ? "Без даты" : fmtDay(key),
    releases: map.get(key) || [],
  }));
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
  const groups = groupReleasesByDate(items);
  if (mini) {
    mini.innerHTML = groups
      .map(
        (g) =>
          `<span class="mini-chip"><strong>${escapeHtml(g.label)}</strong> · ${fmtNumber(g.releases.length)} рел.</span>`
      )
      .join("");
  }

  root.innerHTML = groups
    .map((g) => {
      const cards = g.releases.map((r) => releaseCardHtml(r)).join("");
      return `
      <div class="release-date-group" data-release-date="${escapeHtml(g.key)}">
        <div class="release-date-group-head">
          <h3 class="release-date-group-title">${escapeHtml(g.label)}</h3>
          <span class="release-date-group-count">${fmtNumber(g.releases.length)}</span>
        </div>
        <div class="release-date-group-cards">${cards}</div>
      </div>`;
    })
    .join("");
}

const MODAL_BASE_Z = 40;
let modalLayerSeq = 0;

function getModalsRoot() {
  return document.getElementById("app-modals");
}

function reindexModalLayers(root) {
  const layers = root ? root.querySelectorAll(".modal") : [];
  layers.forEach((el, idx) => {
    el.style.zIndex = String(MODAL_BASE_Z + idx);
    el.classList.toggle("is-top-modal", idx === layers.length - 1);
  });
}

function syncBodyScrollLock() {
  const root = getModalsRoot();
  const open = !!(root && root.querySelector(".modal"));
  document.body.classList.toggle("modal-open", open);
}

function closeAppModal() {
  const root = getModalsRoot();
  if (!root) return;
  const layers = root.querySelectorAll(".modal");
  if (!layers.length) return;
  const top = layers[layers.length - 1];
  if (top.dataset.modalType === "search") clearReportSearchState();
  top.remove();
  reindexModalLayers(root);
  syncBodyScrollLock();
  hideFloatTip();
}

function openAppModal(
  html,
  { wide = false, rating = false, person = false, release = false, type = null } = {}
) {
  const root = getModalsRoot();
  if (!root) return;
  const modalType =
    type ||
    (person ? "person" : release ? "release" : rating ? "rating" : "generic");

  // Only one modal of each type at a time — replace existing of same type.
  root.querySelectorAll(`.modal[data-modal-type="${modalType}"]`).forEach((el) => {
    el.remove();
  });

  modalLayerSeq += 1;
  const titleId = `app-modal-title-${modalLayerSeq}`;
  const bodyHtml = String(html || "").replace(
    /id="app-modal-title"/g,
    `id="${titleId}"`
  );

  const layer = document.createElement("div");
  layer.className = "modal";
  layer.dataset.modalType = modalType;
  layer.setAttribute("aria-hidden", "false");
  // Build shell first, then inject body via DOM API so nested HTML can't break the card.
  layer.innerHTML = `
    <div class="modal-backdrop" data-modal-close></div>
    <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="${titleId}">
      <button type="button" class="modal-close" data-modal-close aria-label="Закрыть">×</button>
      <div class="app-modal-body"></div>
    </div>
  `;

  const card = layer.querySelector(".modal-card");
  const body = layer.querySelector(".app-modal-body");
  if (body) body.innerHTML = bodyHtml;
  if (card) {
    card.classList.toggle("modal-card-wide", !!wide || !!person || !!release || modalType === "search");
    card.classList.toggle("modal-card-person", !!person);
    card.classList.toggle("modal-card-release", !!release);
    card.classList.toggle("modal-card-rating", !!rating);
    card.classList.toggle("modal-card-search", modalType === "search");
    card.scrollTop = 0;
  }
  root.appendChild(layer);
  reindexModalLayers(root);
  syncBodyScrollLock();

  requestAnimationFrame(() => {
    if (card) card.scrollTop = 0;
    if (body) body.scrollTop = 0;
  });
}

function personAiNoteFor(profile) {
  if (!profile) return null;
  if (profile.ai_note?.text) return profile.ai_note;
  const bundle = currentSprintReport?.ai_person_notes;
  const fromBundle = bundle?.notes?.[profile.name];
  if (fromBundle?.text) return fromBundle;
  return null;
}

function jiraBrowseBase() {
  const explicit =
    currentSprintReport?.jira_browse_base ||
    currentSprintReport?.settings?.jira_browse_base ||
    null;
  if (explicit) return String(explicit).replace(/\/+$/, "");

  const collect = [];
  const push = (url) => {
    if (url) collect.push(String(url));
  };
  const issues = currentSprintReport?.issues || {};
  for (const issue of Object.values(issues)) push(issue?.web_url);
  for (const rel of currentSprintReport?.releases || []) {
    push(rel?.web_url);
    for (const t of rel?.tasks || []) push(t?.web_url);
    for (const s of rel?.sections || []) {
      for (const t of s?.tasks_detail || []) push(t?.web_url);
    }
  }
  const risks = currentSprintReport?.risks || {};
  for (const list of Object.values(risks)) {
    if (!Array.isArray(list)) continue;
    for (const item of list) push(item?.web_url);
  }
  for (const person of currentSprintReport?.team || []) {
    push(person?.jira_url);
  }
  for (const url of collect) {
    const browse = String(url).match(/^(https?:\/\/[^/?#]+)\/browse\//i);
    if (browse) return browse[1];
    const host = String(url).match(/^(https?:\/\/[^/?#]+)/i);
    if (host && /jira|atlassian/i.test(host[1])) return host[1];
  }
  return null;
}

function jiraBrowseUrlForKey(issueKey, fallbackUrl = null) {
  const key = String(issueKey || "").trim().toUpperCase();
  if (!key && !fallbackUrl) return null;
  if (fallbackUrl) return String(fallbackUrl);
  const known = key ? currentSprintReport?.issues?.[key] : null;
  if (known?.web_url) return known.web_url;
  // Release-only tasks often aren't in issues{} — still have web_url on the row.
  for (const rel of currentSprintReport?.releases || []) {
    for (const t of rel?.tasks || []) {
      if (String(t?.key || "").toUpperCase() === key && t?.web_url) return t.web_url;
    }
    for (const s of rel?.sections || []) {
      for (const t of s?.tasks_detail || []) {
        if (String(t?.key || "").toUpperCase() === key && t?.web_url) return t.web_url;
      }
    }
  }
  const base = jiraBrowseBase();
  return key && base ? `${base}/browse/${encodeURIComponent(key)}` : null;
}

/** Turn Jira keys in already-escaped HTML/text into issue modal buttons. */
function linkifyIssueKeysInHtml(html) {
  let issueBtnDepth = 0;
  return String(html || "").replace(/(<[^>]+>)|([^<]+)/g, (whole, tag, text) => {
    if (tag) {
      if (/^<button\b/i.test(tag) && /\b(?:issue-key-btn|ai-issue-link)\b/i.test(tag)) {
        issueBtnDepth += 1;
      } else if (/^<\/button\s*>/i.test(tag) && issueBtnDepth > 0) {
        issueBtnDepth -= 1;
      }
      return tag;
    }
    if (issueBtnDepth > 0 || text == null) return whole;
    return text.replace(/\b([A-Z][A-Z0-9]+-\d+)\b/g, (key) => issueKeyButtonHtml(key));
  });
}

function renderPersonAiNoteBody(text) {
  const raw = String(text || "").trim();
  if (!raw) return "";
  // Multi-line / lists → same renderer as AI brief; otherwise inline markdown.
  if (/\n/.test(raw) || /^\s*[-*]\s|\d+\.\s|##\s/m.test(raw)) {
    return `<div class="person-ai-note-body">${renderAiMarkdown(raw)}</div>`;
  }
  return `<p class="person-ai-note-text">${formatAiInlineMarkdown(raw)}</p>`;
}

function renderPersonAiNote(profile) {
  const note = personAiNoteFor(profile);
  if (!note?.text) return "";
  const bundle = currentSprintReport?.ai_person_notes || {};
  const author = bundle.author || corporateAuthorLabel(bundle) || "corporate";
  const when = bundle.generated_at ? fmtRelativeAgo(bundle.generated_at) : "";
  const tone = ["attention", "ok", "info"].includes(note.tone) ? note.tone : "ok";
  const reused = bundle.reason === "reused";
  const meta = [reused ? "переиспользована" : "", author, when].filter(Boolean).join(" · ");
  return `
    <div class="person-ai-note tone-${escapeHtml(tone)}">
      <div class="person-ai-note-head">
        <span class="person-ai-note-label">${reused ? "Прошлая AI-заметка" : "AI-заметка"}</span>
        ${meta ? `<span class="person-ai-note-meta">${escapeHtml(meta)}</span>` : ""}
      </div>
      ${renderPersonAiNoteBody(note.text)}
    </div>`;
}

function openPersonModal(name) {
  const profile = currentSprintReport?.people?.[name];
  if (!profile) {
    openAppModal(
      `<h2 id="app-modal-title">Сотрудник</h2><p class="muted">Нет данных по «${escapeHtml(name)}» в текущем спринте.</p>`,
      { type: "person" }
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
      const isToday = !!d.is_today;
      const isFuture = !!d.is_future;
      const remain = isWeekend || isFuture ? 0 : Math.max(0, expected - h);
      const spentPx = Math.round((h / maxH) * chartH);
      const remainPx = isWeekend || isFuture ? 0 : Math.round((remain / maxH) * chartH);
      const over = !isWeekend && !isFuture && h > expected + 0.05;
      const barCls = isFuture
        ? "is-future"
        : h <= 0
          ? "is-zero"
          : over
            ? "is-over"
            : h + 0.05 < expected && !isWeekend
              ? "is-low"
              : "is-ok";
      const issueLines = (d.issues || [])
        .map((issue) => {
          const summary = String(issue.summary || "").trim();
          const label = summary ? `${issue.key}: ${summary}` : issue.key;
          return `${label} — ${fmtDuration(issue.hours)}`;
        })
        .join("\n");
      let head;
      if (isFuture) {
        head = `${wd} ${fmtDay(d.date)}: ещё не наступил`;
      } else if (isWeekend) {
        head = `${wd} ${fmtDay(d.date)}: списано ${fmtDuration(h)} (выходной)`;
      } else {
        head = `${wd} ${fmtDay(d.date)}: списано ${fmtDuration(h)}, до нормы ${fmtDuration(remain)}`;
      }
      if (isToday) head += " · сегодня";
      const tipText = issueLines
        ? `${head}\n${issueLines}`
        : h > 0
          ? `${head}\nНет разбивки по задачам`
          : head;
      const tipAttr = escapeHtml(tipText).replaceAll("\n", "&#10;");
      const colCls = [
        "person-hours-col",
        "has-tip",
        isToday ? "is-today" : "",
        isFuture ? "is-future-day" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const footer = isFuture
        ? "—"
        : isWeekend
          ? "вых."
          : `ост ${fmtDuration(remain)}`;
      return `
        <div class="${colCls}" tabindex="0" data-tip="${tipAttr}">
          <div class="person-hours-bar-wrap" style="height:${chartH}px">
            <div class="person-hours-stack">
              ${remainPx > 0 ? `<div class="person-hours-remain" style="height:${remainPx}px"></div>` : ""}
              <div class="person-hours-bar ${barCls}" style="height:${Math.max(h > 0 ? 6 : 3, spentPx)}px"></div>
            </div>
          </div>
          <div class="person-hours-label">
            <strong>${escapeHtml(wd)}</strong>
            <span class="person-hours-spent-val">${isFuture ? "—" : fmtDuration(h)}</span>
            <span>${footer}</span>
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

  const activeTasksHtml = renderPersonActiveTasks(
    profile.tasks_active || profile.tasks || []
  );

  const activityHtml = renderPersonActivity(profile.activity);
  const loadHtml = renderPersonLoad(profile.load);

  const avatarImg = avatarImgHtml(
    profile.name,
    teamAvatarOverride(profile.name) || profile.avatar_url,
    { cls: "person-modal-avatar" }
  );
  const avatar = avatarWithLoad(avatarImg, profile.load, { size: "lg" });
  const profileLinks = [
    profile.jira_url
      ? `<a class="person-profile-btn" href="${escapeHtml(profile.jira_url)}" target="_blank" rel="noreferrer">Jira</a>`
      : "",
    profile.gitlab_url
      ? `<a class="person-profile-btn" href="${escapeHtml(profile.gitlab_url)}" target="_blank" rel="noreferrer">GitLab</a>`
      : "",
  ]
    .filter(Boolean)
    .join("");
  const profileLinksHtml = profileLinks
    ? `<div class="person-profile-links">${profileLinks}</div>`
    : "";

  const aiNoteHtml = renderPersonAiNote(profile);

  openAppModal(
    `
    <div class="person-modal-head">
      ${avatar}
      <div>
        <h2 id="app-modal-title">${escapeHtml(profile.name)}</h2>
        <p class="muted">${escapeHtml(profile.direction || "")}</p>
        ${profileLinksHtml}
      </div>
    </div>
    <div class="person-modal-stats">
      <div class="person-stat"><span class="label">${tip("Активные", "Задачи в active-статусе направления")}</span><span class="value">${fmtNumber(profile.tasks_open)}</span></div>
      <div class="person-stat"><span class="label">${tip("Закрыто / всего", "Закрытые по правилам направления / все задачи сотрудника в спринте")}</span><span class="value">${fmtNumber(profile.tasks_done)}/${fmtNumber(profile.tasks_total)}</span></div>
      <div class="person-stat"><span class="label">${tip("Часы спринта", "Сумма worklog за дни спринта")}</span><span class="value">${fmtDuration(profile.hours_sprint)}</span></div>
      <div class="person-stat"><span class="label">${tip("MR", "Merge request’ы, связанные с задачами сотрудника")}</span><span class="value">${fmtNumber(profile.mr_count)}</span></div>
    </div>
    ${aiNoteHtml}
    ${loadHtml}
    ${activityHtml}
    <div class="person-modal-section">
      <h3>Списания по дням</h3>
      <p class="muted person-hours-legend">Столбик: списано · сверху до нормы — остаток · норма ${fmtDuration(expected)} · подсвечен сегодня</p>
      <div class="person-hours-chart">${chart || `<span class="muted">Нет списаний</span>`}</div>
    </div>
    <div class="person-modal-section">
      <h3>Рейтинги</h3>
      <div class="person-rating-chips">${ratings || `<span class="muted">Пока нет попаданий в топы</span>`}</div>
    </div>
    <div class="person-modal-section">
      <h3>Активные задачи</h3>
      ${activeTasksHtml}
    </div>`,
    { person: true, type: "person" }
  );
}

function findPersonByName(name) {
  const people = currentSprintReport?.people || {};
  if (!name) return null;
  if (people[name]) return people[name];
  const target = String(name).trim().toLowerCase();
  for (const [key, profile] of Object.entries(people)) {
    if (key.toLowerCase() === target) return profile;
  }
  return null;
}

function activityStatusLabel(status) {
  const raw = String(status || "").trim();
  const key = raw.toLowerCase();
  if (!key) return "—";
  // Backlog / To Do family (Jira often stores EN "To Do", board shows «Сделать»)
  if (
    key === "сделать" ||
    key === "to do" ||
    key === "todo" ||
    key === "open" ||
    key === "backlog" ||
    key === "новая" ||
    key === "новая (оценено)" ||
    key === "ready for development" ||
    key.startsWith("to do")
  )
    return "Сделать";
  if (key.includes("ready for testing")) return "Тест";
  if (key === "tested" || key.includes("tested")) return "Tested";
  if (key.includes("тест") || key === "testing" || key === "тестирование") return "Тест";
  if (key === "verify" || key === "verified") return "Verify";
  if (key.includes("review") || key.includes("ревью")) return "Ревью";
  if (key === "developed" || key === "разработка") return "Developed";
  if (
    key === "closed" ||
    key === "done" ||
    key === "готово" ||
    key === "закрыт" ||
    key === "закрыта" ||
    key === "resolved" ||
    key.startsWith("closed") ||
    key.startsWith("done")
  )
    return "Закрыта";
  if (key === "отменено" || key === "cancelled" || key === "canceled") return "Отменено";
  if (key.includes("in progress") || key.includes("в работе")) return "В работе";
  if (key.includes("hold")) return "Hold";
  return raw;
}

function activityDestChip(ev) {
  const type = ev.type || "";
  const toAssignee = ev.to_assignee;
  const fromAssignee = ev.from_assignee;
  const reassigned =
    toAssignee && fromAssignee && String(toAssignee) !== String(fromAssignee);

  if (type === "progress") {
    const verb = ev.action || "Списал";
    const dur = ev.hours != null ? fmtDuration(ev.hours) : "";
    return `<span class="act-chip act-chip-hours">${escapeHtml(dur ? `${verb} ${dur}` : verb)}</span>`;
  }

  if ((type === "handed_off" || type === "to_review") && reassigned) {
    return activityPersonChip(toAssignee);
  }
  if (type === "handed_off") {
    const label = activityStatusLabel(ev.to_status) || "Дальше";
    return `<span class="act-chip act-chip-status tone-hand">${escapeHtml(label)}</span>`;
  }
  if (type === "to_review") {
    return `<span class="act-chip act-chip-status tone-review">Ревью</span>`;
  }
  if (type === "closed") {
    return `<span class="act-chip act-chip-status tone-done">Закрыта</span>`;
  }
  if (type === "started") {
    return `<span class="act-chip act-chip-status tone-start">В работе</span>`;
  }
  if (type === "received") {
    if (fromAssignee && String(fromAssignee) !== String(toAssignee || "")) {
      return activityPersonChip(fromAssignee, { prefix: "от" });
    }
    return `<span class="act-chip act-chip-status tone-recv">На мне</span>`;
  }
  if (ev.to_status) {
    return `<span class="act-chip act-chip-status">${escapeHtml(activityStatusLabel(ev.to_status))}</span>`;
  }
  return `<span class="act-chip act-chip-status">${escapeHtml(ev.text || "—")}</span>`;
}

function activityPersonChip(name, { prefix = "", withLoad = false } = {}) {
  const profile = findPersonByName(name);
  const label = shortGivenSurname(name);
  const tipName = escapeHtml(name || label);
  const key = profile?.name || name;
  const canOpen = !!(key && currentSprintReport?.people?.[key]);
  const cls = `act-chip act-chip-person has-tip${canOpen ? " person-btn" : ""}`;
  const tag = canOpen ? "button" : "span";
  const typeAttr = canOpen ? ` type="button"` : "";
  const dataAttr = canOpen ? ` data-person="${escapeHtml(key)}"` : "";
  const prefixHtml = prefix
    ? `<span class="act-chip-prefix">${escapeHtml(prefix)}</span>`
    : "";
  const avatar = avatarWithLoad(
    avatarImgHtml(name, teamAvatarOverride(key) || profile?.avatar_url, { cls: "act-chip-avatar" }),
    withLoad ? profile?.load || null : null,
    { size: "xs" }
  );
  return `<${tag}${typeAttr} class="${cls}"${dataAttr} tabindex="0" data-tip="${tipName}">${prefixHtml}${avatar}<span class="act-chip-name person-name">${escapeHtml(label)}</span></${tag}>`;
}

function activityTaskCard(ev) {
  const issue = {
    key: ev.issue_key || ev.key,
    web_url: ev.web_url,
    summary: ev.summary,
  };
  const summary = ev.summary
    ? `<span class="act-task-summary">${escapeHtml(ev.summary)}</span>`
    : "";
  return `<div class="act-task">${issueLink(issue)}${summary}</div>`;
}

function renderActivityEvent(ev) {
  if (!ev) return "";
  if (ev.type === "more") {
    return `<div class="person-activity-row is-more"><span class="muted">${escapeHtml(ev.text || "")}</span></div>`;
  }

  const type = ev.type || "other";
  const task = activityTaskCard(ev);
  const dest = activityDestChip(ev);

  const action = ev.action
    ? `<span class="act-verb">${escapeHtml(ev.action)}</span>`
    : "";

  // received: «Марк → задача» (кто передал → что пришло)
  if (type === "received" && ev.from_assignee && ev.from_assignee !== ev.to_assignee) {
    return `
      <div class="person-activity-row type-${escapeHtml(type)}">
        <div class="act-flow act-flow-incoming">
          <div class="act-flow-end">
            ${activityPersonChip(ev.from_assignee)}
            <span class="act-arrow" aria-hidden="true">→</span>
          </div>
          ${task}
        </div>
      </div>`;
  }

  if (type === "progress") {
    return `
      <div class="person-activity-row type-${escapeHtml(type)}">
        <div class="act-flow">
          ${task}
          <div class="act-flow-end">${dest}</div>
        </div>
      </div>`;
  }

  return `
    <div class="person-activity-row type-${escapeHtml(type)}">
      <div class="act-flow">
        ${task}
        <div class="act-flow-end">
          <span class="act-arrow" aria-hidden="true">${action ? `${action} →` : "→"}</span>
          ${dest}
        </div>
      </div>
    </div>`;
}

function renderActivityGroups(block) {
  const order = ["movement", "incoming", "progress"];
  const groups = [...(block.groups || [])].sort((a, b) => {
    const ai = order.indexOf(a.id);
    const bi = order.indexOf(b.id);
    return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
  });
  if (groups.length) {
    return groups
      .map((group) => {
        const rows = (group.events || []).map(renderActivityEvent).join("");
        if (!rows) return "";
        return `
          <div class="person-activity-group">
            <h4 class="person-activity-group-title">${escapeHtml(group.label || "")}</h4>
            <div class="person-activity-list">${rows}</div>
          </div>`;
      })
      .join("");
  }
  // Fallback for older reports without groups
  const events = block.events || [];
  return `<div class="person-activity-list">${events.map(renderActivityEvent).join("")}</div>`;
}

function renderPersonActiveTaskRow(t) {
  return releaseTaskRowHtml(t, { showAssignee: false, showReleaseTags: false });
}

function groupPersonActiveTasksByRelease(tasks) {
  const releasesById = Object.fromEntries(
    (currentSprintReport?.releases || []).map((r) => [String(r.id), r])
  );
  const groups = new Map();
  const without = [];

  for (const t of tasks || []) {
    const releaseTags = (t.tags || []).filter((tag) => tag.id === "release");
    if (!releaseTags.length) {
      without.push(t);
      continue;
    }
    // One group per primary Fix Version (first release tag)
    const tag = releaseTags[0];
    const rid = tag.release_id != null ? String(tag.release_id) : "";
    const release = rid
      ? releasesById[rid]
      : (currentSprintReport?.releases || []).find((r) => r.name === tag.label);
    const key = rid || `name:${tag.label}`;
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        label: tag.label,
        release_id: release?.id != null ? String(release.id) : rid || null,
        tasks: [],
      });
    }
    groups.get(key).tasks.push(t);
  }

  return {
    withRelease: [...groups.values()].sort((a, b) =>
      String(a.label).localeCompare(String(b.label), "ru")
    ),
    withoutRelease: without,
  };
}

function renderPersonActiveTasks(tasks) {
  const list = tasks || [];
  if (!list.length) {
    return `<p class="muted">Нет активных задач направления</p>`;
  }
  const { withRelease, withoutRelease } = groupPersonActiveTasksByRelease(list);
  const blocks = [];

  for (const g of withRelease) {
    // Clickable if we know id and/or label — openReleaseModal resolves both.
    const clickable = !!(g.release_id || g.label);
    const title = clickable
      ? `<button type="button" class="person-task-group-release" data-release-id="${escapeHtml(
          g.release_id || ""
        )}" data-release-name="${escapeHtml(g.label || "")}">${escapeHtml(g.label)}</button>`
      : `<span class="person-task-group-release is-static">${escapeHtml(g.label)}</span>`;
    blocks.push(`
      <div class="person-task-group">
        <h4 class="person-task-group-title">
          <span class="person-task-group-kicker">Релиз</span>
          ${title}
          <span class="person-task-group-count">${fmtNumber(g.tasks.length)}</span>
        </h4>
        <div class="person-task-list release-task-list">${g.tasks.map(renderPersonActiveTaskRow).join("")}</div>
      </div>`);
  }

  if (withoutRelease.length) {
    blocks.push(`
      <div class="person-task-group">
        <h4 class="person-task-group-title">
          <span class="person-task-group-kicker">Без релиза</span>
          <span class="person-task-group-count">${fmtNumber(withoutRelease.length)}</span>
        </h4>
        <div class="person-task-list release-task-list">${withoutRelease.map(renderPersonActiveTaskRow).join("")}</div>
      </div>`);
  }

  return `<div class="person-task-groups">${blocks.join("")}</div>`;
}

function renderPersonLoad(load) {
  if (!load) return "";
  const level = load.level || "unknown";
  const pct = load.load_pct;
  const value =
    level === "empty"
      ? "—"
      : pct == null
        ? "—"
        : `${fmtNumber(pct)}%`;
  const levelLabel =
    {
      empty: "нет active-задач",
      unknown: "не хватает данных",
      ok: "есть запас",
      tight: "впритык",
      over: "перегруз",
    }[level] || "";
  const days = Number(load.days_left) || 0;
  const dayWord =
    days === 1 ? "раб. день" : days >= 2 && days <= 4 ? "раб. дня" : "раб. дн.";
  const detail = [
    `осталось ${fmtDuration(load.remaining_hours)}`,
    `ёмкость ${fmtDuration(load.capacity_hours)} (бюджет спринта − списано)`,
    days ? `${fmtNumber(days)} ${dayWord}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const zeroRemaining = Number(load.tasks_with_zero_remaining) || 0;
  const missingRemaining = Number(load.tasks_without_remaining) || 0;
  const notes = [];
  const taskWord = (n) =>
    n === 1 ? "задача" : n >= 2 && n <= 4 ? "задачи" : "задач";
  if (zeroRemaining) {
    notes.push(
      `<div class="person-load-note">${fmtNumber(zeroRemaining)} ${taskWord(
        zeroRemaining
      )} с нулевым Remaining Estimate — не добавляют будущие часы</div>`
    );
  }
  if (missingRemaining) {
    notes.push(
      `<div class="person-load-note">${fmtNumber(missingRemaining)} ${
        missingRemaining === 1
          ? "задача без оценки в Jira"
          : missingRemaining >= 2 && missingRemaining <= 4
            ? "задачи без оценки в Jira"
            : "задач без оценки в Jira"
      } Remaining Estimate — реальная нагрузка может быть выше</div>`
    );
  }
  const tipText =
    "Положительный Remaining Estimate active-задач / (бюджет спринта − уже списанные часы). Бюджет = рабочие дни спринта × норма в день. Нулевой или отсутствующий Remaining Estimate не заменяется условными часами.";
  return `
    <div class="person-load level-${escapeHtml(level)}">
      <div class="person-load-head">
        <span class="person-load-label">${tip("Нагрузка до конца спринта", tipText)}</span>
        <span class="person-load-level">${escapeHtml(levelLabel)}</span>
      </div>
      <div class="person-load-row">
        <div class="person-load-value">${escapeHtml(value)}</div>
        <div class="person-load-bar" aria-hidden="true">
          <div class="person-load-fill" style="width:${Math.min(100, Number(pct) || 0)}%"></div>
        </div>
      </div>
      <div class="person-load-detail">${escapeHtml(detail)}</div>
      ${notes.join("")}
    </div>`;
}

function historyFieldConflict(a, b, fromKey, toKey) {
  const aHas = a[fromKey] != null || a[toKey] != null;
  const bHas = b[fromKey] != null || b[toKey] != null;
  if (!(aHas && bHas)) return false;
  return String(a[fromKey] || "") !== String(b[fromKey] || "") || String(a[toKey] || "") !== String(b[toKey] || "");
}

function canMergeIssueHistory(a, b, windowMs) {
  if (!a || !b) return false;
  if (String(a.author || "") !== String(b.author || "")) return false;
  const ta = Date.parse(a.at || "");
  const tb = Date.parse(b.at || "");
  if (!Number.isFinite(ta) || !Number.isFinite(tb) || Math.abs(ta - tb) > windowMs) return false;
  if (historyFieldConflict(a, b, "status_from", "status_to")) return false;
  if (historyFieldConflict(a, b, "assignee_from", "assignee_to")) return false;
  const aStatus = a.status_from != null || a.status_to != null;
  const bStatus = b.status_from != null || b.status_to != null;
  const aAssign = a.assignee_from != null || a.assignee_to != null;
  const bAssign = b.assignee_from != null || b.assignee_to != null;
  // Merge complementary status+assignee edits (or identical duplicates).
  return (aStatus && bAssign) || (aAssign && bStatus) || (aStatus && bStatus) || (aAssign && bAssign);
}

function mergeIssueHistoryPair(a, b) {
  const newer = Date.parse(a.at || "") >= Date.parse(b.at || "") ? a : b;
  return {
    at: newer.at,
    author: a.author || b.author,
    status_from: a.status_from ?? b.status_from,
    status_to: a.status_to ?? b.status_to,
    assignee_from: a.assignee_from ?? b.assignee_from,
    assignee_to: a.assignee_to ?? b.assignee_to,
  };
}

function mergeIssueHistory(items) {
  const WINDOW_MS = 15 * 60 * 1000;
  const sorted = [...(items || [])].sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")));
  const out = [];
  for (const row of sorted) {
    const prev = out[out.length - 1];
    if (prev && canMergeIssueHistory(prev, row, WINDOW_MS)) {
      out[out.length - 1] = mergeIssueHistoryPair(prev, row);
    } else {
      out.push({ ...row });
    }
  }
  return out;
}

const DEFAULT_STATUS_FLOW = [
  "Сделать",
  "To Do",
  "Open",
  "Backlog",
  "Новая (оценено)",
  "В работе",
  "In Progress",
  "Hold",
  "Code Review",
  "In Review",
  "Review",
  "Developed",
  "Ready for testing",
  "Ready For Testing",
  "Testing",
  "Тестирование",
  "Тест",
  "Tested",
  "Verified",
  "Готово",
  "Done",
  "Closed",
  "Resolved",
];

function statusFlowRank(status) {
  const flow = currentSprintReport?.settings?.status_flow?.length
    ? currentSprintReport.settings.status_flow
    : DEFAULT_STATUS_FLOW;
  const key = String(status || "")
    .trim()
    .toLowerCase();
  if (!key) return null;
  const idx = flow.findIndex((s) => String(s).trim().toLowerCase() === key);
  return idx >= 0 ? idx : null;
}

function isStatusRollback(fromStatus, toStatus) {
  const a = statusFlowRank(fromStatus);
  const b = statusFlowRank(toStatus);
  if (a == null || b == null) return false;
  return b < a;
}

function samePersonName(a, b) {
  if (!a || !b) return false;
  if (String(a) === String(b)) return true;
  const pa = findPersonByName(a);
  const pb = findPersonByName(b);
  if (pa?.name && pb?.name) return pa.name === pb.name;
  return String(a).trim().toLowerCase() === String(b).trim().toLowerCase();
}

/** Reconstruct chronological states with authors of each transition. */
function buildIssueFlowStates(
  rawHistory,
  { fallbackAssignee = null, fallbackStatus = null } = {}
) {
  const events = mergeIssueHistory(rawHistory || [])
    .slice()
    .sort((a, b) => String(a.at || "").localeCompare(String(b.at || "")));
  if (!events.length) {
    // No changelog rows — still show current assignee/status when known
    if (fallbackAssignee || fallbackStatus) {
      return [
        {
          assignee: fallbackAssignee || null,
          status: fallbackStatus || null,
          at: null,
          author: null,
          assigneeChanged: false,
          statusChanged: false,
        },
      ];
    }
    return [];
  }

  let assignee =
    events.find((e) => e.assignee_from != null)?.assignee_from ??
    events.find((e) => e.assignee_to != null)?.assignee_to ??
    null;
  let status =
    events.find((e) => e.status_from != null)?.status_from ??
    events.find((e) => e.status_to != null)?.status_to ??
    null;
  if (!assignee) assignee = fallbackAssignee || null;
  if (!status) status = fallbackStatus || null;

  const states = [];
  const push = ({ at, author = null, assigneeChanged = false, statusChanged = false } = {}) => {
    const effectiveAssignee = assignee || fallbackAssignee || null;
    const effectiveStatus = status || fallbackStatus || null;
    if (!effectiveAssignee && !effectiveStatus) return;
    const last = states[states.length - 1];
    if (
      last &&
      last.assignee === effectiveAssignee &&
      last.status === effectiveStatus
    )
      return;
    states.push({
      assignee: effectiveAssignee,
      status: effectiveStatus,
      at: at || null,
      author: author || null,
      assigneeChanged: !!assigneeChanged,
      statusChanged: !!statusChanged,
    });
  };

  push({ at: events[0].at, author: null });

  for (const h of events) {
    let assigneeChanged = false;
    let statusChanged = false;
    if (h.assignee_from != null || h.assignee_to != null) {
      // null/undefined toString = unassigned; keep null (do not use || — "" is valid edge)
      const nextA = h.assignee_to != null ? h.assignee_to : null;
      if (nextA !== assignee) {
        assignee = nextA;
        assigneeChanged = true;
      }
    }
    if (h.status_from != null || h.status_to != null) {
      // Only advance when toString is present — never wipe status to null
      if (h.status_to != null && h.status_to !== status) {
        status = h.status_to;
        statusChanged = true;
      }
    }
    if (assigneeChanged || statusChanged) {
      push({
        at: h.at,
        author: h.author || null,
        assigneeChanged,
        statusChanged,
      });
    }
  }

  // Backfill empties from the issue's current fields
  if (fallbackAssignee || fallbackStatus) {
    for (const state of states) {
      if (!state.assignee && fallbackAssignee) state.assignee = fallbackAssignee;
      if (!state.status && fallbackStatus) state.status = fallbackStatus;
    }
  }
  return states;
}

function flowInitiatorFor(state) {
  if (!state?.author) return null;
  if (state.assignee && samePersonName(state.author, state.assignee)) return null;
  return state.author;
}

function statusEquals(a, b) {
  const na = String(a || "")
    .trim()
    .toLowerCase();
  const nb = String(b || "")
    .trim()
    .toLowerCase();
  if (!na || !nb) return false;
  if (na === nb) return true;
  return activityStatusLabel(a) === activityStatusLabel(b);
}

/**
 * Rows of forward progress. New row on status rollback.
 * Initiator (who assigned / who returned the task) is shown before the first node.
 */
function buildIssueFlowRows(
  rawHistory,
  { fallbackAssignee = null, fallbackStatus = null, reporter = null } = {}
) {
  const states = buildIssueFlowStates(rawHistory, {
    fallbackAssignee,
    fallbackStatus,
  });
  if (!states.length) return [];

  const rows = [];
  let current = { initiator: null, nodes: [states[0]] };

  for (let i = 1; i < states.length; i += 1) {
    const prev = states[i - 1];
    const node = states[i];
    const rollback = isStatusRollback(prev.status, node.status);
    const assignedByOther =
      node.assigneeChanged &&
      !!flowInitiatorFor(node) &&
      String(prev.assignee || "") !== String(node.assignee || "");

    if (rollback) {
      if (current.nodes.length) rows.push(current);
      current = { initiator: flowInitiatorFor(node), nodes: [node] };
      continue;
    }

    // First hand-off: Александр → ((Дмитрий) To Do) — drop prior assignee node
    if (i === 1 && assignedByOther && current.nodes.length === 1) {
      current = { initiator: flowInitiatorFor(node), nodes: [node] };
      continue;
    }

    current.nodes.push(node);
  }

  if (current.nodes.length) rows.push(current);

  // Jira «Инициатор» (reporter) as fallback for the first segment
  if (
    rows.length &&
    !rows[0].initiator &&
    reporter &&
    !samePersonName(reporter, rows[0].nodes[0]?.assignee)
  ) {
    rows[0].initiator = reporter;
  }

  return rows;
}

function issueFlowNodeHtml(node, { isCurrent = false, showPerson = true } = {}) {
  const person = showPerson
    ? node.assignee
      ? activityPersonChip(node.assignee, { withLoad: false })
      : `<span class="muted">—</span>`
    : "";
  const status = node.status
    ? `<span class="issue-flow-status">${escapeHtml(activityStatusLabel(node.status))}</span>`
    : `<span class="issue-flow-status is-empty">—</span>`;
  const when = node.at
    ? `<span class="muted issue-flow-when">${escapeHtml(fmtRelativeDateTime(node.at))}</span>`
    : "";
  const tip = node.at ? fmtDateTime(node.at) : "";
  return `
    <div class="issue-flow-node${isCurrent ? " is-current" : ""}${
      showPerson ? "" : " is-status-only"
    }" title="${escapeHtml(tip)}">
      <div class="issue-flow-node-main">${person}${status}</div>
      ${when}
    </div>`;
}

function renderIssueMovementFlow(issue) {
  const history = issue?.history || [];
  const fallbackAssignee = issue?.assignee_canonical || issue?.assignee || null;
  const fallbackStatus = issue?.status || null;
  const reporter = issue?.reporter_canonical || issue?.reporter || null;
  const rows = buildIssueFlowRows(history, {
    fallbackAssignee,
    fallbackStatus,
    reporter,
  });
  if (!rows.length) {
    return `<p class="muted">Нет смен статуса/исполнителя в данных спринта</p>`;
  }

  // Highlight the latest node that matches the issue's current status (fallback: last node).
  let currentMarked = false;
  const currentStatus = issue?.status || null;
  for (let r = rows.length - 1; r >= 0 && !currentMarked; r -= 1) {
    const nodes = rows[r].nodes || [];
    for (let i = nodes.length - 1; i >= 0; i -= 1) {
      if (!currentStatus || statusEquals(nodes[i].status, currentStatus)) {
        nodes[i] = { ...nodes[i], isCurrent: true };
        currentMarked = true;
        break;
      }
    }
  }
  if (!currentMarked) {
    const lastRow = rows[rows.length - 1];
    const lastNodes = lastRow?.nodes || [];
    if (lastNodes.length) lastNodes[lastNodes.length - 1].isCurrent = true;
  }

  return `
    <div class="issue-flow">
      ${rows
        .map((row) => {
          const parts = [];
          if (row.initiator) {
            parts.push(`
              <div class="issue-flow-initiator">
                ${activityPersonChip(row.initiator, { withLoad: false })}
              </div>`);
            parts.push(`<span class="issue-flow-arrow" aria-hidden="true">→</span>`);
          }
          (row.nodes || []).forEach((node, idx) => {
            if (idx) parts.push(`<span class="issue-flow-arrow" aria-hidden="true">→</span>`);
            const prev = idx > 0 ? row.nodes[idx - 1] : null;
            // Same assignee streak: person chip only on the first node
            const showPerson =
              idx === 0 ||
              !prev?.assignee ||
              !node.assignee ||
              !samePersonName(prev.assignee, node.assignee);
            parts.push(
              issueFlowNodeHtml(node, {
                isCurrent: !!node.isCurrent,
                showPerson,
              })
            );
          });
          return `<div class="issue-flow-row">${parts.join("")}</div>`;
        })
        .join("")}
    </div>`;
}

function gitlabIconSvg() {
  return `<svg class="gitlab-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M23.955 13.587l-1.342-4.135-2.664-8.189c-.135-.423-.73-.423-.867 0L16.418 9.45H7.582L4.919 1.263C4.783.84 4.185.84 4.05 1.263L1.386 9.452.044 13.587c-.121.375.014.789.331 1.023L12 23.054l11.625-8.443c.318-.235.453-.648.33-1.024z"/></svg>`;
}

function wikiLinkTokenToHtml(label, url) {
  const safeLabel = escapeHtml(label);
  if (/^https?:\/\//i.test(url)) {
    return `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${safeLabel}</a>`;
  }
  return safeLabel;
}

function formatJiraWikiHtml(raw) {
  const text = String(raw || "");
  if (!text.trim()) return "";
  const parts = [];
  const re = /\[([^\]|\n]+)\|([^\]\n]+)\]|\{quote\}([\s\S]*?)\{quote\}/gi;
  let last = 0;
  let match;
  while ((match = re.exec(text))) {
    if (match.index > last) {
      parts.push(escapeHtml(text.slice(last, match.index)));
    }
    if (match[1] != null && match[2] != null) {
      parts.push(wikiLinkTokenToHtml(match[1], match[2]));
    } else if (match[3] != null) {
      parts.push(`<blockquote class="issue-comment-quote">${escapeHtml(match[3].trim())}</blockquote>`);
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push(escapeHtml(text.slice(last)));
  return parts.join("").replace(/\n/g, "<br>");
}

function formatCommentHtml(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  if (/mentioned this issue in/i.test(text)) {
    const authorMatch = text.match(/^\[([^\]|]+)\|([^\]]+)\]/);
    const mrMatch = text.match(/\[a merge request\|([^\]]+)\]/i);
    const projectMatch = text.match(/of \[([^\|]+)\|([^\]]+)\]/i);
    const branchMatch = text.match(/on branch \[([^\|]+)\|([^\]]+)\]/i);
    const quoteMatch = text.match(/\{quote\}([\s\S]*?)\{quote\}/i);
    const authorName = authorMatch ? authorMatch[1].split("|")[0] : null;
    // Author display from wiki is often "Surname Name Patronymic"
    const authorLabel = authorName
      ? shortGivenSurname(authorName) || authorName
      : "GitLab";
    const mrUrl = mrMatch?.[1] || "";
    const projectLabel = projectMatch?.[1] || "проект";
    const branchLabel = branchMatch?.[1] || "";
    const quote = (quoteMatch?.[1] || "").trim();
    return `
      <div class="issue-gl-mention">
        <div class="issue-gl-mention-title">${gitlabIconSvg()} ${escapeHtml(authorLabel)} упомянул задачу в merge request</div>
        <div class="issue-gl-mention-meta">
          ${
            mrUrl
              ? `<a href="${escapeHtml(mrUrl)}" target="_blank" rel="noreferrer">Открыть MR</a>`
              : ""
          }
          <span class="muted">${escapeHtml(projectLabel)}${branchLabel ? ` · ${escapeHtml(branchLabel)}` : ""}</span>
        </div>
        ${quote ? `<blockquote class="issue-comment-quote">${escapeHtml(quote)}</blockquote>` : ""}
      </div>`;
  }
  return formatJiraWikiHtml(text);
}

function openIssueModal(issueKey, { jiraUrl = null } = {}) {
  const key = String(issueKey || "").trim().toUpperCase();
  const issue = currentSprintReport?.issues?.[key];
  if (!issue) {
    // Release/epic tasks may be outside sprint issue dossier — open Jira, no stub modal.
    const url = jiraBrowseUrlForKey(key, jiraUrl);
    if (url) {
      window.open(url, "_blank", "noopener,noreferrer");
      return;
    }
    openAppModal(
      `<h2 id="app-modal-title">${escapeHtml(key || "Задача")}</h2>
       <p class="muted">Нет данных по задаче в текущем отчёте, и ссылку на Jira собрать не удалось.</p>`,
      { type: "issue" }
    );
    return;
  }

  const jiraBtn = issue.web_url
    ? `<a class="jira-open-btn" href="${escapeHtml(issue.web_url)}" target="_blank" rel="noreferrer">Открыть в Jira</a>`
    : "";
  const assignee = issue.assignee_canonical || issue.assignee;
  const assigneeProfile = findPersonByName(issue.assignee_canonical) || findPersonByName(issue.assignee);
  const assigneeHtml = assignee
    ? personCell(issue.assignee || assignee, issue.avatar_url || assigneeProfile?.avatar_url, {
        givenSurname: true,
        personKey: issue.assignee_canonical || issue.assignee,
        withLoad: false,
      })
    : `<span class="muted">Без исполнителя</span>`;

  const spent = Number(issue.hours) || 0;
  const estimate = issue.estimate_hours;
  const remaining = issue.remaining_hours;
  const pct = issue.progress_pct;
  const metrics = `
    <div class="person-modal-stats issue-modal-stats">
      <div class="person-stat"><span class="label">Статус</span><span class="value value-text">${statusChip(
        issue.status,
        issue.status_category
      )}</span></div>
      <div class="person-stat"><span class="label">${tip(
        "Прогресс",
        "Списано / Original Estimate"
      )}</span><span class="value">${pct != null ? `${fmtNumber(pct)}%` : "—"}</span></div>
      <div class="person-stat"><span class="label">Списано</span><span class="value">${fmtDuration(spent)}</span></div>
      <div class="person-stat"><span class="label">Оценка / ост.</span><span class="value value-text">${
        estimate != null ? fmtDuration(estimate) : "—"
      } / ${remaining != null ? fmtDuration(remaining) : "—"}</span></div>
    </div>`;

  const allTags = issue.tags || [];
  const releaseTags = allTags.filter((t) => t.id === "release");
  const riskTags = allTags.filter((t) => t.id !== "release");
  const releasesById = Object.fromEntries(
    (currentSprintReport?.releases || []).map((r) => [String(r.id), r])
  );
  const releaseTiles = releaseTags
    .map((tag) => {
      const rid = tag.release_id != null ? String(tag.release_id) : "";
      const release = rid ? releasesById[rid] : (currentSprintReport?.releases || []).find((r) => r.name === tag.label);
      const releaseId = release?.id != null ? String(release.id) : rid;
      const riskLabel = release?.risk_label || "";
      const riskCls = release?.risk ? ` risk-${escapeHtml(release.risk)}` : "";
      const clickable = !!releaseId;
      const tagName = clickable ? "button" : "div";
      const typeAttr = clickable ? ` type="button"` : "";
      const dataAttr = clickable ? ` data-release-id="${escapeHtml(releaseId)}"` : "";
      const cls = `issue-release-tile${clickable ? " is-clickable" : ""}${riskCls}`;
      return `
        <${tagName}${typeAttr} class="${cls}"${dataAttr}>
          <span class="issue-release-kicker">Релиз</span>
          <strong class="issue-release-name">${escapeHtml(tag.label)}</strong>
          ${
            riskLabel
              ? `<span class="issue-release-risk">${escapeHtml(riskLabel)}</span>`
              : `<span class="muted issue-release-risk">В отчёте нет карточки релиза</span>`
          }
        </${tagName}>`;
    })
    .join("");
  const releaseHtml = releaseTiles
    ? `<div class="person-modal-section"><h3>Релиз</h3><div class="issue-release-tiles">${releaseTiles}</div></div>`
    : issue.hidden_from_display
      ? ""
      : `<div class="person-modal-section">
          <h3>Релиз</h3>
          <div class="issue-no-release">Нет в релизе</div>
        </div>`;
  const risksHtml = `
    <div class="person-modal-section">
      <h3>Риски</h3>
      ${
        riskTags.length
          ? `<div class="task-tags">${tagsHtml(riskTags)}</div>`
          : `<p class="muted">Явных рисков по задаче нет</p>`
      }
    </div>`;

  const historyHtml = `
    <div class="person-modal-section">
      <h3>Движение</h3>
      ${renderIssueMovementFlow(issue)}
    </div>`;

  const worklogs = (issue.worklogs || [])
    .map((w) => {
      const who = w.author_canonical || w.author;
      const whoHtml = who
        ? activityPersonChip(who, { withLoad: false })
        : `<span class="muted">—</span>`;
      const verb = "Списал";
      const comment = w.comment
        ? `<div class="issue-worklog-comment">${formatJiraWikiHtml(w.comment)}</div>`
        : "";
      return `
        <div class="person-activity-row type-progress">
          <div class="act-flow act-flow-worklog">
            <div class="issue-worklog-top">
              ${whoHtml}
              <span class="muted act-when">${escapeHtml(w.at ? fmtRelativeDateTime(w.at) : "—")}</span>
              <span class="act-chip act-chip-hours issue-worklog-hours">${escapeHtml(`${verb} ${fmtDuration(w.hours)}`)}</span>
            </div>
            ${comment}
          </div>
        </div>`;
    })
    .join("");
  const worklogsHtml = `
    <div class="person-modal-section">
      <h3>Списания</h3>
      <div class="person-activity-list issue-worklog-list">${
        worklogs || `<p class="muted">Нет списаний в окне спринта</p>`
      }</div>
    </div>`;

  const comments = (issue.comments || [])
    .map((c) => {
      const who = c.author_canonical || c.author;
      const whoHtml = who ? activityPersonChip(who, { withLoad: false }) : `<span class="muted">—</span>`;
      return `
        <div class="issue-comment-row">
          <div class="issue-comment-head">
            ${whoHtml}
            <span class="muted">${escapeHtml(c.at ? fmtRelativeDateTime(c.at) : "—")}</span>
          </div>
          <div class="issue-comment-body">${formatCommentHtml(c.body || "")}</div>
        </div>`;
    })
    .join("");
  const commentsHtml = `
    <div class="person-modal-section">
      <h3>Комментарии</h3>
      <div class="issue-comment-list">${
        comments || `<p class="muted">Нет комментариев</p>`
      }</div>
    </div>`;

  const epicHtml = issue.epic_key
    ? `<p class="muted issue-epic-line">Эпик: <button type="button" class="issue-key issue-key-btn" data-issue="${escapeHtml(
        issue.epic_key
      )}">${escapeHtml(issue.epic_key)}</button> ${escapeHtml(issue.epic_summary || "")}</p>`
    : "";

  const mrs = (issue.mrs || [])
    .map((mr) => {
      const title = escapeHtml(mr.title || `MR !${mr.iid || ""}`);
      return mr.web_url
        ? `<a class="issue-mr-link" href="${escapeHtml(mr.web_url)}" target="_blank" rel="noreferrer">${gitlabIconSvg()}<span>${title}</span></a>`
        : `<span class="issue-mr-link is-static">${gitlabIconSvg()}<span>${title}</span></span>`;
    })
    .join("");
  const mrsHtml = mrs
    ? `<div class="person-modal-section"><h3>Merge requests</h3><div class="issue-mr-list">${mrs}</div></div>`
    : "";

  openAppModal(
    `
    <div class="issue-modal-head">
      <div class="issue-modal-title-row">
        <h2 id="app-modal-title" class="issue-modal-title">
          <span class="issue-key issue-key-static">${escapeHtml(issue.key)}</span>
          <span class="issue-modal-summary">${escapeHtml(issue.summary || issue.key)}</span>
        </h2>
        ${jiraBtn}
      </div>
      <div class="issue-modal-assignee">${assigneeHtml}</div>
      ${epicHtml}
    </div>
    ${metrics}
    ${releaseHtml}
    ${risksHtml}
    ${historyHtml}
    ${worklogsHtml}
    ${commentsHtml}
    ${mrsHtml}
  `,
    { person: true, type: "issue" }
  );
}

function renderPersonActivity(activity) {
  if (!activity) return "";
  const blocks = [
    ["yesterday", activity.yesterday],
    ["today", activity.today],
  ];
  const sections = blocks
    .map(([id, block]) => {
      if (!block) return "";
      const title = escapeHtml(block.label || (id === "yesterday" ? "Вчера" : "Сегодня"));
      const dateLabel = block.date
        ? `<span class="person-activity-date">${escapeHtml(fmtDay(block.date))}</span>`
        : "";
      const events = block.events || [];
      const meaningful = events.filter((e) => e && e.type && e.type !== "more");
      const body = meaningful.length
        ? renderActivityGroups(block)
        : `<p class="muted person-activity-empty">Нет заметных движений</p>`;
      return `
        <div class="person-modal-section person-activity-section">
          <h3>${title} ${dateLabel}</h3>
          ${body}
        </div>`;
    })
    .join("");
  return sections;
}

function openRatingModal(catId) {
  const cat = (currentSprintReport?.ratings || []).find((c) => c.id === catId);
  if (!cat) return;
  const people = cat.all_people || cat.people || [];
  const stabilityPct = (value) => {
    if (value === null || value === undefined) return "—";
    const number = Number(value);
    return Number.isFinite(number) ? `${number.toFixed(1)}%` : "—";
  };
  const modalDetail = (person) => {
    const stability = person.stability;
    if (cat.id !== "stability" || !stability) {
      return `<div class="muted rating-score-detail">${escapeHtml(person.detail || "")}</div>`;
    }
    return `
      <div class="rating-stability-details">
        <span>Темп <strong>${escapeHtml(stabilityPct(stability.pace_pct))}</strong></span>
        <span>Вовремя <strong>${escapeHtml(stabilityPct(stability.timely_pct))}</strong></span>
        <span>Наперёд <strong>${escapeHtml(fmtNumber(stability.advance_hours || 0))} ч</strong> за ${escapeHtml(fmtNumber(stability.advance_days || 0))} дн.</span>
        <span>Равномерность <strong>${escapeHtml(stabilityPct(stability.cadence_pct))}</strong></span>
        <span>Закрыто задач <strong>${escapeHtml(fmtNumber(stability.closed_tasks || 0))}</strong></span>
      </div>`;
  };
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
            ${modalDetail(p)}
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
    { wide: true, rating: true, type: "rating" }
  );
}

function openPersonTasksModal(name) {
  const profile = currentSprintReport?.people?.[name];
  if (!profile) return;
  const tasks = profile.tasks_active || profile.tasks || [];
  const rows = tasks.map((t) => releaseTaskRowHtml(t, { showAssignee: false })).join("");
  openAppModal(
    `
    <h2 id="app-modal-title">Задачи · ${escapeHtml(shortName(name))}</h2>
    <p class="muted">${escapeHtml(profile.direction || "")} · активных ${fmtNumber(profile.tasks_open)} · с рисками ${fmtNumber(profile.risk_count)}</p>
    <div class="person-task-list release-task-list" style="margin-top:0.85rem">${rows || `<p class="muted">Нет задач</p>`}</div>
  `,
    { wide: true, type: "person_tasks" }
  );
}

function isTaskDone(task) {
  if (!task) return false;
  if (task.direction_state === "done" || task.done || task.jira_done) return true;
  const cat = String(task.status_category || "").toLowerCase();
  return cat === "done";
}

function assigneeStatusNodeHtml(assignee, status, { showPerson = true } = {}) {
  // Same visual as task modal «Движение» nodes: white rounded pill with chip + status.
  const person = showPerson
    ? assignee
      ? activityPersonChip(assignee, { withLoad: false })
      : `<span class="muted">—</span>`
    : "";
  const statusHtml = status
    ? `<span class="issue-flow-status">${escapeHtml(activityStatusLabel(status))}</span>`
    : `<span class="issue-flow-status is-empty">—</span>`;
  return `
    <div class="issue-flow-node${showPerson ? "" : " is-status-only"}">
      <div class="issue-flow-node-main">${person}${statusHtml}</div>
    </div>`;
}

function releaseTaskRowHtml(
  t,
  {
    forceRisk = false,
    showReleaseTags = false,
    outOfSprint = false,
    showAssignee = true,
  } = {}
) {
  const key = String(t?.key || "").trim().toUpperCase();
  const dossier = key ? currentSprintReport?.issues?.[key] : null;
  const merged = { ...(dossier || {}), ...(t || {}), key: key || t?.key };
  const done = isTaskDone(merged);
  const rowCls = [
    "person-task-row",
    "release-task-row",
    forceRisk || merged.risk ? "task-row-risk" : "",
    done ? "task-row-done" : "",
    outOfSprint || merged.in_sprint === false ? "task-row-out-of-sprint" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const tags = (merged.tags || []).filter((tag) => {
    if (!tag) return false;
    if (tag.id === "release") return !!showReleaseTags;
    return true;
  });
  const tagsInline = tags.length
    ? `<span class="task-tags release-task-tags">${tagsHtml(tags, {
        clickableReleases: !!showReleaseTags,
      })}</span>`
    : "";
  const summary = merged.summary
    ? `<span class="release-task-summary" title="${escapeHtml(merged.summary)}">${escapeHtml(merged.summary)}</span>`
    : "";
  const assigneeKey = merged.assignee_canonical || merged.assignee;
  const meta = assigneeStatusNodeHtml(assigneeKey || merged.assignee, merged.status, {
    showPerson: !!showAssignee,
  });
  return `
    <div class="${rowCls}">
      <div class="release-task-main">
        <div class="release-task-line">
          ${issueLink(merged)}
          ${summary}
          ${tagsInline}
        </div>
      </div>
      <div class="release-task-meta">${meta}</div>
    </div>`;
}

function releaseAiBriefHtml(release) {
  const local = release?.ai_brief;
  const bundle = currentSprintReport?.ai_release_briefs || {};
  const gkey = releaseDateGroupKey(release);
  const fromBundle =
    (gkey && bundle.briefs?.[gkey]) ||
    (release?.id != null && bundle.briefs?.[String(release.id)]) ||
    bundle.briefs?.[release?.name] ||
    null;
  const brief =
    local?.markdown || local?.text
      ? local
      : fromBundle?.status === "ok"
        ? fromBundle
        : null;
  const dateLabel =
    gkey === "undated" ? "без даты" : fmtDay(brief?.release_date || release?.release_date || gkey);
  const reused = brief?.reason === "reused" || bundle.reason === "reused";
  const label = `${reused ? "Прошлая AI-оценка" : "AI-оценка"} релизов на ${dateLabel}`;
  if (!brief?.markdown && !brief?.text) {
    if (bundle.status === "error") {
      return `
        <div class="person-modal-section">
          <div class="release-ai-brief tone-info">
            <div class="person-ai-note-head">
              <span class="person-ai-note-label">${escapeHtml(label)}</span>
            </div>
            <p class="muted">AI-оценка сейчас недоступна.</p>
          </div>
        </div>`;
    }
    return "";
  }
  const markdown = brief.markdown || brief.text || "";
  const verdictText =
    brief.verdict ||
    extractAiVerdict(markdown) ||
    "Откройте подробности, чтобы увидеть оценку.";
  const tone = ["attention", "ok", "info"].includes(brief.tone) ? brief.tone : "ok";
  const author = brief.author || bundle.author || corporateAuthorLabel(bundle) || "corporate";
  const when = brief.generated_at
    ? fmtRelativeAgo(brief.generated_at)
    : bundle.generated_at
      ? fmtRelativeAgo(bundle.generated_at)
      : "";
  const meta = [reused ? "переиспользована" : "", author, when].filter(Boolean).join(" · ");
  return `
    <div class="person-modal-section">
      <div class="release-ai-brief is-collapsed tone-${escapeHtml(tone)}" data-release-ai-card role="button" tabindex="0" aria-expanded="false" title="Нажмите, чтобы раскрыть">
        <div class="person-ai-note-head">
          <span class="person-ai-note-label">${escapeHtml(label)}</span>
          <div class="release-ai-brief-head-right">
            ${meta ? `<span class="person-ai-note-meta">${escapeHtml(meta)}</span>` : ""}
            <button type="button" class="release-ai-expand-btn" data-release-ai-expand aria-expanded="false">Подробнее</button>
          </div>
        </div>
        <div class="release-ai-brief-verdict">${formatAiInlineMarkdown(verdictText)}</div>
        <div class="release-ai-brief-body">${renderAiMarkdown(markdown)}</div>
      </div>
    </div>`;
}

function toggleReleaseAiBriefCard(card, forceOpen = null) {
  if (!card) return;
  const open = forceOpen == null ? card.classList.contains("is-collapsed") : !!forceOpen;
  card.classList.toggle("is-collapsed", !open);
  card.setAttribute("aria-expanded", open ? "true" : "false");
  const btn = card.querySelector("[data-release-ai-expand]");
  if (btn) {
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    btn.textContent = open ? "Свернуть" : "Подробнее";
  }
  card.title = open ? "Нажмите, чтобы свернуть" : "Нажмите, чтобы раскрыть";
}

function findRelease(releaseId, releaseName) {
  const releases = currentSprintReport?.releases || [];
  const byId = releases.find((r) => String(r.id) === String(releaseId || ""));
  if (byId) return byId;
  const name = String(releaseName || "").trim();
  if (!name) return null;
  return releases.find((r) => String(r.name || "").trim() === name) || null;
}

function openReleaseModal(releaseId, releaseName) {
  const release = findRelease(releaseId, releaseName);
  if (!release) {
    openAppModal(
      `
      <div class="release-modal-head">
        <h2 id="app-modal-title">${escapeHtml(releaseName || "Релиз")}</h2>
      </div>
      <p class="muted">Этого релиза нет в текущем снимке спринта — карточка недоступна.</p>
      `,
      { wide: true, release: true, type: "release" }
    );
    return;
  }
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
          <div class="person-task-list release-task-list">${taskList || `<p class="muted">Нет задач направления</p>`}</div>
        </div>`;
    })
    .join("");
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
    </div>
    ${releaseAiBriefHtml(release)}
    ${release.description ? `<div class="person-modal-section"><h3>Описание</h3><p class="muted">${escapeHtml(release.description)}</p></div>` : ""}
    ${dirBlocks || `<p class="muted">Нет задач команды на версии</p>`}
  `,
    { wide: true, release: true, type: "release" }
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
      <td class="col-assignee">${personCell(t.assignee || "—", t.avatar_url, {
        short: true,
        personKey: t.assignee_canonical || t.assignee,
        load: (currentSprintReport?.people || {})[t.assignee_canonical || t.assignee]?.load,
      })}</td>
      <td class="col-hours">${timeCell(t.hours, t.estimate_hours, { warnZero: true })}</td>
      ${commitsCell}
    </tr>`;
}

function slugifyId(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-zа-яё0-9]+/gi, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48) || "item";
}

let reportNavSpyRaf = 0;
let reportNavSpyLockUntil = 0;
let reportNavSpyBound = false;

function setReportNavActive(targetId) {
  const nav = document.getElementById("report-nav");
  if (!nav || !targetId) return;
  nav.querySelectorAll(".report-nav-btn").forEach((btn) => {
    const on = btn.getAttribute("data-nav-target") === targetId;
    btn.classList.toggle("is-active", on);
    if (on) btn.setAttribute("aria-current", "true");
    else btn.removeAttribute("aria-current");
  });
}

function reportNavTargetIds() {
  const nav = document.getElementById("report-nav");
  if (!nav) return [];
  return [...nav.querySelectorAll(".report-nav-btn[data-nav-target]")]
    .map((btn) => btn.getAttribute("data-nav-target"))
    .filter(Boolean);
}

function updateReportNavFromScroll() {
  if (Date.now() < reportNavSpyLockUntil) return;
  const ids = reportNavTargetIds();
  if (!ids.length) return;
  const marker = window.scrollY + Math.min(160, window.innerHeight * 0.22);
  let active = ids[0];
  for (const id of ids) {
    const el = document.getElementById(id);
    if (!el || el.classList.contains("hidden")) continue;
    const top = el.getBoundingClientRect().top + window.scrollY;
    if (top <= marker) active = id;
  }
  const nearBottom =
    window.innerHeight + window.scrollY >=
    (document.documentElement.scrollHeight || document.body.scrollHeight) - 24;
  if (nearBottom) active = ids[ids.length - 1];
  setReportNavActive(active);
}

function scheduleReportNavSpy() {
  if (reportNavSpyRaf) return;
  reportNavSpyRaf = requestAnimationFrame(() => {
    reportNavSpyRaf = 0;
    updateReportNavFromScroll();
  });
}

function bindReportNavSpy() {
  if (!reportNavSpyBound) {
    reportNavSpyBound = true;
    window.addEventListener("scroll", scheduleReportNavSpy, { passive: true });
    window.addEventListener("resize", scheduleReportNavSpy);
  }
  scheduleReportNavSpy();
}

function scrollToReportSection(targetId) {
  const el = document.getElementById(targetId);
  if (!el) return;
  setReportNavActive(targetId);
  // Keep click selection until smooth scroll settles.
  reportNavSpyLockUntil = Date.now() + 900;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  // Expand collapsed target panels so the jump is useful.
  if (el.classList.contains("collapsible-panel") && !el.classList.contains("is-open")) {
    const toggle = el.querySelector("[data-collapse-toggle]");
    if (toggle) toggle.click();
  }
  window.setTimeout(() => {
    reportNavSpyLockUntil = 0;
    updateReportNavFromScroll();
  }, 950);
}

const NAV_ICONS = {
  directions: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/></svg>`,
  releases: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3l8 4v6c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V7l8-4z"/><path d="M9.5 12l1.8 1.8L15 10"/></svg>`,
  epics: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M5 4h9l5 5v11H5V4z"/><path d="M14 4v5h5"/><path d="M8 13h8M8 17h5"/></svg>`,
  worklog: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3.2 2"/></svg>`,
  risks: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 4l9 16H3L12 4z"/><path d="M12 10v4M12 17.5h.01"/></svg>`,
  ratings: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3.8l2.3 4.7 5.2.8-3.8 3.7.9 5.2L12 15.8 7.4 18.2l.9-5.2L4.5 9.3l5.2-.8L12 3.8z"/></svg>`,
  search: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="6.5"/><path d="M16.2 16.2L20 20"/></svg>`,
  fallback: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="8"/></svg>`,
};

function reportNavIconHtml(item) {
  if (item.kind === "direction") {
    return (DIRECTION_ICONS[item.label] && DIRECTION_ICONS[item.label].svg) || NAV_ICONS.fallback;
  }
  return NAV_ICONS[item.icon] || NAV_ICONS.fallback;
}

function renderReportNav(directions) {
  const nav = document.getElementById("report-nav");
  if (!nav) return;
  const items = [
    { id: "directions-section", label: "Направления", kind: "main", icon: "directions" },
    { id: "releases-section", label: "Релизы", kind: "main", icon: "releases" },
    { id: "epic-timeline-section", label: "Эпики", kind: "main", icon: "epics" },
  ];
  for (const d of directions || []) {
    items.push({
      id: `direction-${slugifyId(d.name)}`,
      label: d.name,
      kind: "direction",
    });
  }
  items.push(
    { id: "team-section", label: "Списание времени", kind: "main", icon: "worklog" },
    { id: "risks-section", label: "Риски спринта", kind: "main", icon: "risks" },
    { id: "ratings-section", label: "Рейтинг", kind: "main", icon: "ratings" }
  );
  const visible = items.filter((item) => {
    const el = document.getElementById(item.id);
    return el && !el.classList.contains("hidden");
  });
  const navBtns = visible
    .map(
      (item) => `
    <button type="button" class="report-nav-btn kind-${escapeHtml(item.kind)}" data-nav-target="${escapeHtml(item.id)}" title="${escapeHtml(item.label)}" aria-label="${escapeHtml(item.label)}">
      ${reportNavIconHtml(item)}
    </button>`
    )
    .join("");
  const searchBtn = `
    <button type="button" class="report-nav-btn kind-search" data-report-search title="Поиск" aria-label="Поиск">
      ${NAV_ICONS.search}
    </button>`;
  nav.innerHTML = `${navBtns}${searchBtn}`;
  nav.classList.toggle("hidden", !visible.length && !searchBtn);
  bindReportNavSpy();
}

let reportSearchDebounce = 0;
let reportSearchProgressTimer = 0;
let reportSearchToken = 0;
let reportSearchIndex = null;

function clearReportSearchState() {
  if (reportSearchDebounce) {
    clearTimeout(reportSearchDebounce);
    reportSearchDebounce = 0;
  }
  if (reportSearchProgressTimer) {
    clearInterval(reportSearchProgressTimer);
    reportSearchProgressTimer = 0;
  }
  reportSearchToken += 1;
  reportSearchIndex = null;
}

function normalizeSearchText(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/ё/g, "е")
    .replace(/\s+/g, " ")
    .trim();
}

function haystackHas(haystack, query) {
  return !!query && haystack.includes(query);
}

function collectSearchTasks(sr) {
  const byKey = new Map();
  const push = (t) => {
    if (!t || typeof t !== "object") return;
    const key = String(t.key || "").trim().toUpperCase();
    if (!key) return;
    const prev = byKey.get(key) || {};
    byKey.set(key, { ...prev, ...t, key });
  };
  for (const issue of Object.values(sr.issues || {})) push(issue);
  for (const d of sr.directions || []) {
    for (const t of [...(d.remaining_tasks || []), ...(d.tasks || []), ...(d.top_tasks_by_commits || [])]) {
      push(t);
    }
  }
  for (const r of sr.releases || []) {
    for (const t of r.tasks || []) push(t);
    for (const s of r.sections || []) {
      for (const t of s.tasks_detail || []) push(t);
    }
  }
  for (const profile of Object.values(sr.people || {})) {
    for (const t of [...(profile.tasks_active || []), ...(profile.tasks || [])]) push(t);
  }
  const risks = sr.risks || {};
  for (const list of Object.values(risks)) {
    if (!Array.isArray(list)) continue;
    for (const t of list) push(t);
  }
  for (const epic of sr.epic_timeline?.epics || []) {
    for (const s of epic.sections || []) {
      for (const t of s.tasks_detail || []) push(t);
    }
  }
  return [...byKey.values()];
}

function buildReportSearchIndex(sr) {
  if (!sr) {
    return { tasks: [], releases: [], people: [], epics: [] };
  }
  const tasks = collectSearchTasks(sr).map((t) => {
    const key = String(t.key || "").toUpperCase();
    const dossier = sr.issues?.[key] || {};
    const merged = { ...dossier, ...t, key };
    const assignee = merged.assignee_canonical || merged.assignee || "";
    return {
      item: merged,
      hay: normalizeSearchText(
        [merged.key, merged.summary, assignee, shortGivenSurname(assignee), shortName(assignee)].join(" ")
      ),
    };
  });
  const releases = (sr.releases || []).map((r) => ({
    item: r,
    hay: normalizeSearchText(
      [r.name, r.description, r.release_date, fmtDay(r.release_date), r.project].join(" ")
    ),
  }));
  const people = Object.values(sr.people || {}).map((p) => ({
    item: p,
    hay: normalizeSearchText(
      [p.name, shortGivenSurname(p.name), shortName(p.name), p.direction, p.role].join(" ")
    ),
  }));
  const epicMap = new Map();
  for (const e of sr.epic_timeline?.epics || []) {
    if (e?.key) epicMap.set(String(e.key), e);
  }
  for (const d of sr.directions || []) {
    for (const e of d.epics || []) {
      const key = String(e.key || "");
      if (!key || epicMap.has(key)) continue;
      epicMap.set(key, e);
    }
  }
  const epics = [...epicMap.values()].map((e) => {
    const releaseNames = (e.releases || []).map((r) => r.name).filter(Boolean);
    return {
      item: e,
      hay: normalizeSearchText([e.key, e.summary, ...releaseNames].join(" ")),
    };
  });
  return { tasks, releases, people, epics };
}

function getReportSearchIndex() {
  if (!reportSearchIndex) {
    reportSearchIndex = buildReportSearchIndex(currentSprintReport);
  }
  return reportSearchIndex;
}

function runReportSearch(query) {
  const q = normalizeSearchText(query);
  if (q.length < 2) {
    return { query: q, groups: [] };
  }
  const index = getReportSearchIndex();
  const limit = 24;
  const tasks = index.tasks.filter((x) => haystackHas(x.hay, q)).slice(0, limit).map((x) => x.item);
  const releases = index.releases
    .filter((x) => haystackHas(x.hay, q))
    .slice(0, limit)
    .map((x) => x.item);
  const people = index.people.filter((x) => haystackHas(x.hay, q)).slice(0, limit).map((x) => x.item);
  const epics = index.epics.filter((x) => haystackHas(x.hay, q)).slice(0, limit).map((x) => x.item);
  const groups = [];
  if (releases.length) groups.push({ type: "releases", label: "Релизы", items: releases });
  if (tasks.length) groups.push({ type: "tasks", label: "Задачи", items: tasks });
  if (epics.length) groups.push({ type: "epics", label: "Эпики", items: epics });
  if (people.length) groups.push({ type: "people", label: "Сотрудники", items: people });
  return { query: q, groups };
}

function searchPersonCardHtml(profile) {
  const name = profile?.name || "—";
  const avatarUrl =
    teamAvatarOverride(name) || profile?.avatar_url || null;
  const load = profile?.load || null;
  const dir = profile?.direction || "—";
  return `
    <button type="button" class="search-person-card person-btn" data-person="${escapeHtml(name)}" title="${escapeHtml(name)}">
      ${avatarWithLoad(avatarImgHtml(name, avatarUrl), load, { size: "sm" })}
      <span class="search-person-meta">
        <span class="search-person-name">${escapeHtml(name)}</span>
        <span class="search-person-dir">${escapeHtml(dir)}</span>
      </span>
    </button>`;
}

function renderReportSearchResults(root, result) {
  if (!root) return;
  if (!result.query || result.query.length < 2) {
    root.innerHTML = `<p class="muted search-hint">Введите минимум 2 символа</p>`;
    return;
  }
  if (!result.groups.length) {
    root.innerHTML = `<p class="muted search-hint">Ничего не найдено</p>`;
    return;
  }
  root.innerHTML = `<div class="search-groups">${result.groups
    .map((g) => {
      let list = "";
      if (g.type === "releases") {
        list = `<div class="search-group-list">${g.items
          .map((r) => releaseCardHtml(r, { showDate: true }))
          .join("")}</div>`;
      } else if (g.type === "tasks") {
        list = `<div class="search-group-list person-task-list release-task-list">${g.items
          .map((t) =>
            releaseTaskRowHtml(t, {
              showReleaseTags: true,
              outOfSprint: t.in_sprint === false,
            })
          )
          .join("")}</div>`;
      } else if (g.type === "epics") {
        list = `<div class="search-group-list epic-rows">${g.items
          .map((e) => epicTimelineCardHtml(epicCardForDisplay(e) || e))
          .join("")}</div>`;
      } else if (g.type === "people") {
        list = `<div class="search-group-list">${g.items.map(searchPersonCardHtml).join("")}</div>`;
      }
      return `
        <section class="search-group">
          <h3 class="search-group-title">${escapeHtml(g.label)} · ${fmtNumber(g.items.length)}</h3>
          ${list}
        </section>`;
    })
    .join("")}</div>`;
}

function setReportSearchProgress(layer, pct, { hidden = false } = {}) {
  const wrap = layer?.querySelector("[data-report-search-progress]");
  const fill = layer?.querySelector("[data-report-search-progress-fill]");
  if (!wrap || !fill) return;
  const value = Math.max(0, Math.min(100, Number(pct) || 0));
  wrap.classList.toggle("hidden", !!hidden);
  wrap.setAttribute("aria-hidden", hidden ? "true" : "false");
  const radius = Number(fill.getAttribute("r")) || 8;
  const circ = 2 * Math.PI * radius;
  fill.style.strokeDasharray = `${circ}`;
  fill.style.strokeDashoffset = `${circ * (1 - value / 100)}`;
}

function scheduleReportSearch(layer, rawQuery) {
  if (reportSearchDebounce) clearTimeout(reportSearchDebounce);
  if (reportSearchProgressTimer) {
    clearInterval(reportSearchProgressTimer);
    reportSearchProgressTimer = 0;
  }
  const results = layer.querySelector("[data-report-search-results]");
  const q = String(rawQuery || "").trim();
  if (q.length < 2) {
    setReportSearchProgress(layer, 0, { hidden: true });
    renderReportSearchResults(results, { query: normalizeSearchText(q), groups: [] });
    return;
  }
  const token = ++reportSearchToken;
  setReportSearchProgress(layer, 8, { hidden: false });
  let pct = 8;
  reportSearchProgressTimer = setInterval(() => {
    pct = Math.min(86, pct + 9 + Math.random() * 8);
    setReportSearchProgress(layer, pct);
  }, 70);
  reportSearchDebounce = setTimeout(() => {
    reportSearchDebounce = 0;
    // Artificial delay so progress is visible even on fast local search.
    window.setTimeout(() => {
      if (token !== reportSearchToken) return;
      if (reportSearchProgressTimer) {
        clearInterval(reportSearchProgressTimer);
        reportSearchProgressTimer = 0;
      }
      setReportSearchProgress(layer, 100);
      const found = runReportSearch(q);
      renderReportSearchResults(results, found);
      window.setTimeout(() => {
        if (token === reportSearchToken) setReportSearchProgress(layer, 0, { hidden: true });
      }, 180);
    }, 220);
  }, 260);
}

function openSearchModal() {
  clearReportSearchState();
  openAppModal(
    `
    <div class="search-modal">
      <div class="search-modal-head">
        <h2 id="app-modal-title">Поиск</h2>
        <div class="search-input-wrap">
          <input type="search" class="search-input" data-report-search-input placeholder="что ищем?" autocomplete="off" spellcheck="false" />
          <div class="search-progress-circle hidden" data-report-search-progress aria-hidden="true">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle class="search-progress-track" cx="12" cy="12" r="8"></circle>
              <circle class="search-progress-fill" data-report-search-progress-fill cx="12" cy="12" r="8"></circle>
            </svg>
          </div>
        </div>
      </div>
      <div class="search-modal-results" data-report-search-results>
        <p class="muted search-hint">Введите минимум 2 символа</p>
      </div>
    </div>
  `,
    { wide: true, type: "search" }
  );
  const layer = getModalsRoot()?.querySelector('.modal[data-modal-type="search"]');
  const input = layer?.querySelector("[data-report-search-input]");
  if (!input) return;
  // Prefetch index while the modal is open.
  getReportSearchIndex();
  input.addEventListener("input", () => scheduleReportSearch(layer, input.value));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      closeAppModal();
    }
  });
  requestAnimationFrame(() => input.focus());
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

      const dirId = `direction-${slugifyId(d.name)}`;
      return `
        <section id="${escapeHtml(dirId)}" class="panel direction-detail collapsible-panel" data-direction-nav="${escapeHtml(d.name)}">
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
  const root = document.getElementById("team-by-direction");
  const mini = document.getElementById("worklog-mini");
  const people = report.people || {};
  const byDay = (worklogs.by_day || []).filter((day) => !day.is_weekend);
  const days = byDay.map((day) => day.date);
  const expectedToDate = Number(worklogs.expected_hours_to_date) || 0;
  const planHours =
    Number(worklogs.plan_hours) ||
    days.length * (Number(worklogs.expected_hours_per_day) || 8);
  const reportCollectedAt = fmtDateTime(currentReportMeta?.fetched_at);
  const reportUrl = `${window.location.origin}${window.location.pathname}`;
  const weekdayNames = ["ВС", "ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ"];
  const weekdayLabel = (value) => {
    const d = new Date(`${value}T12:00:00`);
    return weekdayNames[d.getDay()] || "—";
  };
  const shortDate = (value) => {
    const match = String(value || "").match(/^\d{4}-(\d{2})-(\d{2})/);
    return match ? `${match[2]}.${match[1]}` : fmtDay(value);
  };

  const weekGroups = [];
  for (const day of byDay) {
    const week = Number(day.week_index) || 1;
    const last = weekGroups[weekGroups.length - 1];
    if (!last || last.week !== week) weekGroups.push({ week, count: 1 });
    else last.count += 1;
  }

  const teamRows = [];
  for (const direction of report.directions || []) {
    for (const person of direction.members || []) {
      teamRows.push({ ...person, directionName: direction.name, directionColor: direction.color });
    }
  }

  const valueLevel = (hours, target) => {
    if (target <= 0) return "future";
    if (hours <= 0) return "bad";
    return hours + 0.01 >= target ? "ok" : "warn";
  };
  const underlogged = teamRows.filter((person) => {
    const actual = Number(person.hours_sprint) || 0;
    return expectedToDate > 0 && actual + 0.01 < expectedToDate;
  }).length;

  if (mini) {
    mini.innerHTML = `
      <span class="mini-chip">рабочих дней <strong>${fmtNumber(days.length)}</strong></span>
      <span class="mini-chip">план <strong>${fmtDuration(planHours)}</strong></span>
      <span class="mini-chip">отстают по списаниям <strong>${fmtNumber(underlogged)}</strong> чел.</span>`;
  }

  const weekHead = weekGroups
    .map(
      (group, index) =>
        `<th class="worklog-week-head${index ? " week-start" : ""}" colspan="${group.count}">Неделя ${fmtNumber(group.week)}</th>`
    )
    .join("");
  const dayHead = byDay
    .map((day, index) => {
      const prev = byDay[index - 1];
      const weekStart = prev && Number(prev.week_index) !== Number(day.week_index);
      return `<th class="worklog-day-head${weekStart ? " week-start" : ""}${day.is_today ? " is-today" : ""}" title="${escapeHtml(fmtDay(day.date))}">${weekdayLabel(day.date)}</th>`;
    })
    .join("");

  const rows = teamRows
    .map((person) => {
      const profile = people[person.name] || {};
      const actual = Number(person.hours_sprint) || 0;
      const totalLevel = valueLevel(actual, expectedToDate);
      const missingDays = byDay
        .filter((day) => {
          if (day.is_future) return false;
          const target = Number(day.expected_hours) || 0;
          const fullNorm = Number(worklogs.expected_hours_per_day) || 8;
          // Do not remind for today until the configured workday is complete.
          return !day.is_today || target + 0.01 >= fullNorm;
        })
        .map((day) => {
          const row = (day.people || []).find((item) => item.name === person.name) || {};
          const hours = Number(row.hours) || 0;
          const norm = Number(worklogs.expected_hours_per_day) || 8;
          return { day, missing: Math.max(0, norm - hours) };
        })
        .filter((item) => item.missing > 0.01);
      const missingTotal = missingDays.reduce((sum, item) => sum + item.missing, 0);
      const reminderText = missingDays.length
        ? [
            "Привет! Напоминаю о необходимости списать время.",
            "",
            "У тебя не хватает списаний:",
            ...missingDays.map(
              ({ day, missing }) =>
                `${weekdayLabel(day.date)} (${shortDate(day.date)}) — ${fmtNumber(missing)} ч`
            ),
            "",
            `Всего не хватает: ${fmtNumber(missingTotal)} ч`,
          ].join("\n")
        : "";
      const cells = byDay
        .map((day, index) => {
          const prev = byDay[index - 1];
          const weekStart = prev && Number(prev.week_index) !== Number(day.week_index);
          const row = (day.people || []).find((item) => item.name === person.name) || {};
          const hours = Number(row.hours) || 0;
          const level = row.level || (day.is_future ? "future" : valueLevel(hours, Number(day.expected_hours) || 0));
          const value = level === "future" ? "—" : fmtNumber(hours);
          const hint = level === "future"
            ? `${fmtDay(day.date)} · будущий рабочий день`
            : `${fmtDay(day.date)} · списано ${fmtDuration(hours)} · план ${fmtDuration(day.expected_hours)}`;
          return `<td class="worklog-day-cell tone-${escapeHtml(level)}${weekStart ? " week-start" : ""}${day.is_today ? " is-today" : ""}" title="${escapeHtml(hint)}"><span>${escapeHtml(value)}</span></td>`;
        })
        .join("");
      return `
        <tr data-name="${escapeHtml(person.name || "")}" data-total="${actual}">
          <td class="worklog-person-cell">
            ${personCell(person.name, person.avatar_url, { short: true, load: profile.load || person.load || null })}
            <span class="worklog-direction" style="--direction-color:${escapeHtml(person.directionColor || "var(--muted)")}">${escapeHtml(person.directionName || "")}</span>
          </td>
          ${cells}
          <td class="worklog-total-cell tone-${totalLevel}" title="Ожидается к текущему времени: ${escapeHtml(fmtDuration(expectedToDate))}"><span>${fmtNumber(actual)}</span></td>
          <td class="worklog-plan-cell"><span>${fmtNumber(planHours)}</span></td>
          <td class="worklog-reminder-cell">
            ${reminderText ? `<button type="button" class="worklog-reminder-btn" data-worklog-reminder="${escapeHtml(person.name)}" title="Будет скопировано сообщение и выполнен переход в профиль сотрудника в мессенджере">Напомнить</button>` : "—"}
          </td>
        </tr>`;
    })
    .join("");

  const reminders = new Map();
  for (const person of teamRows) {
    const completedMissing = byDay
      .filter((day) => {
        if (day.is_future) return false;
        const target = Number(day.expected_hours) || 0;
        const norm = Number(worklogs.expected_hours_per_day) || 8;
        return !day.is_today || target + 0.01 >= norm;
      })
      .map((day) => {
        const row = (day.people || []).find((item) => item.name === person.name) || {};
        const norm = Number(worklogs.expected_hours_per_day) || 8;
        return { day, missing: Math.max(0, norm - (Number(row.hours) || 0)) };
      })
      .filter((item) => item.missing > 0.01);
    if (!completedMissing.length) continue;
    const total = completedMissing.reduce((sum, item) => sum + item.missing, 0);
    reminders.set(person.name, {
      text: [
        "Привет! Напоминаю о необходимости списать время.",
        "",
        "У тебя не хватает списаний:",
        ...completedMissing.map(
          ({ day, missing }) =>
            `${weekdayLabel(day.date)} (${shortDate(day.date)}) — ${fmtNumber(missing)} ч`
        ),
        "",
        `Всего не хватает: ${fmtNumber(total)} ч`,
        "",
        `[Отчёт](${reportUrl}) на дату ${reportCollectedAt}`,
      ].join("\n"),
      messengerUrl: person.messenger_url || people[person.name]?.messenger_url || "",
    });
  }

  root.innerHTML = `
    <div class="worklog-matrix-wrap" role="region" aria-label="Списания сотрудников по рабочим дням спринта" tabindex="0">
      <table class="js-sortable worklog-matrix" style="min-width:${27.45 + days.length * 3}rem">
        <thead>
          <tr>
            <th class="sortable worklog-person-head" rowspan="2" data-sort="name" data-type="string">Сотрудник</th>
            ${weekHead}
            <th class="sortable worklog-total-head" rowspan="2" data-sort="total" data-type="number">Итого</th>
            <th class="worklog-plan-head" rowspan="2">План</th>
            <th class="worklog-reminder-head" rowspan="2">Действие</th>
          </tr>
          <tr>${dayHead}</tr>
        </thead>
        <tbody>${rows || `<tr><td colspan="${days.length + 4}" class="muted">Нет сотрудников</td></tr>`}</tbody>
      </table>
    </div>`;
  bindSortableTables(root);
  const copyText = async (value) => {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return;
    }
    const area = document.createElement("textarea");
    area.value = value;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    if (!copied) throw new Error("copy failed");
  };
  root.querySelectorAll("[data-worklog-reminder]").forEach((button) => {
    button.addEventListener("click", async () => {
      const reminder = reminders.get(button.dataset.worklogReminder);
      if (!reminder) return;
      try {
        const copyPromise = copyText(reminder.text);
        if (reminder.messengerUrl) {
          window.open(reminder.messengerUrl, "_blank", "noopener,noreferrer");
        }
        await copyPromise;
        button.textContent = "Скопировано";
        button.classList.add("is-copied");
        if (!reminder.messengerUrl) {
          button.title = "Сообщение скопировано; messenger_url сотрудника не настроен";
        }
        window.setTimeout(() => {
          button.textContent = "Напомнить";
          button.classList.remove("is-copied");
        }, 2200);
      } catch (_error) {
        button.textContent = "Не скопировано";
        button.classList.add("is-error");
      }
    });
  });
}

function renderRisks(risks) {
  const filters = {
    outsideSprint: true,
    withoutDate: true,
  };

  function visibleItems(items) {
    return (items || []).filter((item) => {
      if (!filters.outsideSprint && item.release_outside_sprint) return false;
      if (!filters.withoutDate && item.release_without_date) return false;
      return true;
    });
  }

  function block(title, sourceItems, empty) {
    const allItems = sourceItems || [];
    const items = visibleItems(allItems);
    const count = items.length;
    const countText =
      count === allItems.length
        ? count
          ? `${count} задач`
          : "пусто"
        : `${count} из ${allItems.length} задач`;
    const rows = items
      .map(
        (item) => `
      <tr data-assignee="${escapeHtml(item.assignee || "")}" data-direction="${escapeHtml(item.direction || "")}" data-status="${escapeHtml(item.status || "")}" data-hours="${item.hours || 0}" data-key="${escapeHtml(item.key || "")}">
        <td class="task-cell">${taskTitle(item)}</td>
        <td class="col-assignee">${personCell(item.assignee, item.avatar_url, {
          short: true,
          personKey: item.assignee_canonical || item.assignee,
          load: (currentSprintReport?.people || {})[item.assignee_canonical || item.assignee]?.load,
        })}</td>
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
        <p class="risk-count">${countText}</p>
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
  function paint() {
    const toolbar = `
      <div class="risk-filters" role="group" aria-label="Показывать риск-задачи">
        <span class="risk-filters-label">Показывать задачи:</span>
        <label class="risk-filter-toggle">
          <input type="checkbox" data-risk-filter="outsideSprint" ${filters.outsideSprint ? "checked" : ""}>
          <span>Релиз вне спринта</span>
        </label>
        <label class="risk-filter-toggle">
          <input type="checkbox" data-risk-filter="withoutDate" ${filters.withoutDate ? "checked" : ""}>
          <span>Релиз без даты</span>
        </label>
      </div>`;
    root.innerHTML = toolbar + [
      block(
        "Могут не закрыться до конца спринта",
        risks.at_risk,
        "Критичных задач не видно"
      ),
      block(
        `Застрявшие (нет обновлений ≥ ${fmtNumber(risks.stale_days ?? currentSprintReport?.settings?.metrics?.stale_days ?? 5)} дн.)`,
        risks.stale,
        "Нет застрявших задач"
      ),
      block("Открыты без списаний", risks.no_worklogs, "Нет таких задач"),
      block(
        "Задачи без оценки",
        risks.no_estimate,
        "У всех открытых задач спринта есть оценка"
      ),
      block(
        "Задачи без релиза",
        risks.no_release,
        "У всех открытых задач спринта указан релиз"
      ),
      block(
        "Релиз выпущен — задача не закрыта",
        risks.released_not_closed,
        "Нет открытых задач в уже выпущенных релизах"
      ),
    ].join("");
    root.querySelectorAll("[data-risk-filter]").forEach((input) => {
      input.addEventListener("change", () => {
        filters[input.dataset.riskFilter] = input.checked;
        paint();
      });
    });
    bindSortableTables(root);
    bindCollapsibleTables(root);
  }
  paint();
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
  reportSearchIndex = null;
  renderSprintHeader(sr.sprint, sr.team_mood, sr.ai_brief);
  renderDirections(directions);
  renderReleases(sr.releases || []);
  renderEpicTimeline(sr.epic_timeline || {});
  renderDirectionDetails(directions);
  renderTeam(sr);
  renderRisks(sr.risks || {});
  renderRatings(sr.ratings || []);
  bindCollapseToggles(document.getElementById("report-root") || document);
  renderReportNav(directions);
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
    const searchNavBtn = event.target.closest("[data-report-search]");
    if (searchNavBtn) {
      event.preventDefault();
      openSearchModal();
      return;
    }
    const navBtn = event.target.closest("[data-nav-target]");
    if (navBtn) {
      event.preventDefault();
      scrollToReportSection(navBtn.getAttribute("data-nav-target"));
      return;
    }
    const closeEl = event.target.closest("[data-modal-close]");
    if (closeEl) {
      closeAppModal();
      return;
    }
    const searchTaskRow = event.target.closest(
      '.modal[data-modal-type="search"] .release-task-row'
    );
    if (
      searchTaskRow &&
      !event.target.closest("a, .issue-key-btn, .person-btn, .release-tag-btn, button")
    ) {
      const issueBtn = searchTaskRow.querySelector(".issue-key-btn[data-issue]");
      if (issueBtn) {
        event.preventDefault();
        openIssueModal(issueBtn.getAttribute("data-issue"), {
          jiraUrl: issueBtn.getAttribute("data-jira-url"),
        });
        return;
      }
    }
    const releaseAiCard = event.target.closest("[data-release-ai-card]");
    if (releaseAiCard) {
      // Issue/person links must fall through; expand btn and empty card area toggle.
      if (
        !event.target.closest(
          "a, .issue-key-btn, .person-btn, .ai-person-link, .release-tag-btn"
        )
      ) {
        event.preventDefault();
        event.stopPropagation();
        toggleReleaseAiBriefCard(releaseAiCard);
        return;
      }
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
    const epicBtn = event.target.closest(".issue-key-btn[data-epic]");
    if (epicBtn) {
      event.preventDefault();
      event.stopPropagation();
      openEpicModal(epicBtn.getAttribute("data-epic"));
      return;
    }
    const issueBtn = event.target.closest(".issue-key-btn[data-issue]");
    if (issueBtn) {
      event.preventDefault();
      event.stopPropagation();
      openIssueModal(issueBtn.getAttribute("data-issue"), {
        jiraUrl: issueBtn.getAttribute("data-jira-url"),
      });
      return;
    }
    const releaseTagBtn = event.target.closest(
      ".release-tag-btn[data-release-id], .epic-release-chip[data-release-id]"
    );
    if (releaseTagBtn) {
      event.preventDefault();
      event.stopPropagation();
      openReleaseModal(
        releaseTagBtn.getAttribute("data-release-id"),
        releaseTagBtn.getAttribute("data-release-name") || releaseTagBtn.textContent
      );
      return;
    }
    const epicRow = event.target.closest(
      ".epic-row[data-epic-key], tr.epic-table-row[data-epic-key]"
    );
    if (epicRow && !event.target.closest("a, button, .issue-key-btn, .person-btn")) {
      openEpicModal(epicRow.getAttribute("data-epic-key"));
      return;
    }
    const personRelease = event.target.closest("button.person-task-group-release");
    if (personRelease && !personRelease.classList.contains("is-static")) {
      event.preventDefault();
      event.stopPropagation();
      openReleaseModal(
        personRelease.getAttribute("data-release-id"),
        personRelease.getAttribute("data-release-name") || personRelease.textContent
      );
      return;
    }
    const releaseTile = event.target.closest(".issue-release-tile[data-release-id]");
    if (releaseTile) {
      event.preventDefault();
      event.stopPropagation();
      openReleaseModal(releaseTile.getAttribute("data-release-id"));
      return;
    }
    const releaseCard = event.target.closest(".release-card[data-release-id]");
    if (releaseCard && !event.target.closest("a, button, .issue-key-btn, .person-btn")) {
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
    const aiCard = event.target.closest(".ai-brief-card[data-ai-brief]");
    if (aiCard) {
      if (
        event.target.closest(
          "a, .issue-key-btn, .person-btn, .ai-person-link, .release-tag-btn"
        )
      ) {
        // fall through to issue/person handlers
      } else {
        openAiBriefModal(currentSprintReport?.ai_brief);
        return;
      }
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
      const releaseAiCard = event.target.closest?.("[data-release-ai-card]");
      if (releaseAiCard && !event.target.closest?.("a, .issue-key-btn, .person-btn")) {
        event.preventDefault();
        toggleReleaseAiBriefCard(releaseAiCard);
        return;
      }
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
      const aiCard = event.target.closest?.(".ai-brief-card[data-ai-brief]");
      if (
        aiCard &&
        !event.target.closest?.("a, .issue-key-btn, .person-btn, .ai-person-link")
      ) {
        event.preventDefault();
        openAiBriefModal(currentSprintReport?.ai_brief);
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
