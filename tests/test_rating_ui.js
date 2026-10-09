const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const app = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");

function functionSource(name, nextName) {
  const start = app.indexOf(`function ${name}(`);
  const end = app.indexOf(`function ${nextName}(`, start);
  assert.ok(start >= 0 && end > start);
  return app.slice(start, end);
}

function render(stability, categoryId = "stability") {
  const person = {
    name: "Тестовый Сотрудник",
    direction: "Тестирование",
    place: 1,
    value: "97 баллов",
    detail: "Обычный рейтинг",
    stability,
  };
  const category = {
    id: categoryId,
    enabled: true,
    title: "Тестовый рейтинг",
    people: [person],
    all_people: [person],
  };
  const root = { innerHTML: "" };
  let modal = "";
  const context = vm.createContext({
    currentSprintReport: { ratings: [category] },
    category,
    document: {
      getElementById: (id) => id === "ratings" ? root : {
        classList: { add() {}, remove() {} },
      },
    },
    escapeHtml: (value) => String(value).replace(/[&<>"']/g, ""),
    fmtNumber: (value) => value == null ? "—" : String(value),
    medalHtml: () => "",
    personCell: () => "Тестовый Сотрудник",
    findPersonByName: () => null,
    openAppModal: (html) => { modal = html; },
  });
  vm.runInContext(functionSource("openRatingModal", "openPersonTasksModal"), context);
  vm.runInContext(functionSource("renderRatings", "releaseStatusBadge"), context);
  vm.runInContext("openRatingModal(category.id); renderRatings([category]);", context);
  return { modal, card: root.innerHTML };
}

function stability(overrides = {}) {
  return {
    pace_pct: 100,
    timely_pct: 90,
    recording_timing: {
      same_day: { pct: 0, hours: 0 },
      next_workday: { pct: 100, hours: 80 },
      later: { pct: 0, hours: 0 },
      before_workday: { pct: 0, hours: 0 },
    },
    cadence_pct: null,
    advance_hours: 0,
    advance_days: 0,
    closed_tasks: 3,
    ...overrides,
  };
}

test("modal separates weighted timeliness from actual hours by recording date", () => {
  const { modal, card } = render(stability());
  assert.match(modal, /Своевременность <strong>90\.0%<\/strong>/);
  assert.match(modal, /День в день <strong>0\.0%<\/strong> · 0 ч/);
  assert.match(modal, /На следующий рабочий день <strong>100\.0%<\/strong> · 80 ч/);
  assert.match(modal, /Позже <strong>0\.0%<\/strong> · 0 ч/);
  assert.match(card, /сроки 90\.0%/);
  assert.doesNotMatch(modal + card, /[Вв]овремя/);
  assert.doesNotMatch(modal, /До даты работ/);
});

test("uncomputed cadence is hidden, including in older snapshots", () => {
  assert.doesNotMatch(render(stability()).modal, /Равномерность/);
  assert.doesNotMatch(render(stability({ cadence_pct: undefined })).modal, /Равномерность/);
});

test("computed zero cadence remains visible", () => {
  assert.match(render(stability({ cadence_pct: 0 })).modal, /Равномерность <strong>0\.0%<\/strong>/);
});

test("missing recording timestamps are not presented as zero shares", () => {
  const { modal } = render(stability({ timely_pct: null, recording_timing: null }));
  assert.match(modal, /Нет данных о сроках внесения/);
  assert.doesNotMatch(modal, /День в день|На следующий рабочий день/);
});

test("early entries have their own breakdown row and retain advance details", () => {
  const data = stability({ advance_hours: 8, advance_days: 1 });
  data.recording_timing.before_workday = { pct: 10, hours: 8 };
  data.recording_timing.next_workday = { pct: 90, hours: 72 };
  const { modal } = render(data);
  assert.match(modal, /До даты работ <strong>10\.0%<\/strong> · 8 ч/);
  assert.match(modal, /Наперёд <strong>8 ч<\/strong> за 1 дн\./);
});

test("other rating categories keep their existing detail", () => {
  const { modal, card } = render(undefined, "closer");
  assert.match(modal, /Обычный рейтинг/);
  assert.match(card, /Обычный рейтинг/);
  assert.doesNotMatch(modal, /Своевременность|День в день|Равномерность/);
});
