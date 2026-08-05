#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.collector import collect_and_build
from src.config import DATA_DIR, ROOT as PROJECT_ROOT, load_config
from src.publish import PublishError, publish_report, validate_report
from src.server import serve_app
from src.state import AppState


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Спринтовый отчёт GitLab/Jira (локально / publish на сервер)."
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Использовать демо-данные без доступа к API.",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Не открывать браузер автоматически.",
    )
    parser.add_argument(
        "--no-collect",
        action="store_true",
        help=(
            "Открыть UI на существующем data/report.json без нового сбора "
            "(кнопка «Обновить данные» по-прежнему запускает сбор)."
        ),
    )
    parser.add_argument(
        "--dump-only",
        action="store_true",
        help="Только сохранить JSON, без веб-сервера.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Собрать отчёт, провалидировать и опубликовать на сервер.",
    )
    parser.add_argument(
        "--publish-only",
        action="store_true",
        help="Опубликовать уже существующий data/report.json без нового сбора.",
    )
    return parser.parse_args()


def _print_summary(report: dict) -> None:
    if report.get("error"):
        print(f"Ошибка отчёта: {report['error']}")

    sr = report.get("sprint_report")
    if not sr:
        return

    s = sr["sprint"]
    print(
        f"Спринт: {s['name']} [{s['state_label']}] "
        f"{s.get('start_date')} — {s.get('end_date')} · "
        f"задачи {s.get('tasks_progress_pct')}% · "
        f"прохождение {s.get('time_progress_pct')}%"
    )
    dirs = ", ".join(
        f"{d['name']}={d['people_count']}" for d in (sr.get("directions") or [])
    )
    if dirs:
        print(f"Направления: {dirs}")
    risks = sr.get("risks") or {}
    print(
        "Риски:",
        f"срыв={len(risks.get('at_risk') or [])}",
        f"застряли={len(risks.get('stale') or [])}",
        f"без_списаний={len(risks.get('no_worklogs') or [])}",
    )


def _publish_settings() -> tuple[str, str]:
    ssh_host = (os.getenv("PUBLISH_SSH") or "server").strip()
    remote_dir = (os.getenv("PUBLISH_REMOTE_DIR") or "").strip()
    if not remote_dir:
        raise SystemExit(
            "Задайте PUBLISH_REMOTE_DIR в .env "
            "(например /home/USER/path/to/sprint-report)."
        )
    return ssh_host, remote_dir


def _format_fetched_local(fetched: str | None) -> str:
    """UTC/ISO fetched_at → local `DD.MM.YYYY HH:MM`."""
    if not fetched:
        return "—"
    try:
        raw = str(fetched).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.astimezone()  # treat naive as local
        else:
            dt = dt.astimezone()
        return dt.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return str(fetched)


def _publish_mode_label() -> str:
    """Автоматически = LaunchAgent; Вручную = CLI --publish / --publish-only."""
    mode = (os.getenv("WORK_REPORTER_PUBLISH_MODE") or "").strip().lower()
    if mode in {"auto", "automatic", "launchd", "launchagent"}:
        return "Автоматически"
    return "Вручную"


def _remote_notify_script(raw: str | None) -> str:
    """
    Normalize notify script path for execution on the publish SSH host.

    LaunchAgent sources .env with bash, which expands ~/… to the Mac home.
    The script must run on the server, so rewrite local $HOME prefixes back to ~/.
    """
    script = (raw or "~/work-reporter-notify/notify-telegram.sh").strip()
    if not script:
        return ""
    home = str(Path.home())
    if script == home or script.startswith(home + "/"):
        script = "~" + script[len(home) :]
    return script


def _notify_remote(status: str, text: str) -> None:
    """
    Ask the home server to call Telegram API.
    Secrets and network access to Telegram live only on the server.
    """
    ssh_host = (os.getenv("PUBLISH_SSH") or "server").strip()
    script = _remote_notify_script(os.getenv("PUBLISH_NOTIFY_SCRIPT"))
    if not script:
        return
    # Preserve newlines; remote script takes status + one message argument.
    clean = str(text or "").replace("\r\n", "\n").strip()
    if len(clean) > 900:
        clean = clean[:897] + "…"
    remote = f"{script} {shlex.quote(status)} {shlex.quote(clean)}"
    try:
        completed = subprocess.run(
            ["ssh", ssh_host, f"bash -lc {shlex.quote(remote)}"],
            check=False,
            timeout=60,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "").strip()
            print(
                f"  ! telegram notify failed (exit {completed.returncode})"
                + (f": {err}" if err else "")
            )
    except Exception as exc:  # noqa: BLE001 - notify must not break publish
        print(f"  ! telegram notify failed: {exc}")


def _success_notify_text(*, fetched_at: str | None, public_url: str) -> str:
    url = (public_url or "").strip() or "—"
    lines = [
        f"Отчёт обновлён: {url}",
        f"Дата сборки: {_format_fetched_local(fetched_at)}",
        f"Режим: {_publish_mode_label()}",
    ]
    return "\n".join(lines)


