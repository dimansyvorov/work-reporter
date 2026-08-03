from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .errors import CollectError
from .gitlab_client import collect_raw as collect_gitlab_raw
from .gitlab_client import enrich_mr_commit_counts
from .jira_client import collect_jira_raw
from .metrics import compute_report
from .mock_data import build_mock_raw
from .state import AppState
from .team_config import load_team_config


def _friendly_error(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()
    vpn_hints = (
        "vpn",
        "connection",
        "connect",
        "timed out",
        "timeout",
        "name or service not known",
        "nodename nor servname",
        "network is unreachable",
        "failed to resolve",
        "max retries",
        "connection refused",
        "temporary failure",
        "jira request failed",
        "gitlab request failed",
    )
    if any(h in lowered for h in vpn_hints):
        return (
            "Не удалось подключиться к GitLab/Jira.\n"
            "Проверьте VPN и доступность корпоративных сервисов, затем нажмите «Обновить данные».\n\n"
            f"Детали: {text}"
        )
    return text


def _skip_remaining_jira(state: AppState, reason: str) -> None:
    for key in ("jira_sprint", "jira_issues", "jira_epics", "jira_worklogs"):
        snap = state.snapshot()
        current = next((s for s in snap["steps"] if s["key"] == key), None)
        if current and current["state"] == "pending":
            state.update_step(key, "skipped", reason)


def collect_and_build(
    cfg: Config,
    state: AppState,
    *,
    raw_path: Path,
    report_path: Path,
) -> dict:
    if not state.begin_collection("Собираю данные…"):
        existing = state.get_report()
        if existing is not None:
            return existing
        raise CollectError("Сбор данных уже выполняется")

    def on_progress(
        step: str,
        message: str,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        state.run_step(step, message)
        state.set_status("collecting", message)

    try:
        state.run_step("init", "Подготовка: читаю конфиг команды…")
        if cfg.mock:
            from pathlib import Path

            example = Path(__file__).resolve().parents[1] / "team.json.example"
            if example.exists():
                load_team_config(example, reload=True)
                state.update_step(
                    "init",
                    "done",
                    "Подготовка · демо-конфиг team.json.example",
                )
            else:
                load_team_config(reload=True)
                state.update_step("init", "done", "Подготовка")
        else:
            load_team_config(reload=True)
            state.update_step("init", "done", "Подготовка")
        raw_path.parent.mkdir(parents=True, exist_ok=True)

        if cfg.mock:
            state.run_step("jira_sprint", "Демо: генерация данных…")
            state.set_status("collecting", "Готовлю демо-данные…")
            time.sleep(0.4)
            raw = build_mock_raw(days=cfg.days)
            jira = raw.get("jira") or {}
            gl = raw.get("gitlab") or {}
            mr_count = sum(
                len(p.get("merge_requests_merged") or [])
                + len(p.get("merge_requests_open") or [])
                for p in (gl.get("projects") or [])
            )
            state.update_step("jira_sprint", "done", "Демо: спринт")
            state.update_step(
                "jira_issues",
                "done",
                f"Демо: {len(jira.get('issues') or [])} задач",
            )
            state.update_step(
                "jira_epics",
                "done",
                f"Демо: {len(jira.get('epics') or {})} эпиков",
            )
            state.update_step(
                "jira_worklogs",
                "done",
                f"Демо: {len(jira.get('worklogs') or [])} списаний",
            )
            state.update_step("gitlab", "done", f"Демо: {mr_count} MR")
            state.update_step("commits", "done", "Демо: коммиты")
            time.sleep(0.3)
        else:
            raw = {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "gitlab": None,
                "jira": None,
            }

            if cfg.jira_enabled:
                state.run_step("jira_sprint", "Jira: подключаюсь…")
                state.set_status("collecting", "Загружаю данные из Jira…")

                def jira_progress(step, message, current=None, total=None):
                    on_progress(step, message, current=current, total=total)
                    # mark prior jira phases done when moving forward
                    order = [
                        "jira_sprint",
                        "jira_issues",
                        "jira_epics",
                        "jira_worklogs",
                    ]
                    if step in order:
                        idx = order.index(step)
                        for prev in order[:idx]:
                            snap = state.snapshot()
                            cur = next(
                                (s for s in snap["steps"] if s["key"] == prev), None
                            )
                            if cur and cur["state"] == "running":
                                state.update_step(prev, "done", cur["label"])

                raw["jira"] = collect_jira_raw(cfg, on_progress=jira_progress)
                jira = raw["jira"] or {}
                state.update_step(
                    "jira_sprint",
                    "done",
                    f"Jira: {(jira.get('sprint') or {}).get('name') or 'спринт'}",
                )
                state.update_step(
                    "jira_issues",
                    "done",
                    f"Jira: {len(jira.get('issues') or [])} задач",
                )
                state.update_step(
                    "jira_epics",
                    "done",
                    f"Jira: {len(jira.get('epics') or {})} эпиков",
                )
                state.update_step(
                    "jira_worklogs",
                    "done",
                    f"Jira: {len(jira.get('worklogs') or [])} списаний",
                )
            else:
                _skip_remaining_jira(state, "Jira отключена")

            since = None
            if raw.get("jira") and raw["jira"].get("sprint", {}).get("start_at"):
                since = datetime.fromisoformat(
                    raw["jira"]["sprint"]["start_at"].replace("Z", "+00:00")
                )

            if cfg.gitlab_enabled:
                state.run_step("gitlab", "GitLab: загружаю MR…")
                state.set_status("collecting", "Загружаю merge requests из GitLab…")
                raw["gitlab"] = collect_gitlab_raw(
                    cfg, since=since, on_progress=on_progress
                )
                gl = raw["gitlab"] or {}
                mr_count = sum(
                    len(p.get("merge_requests_merged") or [])
                    + len(p.get("merge_requests_open") or [])
                    for p in (gl.get("projects") or [])
                )
                state.update_step("gitlab", "done", f"GitLab: {mr_count} MR")
            else:
                state.update_step("gitlab", "skipped", "GitLab пропущен")
                state.update_step("commits", "skipped", "Коммиты пропущены")

            if not raw["jira"]:
                raise CollectError(
                    "Для спринтового отчёта нужна Jira. Заполните JIRA_* в .env"
                )

            if raw.get("gitlab"):
                state.run_step("commits", "GitLab: считаю коммиты по MR…")
                state.set_status("collecting", "Считаю коммиты по задачам…")
                issue_keys = {
                    (issue.get("key") or "").upper()
                    for issue in (raw["jira"].get("issues") or [])
                    if issue.get("key")
                }
                enrich_mr_commit_counts(
                    cfg,
                    raw["gitlab"],
                    issue_keys=issue_keys,
                    jira_raw=raw.get("jira"),
                    on_progress=on_progress,
                )
                state.update_step("commits", "done", "GitLab: коммиты по MR")

        state.run_step("metrics", "Считаю метрики и рейтинги…")
        state.set_status("collecting", "Считаю метрики и рейтинги…")
        report = compute_report(raw)
        sr = (report or {}).get("sprint_report") or {}
        state.update_step(
            "metrics",
            "done",
            f"Метрики: {len(sr.get('directions') or [])} направлений, "
            f"{len(sr.get('ratings') or [])} рейтингов",
        )

        state.run_step("save", "Сохраняю отчёт…")
        state.set_status("collecting", "Сохраняю отчёт…")
        raw_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        state.update_step("save", "done", "Отчёт сохранён")

        state.set_ready(report, "Отчёт готов")
        return report

    except BaseException as exc:
        # SystemExit used to kill the worker silently — always surface the error.
        if isinstance(exc, KeyboardInterrupt):
            state.set_error("Сбор прерван")
            raise
        detail = _friendly_error(exc)
        traceback.print_exc()
        snap = state.snapshot()
        for step in snap["steps"]:
            if step["state"] == "running":
                state.update_step(step["key"], "error", step["label"])
        state.set_error(detail)
        raise CollectError(detail) from exc