def _run_publish(*, collected: bool) -> int:
    report_path = DATA_DIR / "report.json"
    try:
        report = validate_report(report_path)
    except PublishError as exc:
        print(f"Публикация отменена: {exc}")
        _notify_remote("error", f"Публикация отменена: {exc}")
        return 1

    ssh_host, remote_dir = _publish_settings()
    print(
        f"Публикую на {ssh_host}:{remote_dir}"
        + (" (после свежего сбора)" if collected else " (существующий report.json)")
    )
    try:
        published = publish_report(
            report_path=report_path,
            web_dir=PROJECT_ROOT / "web",
            ssh_host=ssh_host,
            remote_dir=remote_dir,
            delete_raw=True,
        )
    except PublishError as exc:
        print(f"Публикация отменена: {exc}")
        _notify_remote("error", f"Публикация отменена: {exc}")
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"Ошибка публикации: {exc}")
        _notify_remote("error", f"Ошибка публикации на сервер: {exc}")
        return 1

    sprint = ((published.get("sprint_report") or {}).get("sprint") or {}).get("name")
    fetched = (published.get("meta") or {}).get("fetched_at")
    print(f"Опубликовано: {sprint or '—'} · fetched_at={fetched or '—'}")
    public_url = (os.getenv("PUBLISH_PUBLIC_URL") or "").strip()
    if public_url:
        print(f"URL: {public_url}")
    _notify_remote(
        "success",
        _success_notify_text(fetched_at=fetched, public_url=public_url),
    )
    return 0


def main() -> None:
    args = parse_args()
    if args.publish and args.publish_only:
        print("Укажите либо --publish, либо --publish-only")
        raise SystemExit(2)
    if args.dump_only and (args.publish or args.publish_only):
        print("--dump-only нельзя совмещать с publish")
        raise SystemExit(2)
    if args.no_collect and (args.dump_only or args.publish or args.publish_only):
        print("--no-collect нельзя совмещать с --dump-only / --publish / --publish-only")
        raise SystemExit(2)

    cfg = load_config(mock=args.mock)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = DATA_DIR / "raw.json"
    report_path = DATA_DIR / "report.json"

    if args.publish_only:
        raise SystemExit(_run_publish(collected=False))

    if args.publish:
        state = AppState()
        try:
            report = collect_and_build(
                cfg, state, raw_path=raw_path, report_path=report_path
            )
        except BaseException as exc:
            print(f"Ошибка сбора: {exc}")
            _notify_remote("error", f"Ошибка сбора отчёта: {exc}")
            raise SystemExit(1) from exc
        print(f"Сохранено: {raw_path.relative_to(PROJECT_ROOT)}")
        print(f"Сохранено: {report_path.relative_to(PROJECT_ROOT)}")
        _print_summary(report)
        raise SystemExit(_run_publish(collected=True))

    if args.dump_only:
        state = AppState()
        report = collect_and_build(
            cfg, state, raw_path=raw_path, report_path=report_path
        )
        print(f"Сохранено: {raw_path.relative_to(PROJECT_ROOT)}")
        print(f"Сохранено: {report_path.relative_to(PROJECT_ROOT)}")
        _print_summary(report)
        return

    state = AppState()
    state.set_status("starting", "Открываю интерфейс…")
    state.add_step("ui", "Открытие страницы", "done")
    state.add_step("init", "Подготовка", "pending")

    def start_collection(label: str = "collector") -> bool:
        if state.is_collecting() and state.status != "starting":
            return False

        def worker() -> None:
            try:
                report = collect_and_build(
                    cfg, state, raw_path=raw_path, report_path=report_path
                )
                print(f"Сохранено: {raw_path.relative_to(PROJECT_ROOT)}")
                print(f"Сохранено: {report_path.relative_to(PROJECT_ROOT)}")
                _print_summary(report)
            except BaseException as exc:
                # Errors are already stored in AppState by collector.
                print(f"Ошибка сбора: {exc}")

        threading.Thread(target=worker, name=label, daemon=True).start()
        return True

    def on_refresh() -> bool:
        return start_collection("collector-refresh")

    if args.no_collect:
        if not report_path.is_file():
            print(
                f"Нет {report_path.relative_to(PROJECT_ROOT)} — "
                "сначала соберите отчёт (python run.py --dump-only) "
                "или уберите --no-collect."
            )
            raise SystemExit(1)
        try:
            existing = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Не удалось прочитать {report_path.name}: {exc}")
            raise SystemExit(1) from exc
        if not isinstance(existing, dict) or existing.get("error"):
            print(
                f"{report_path.name} повреждён или содержит ошибку — "
                "уберите --no-collect и пересоберите."
            )
            raise SystemExit(1)
        state.set_ready(existing, "Локальный отчёт (без нового сбора)")
        state.add_step("init", "Подготовка", "done")
        print(
            f"UI без сбора: {report_path.relative_to(PROJECT_ROOT)} "
            "(«Обновить данные» запустит полный collect)"
        )
    else:
        start_collection("collector")

    serve_app(
        state,
        host=cfg.host,
        port=cfg.port,
        web_dir=PROJECT_ROOT / "web",
        open_browser=not args.no_open,
        on_refresh=on_refresh,
    )


if __name__ == "__main__":
    main()
