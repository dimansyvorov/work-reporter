from __future__ import annotations

import json
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .state import AppState


class ReportHandler(SimpleHTTPRequestHandler):
    def __init__(
        self,
        *args,
        state: "AppState",
        web_dir: Path,
        on_refresh: Callable[[], bool] | None = None,
        **kwargs,
    ):
        self.state = state
        self.web_dir = web_dir
        self.on_refresh = on_refresh
        super().__init__(*args, directory=str(web_dir), **kwargs)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]

        if path == "/api/status":
            return self._json(self.state.snapshot())

        if path in {"/api/report", "/api/report.json"}:
            report = self.state.get_report()
            if report is None:
                return self._json(
                    {
                        "error": "Отчёт ещё не готов",
                        "status": self.state.snapshot(),
                    },
                    status=202,
                )
            return self._json(report)

        if path.endswith((".js", ".css", ".html", ".svg")) or path in {"/", "/index.html"}:
            if path in {"/", "/index.html"}:
                path = "/index.html"
            self.close_connection = True
            return self._serve_static_no_cache(path)

        return super().do_GET()

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/refresh":
            if self.on_refresh is None:
                return self._json({"ok": False, "error": "Обновление недоступно"}, status=500)
            if self.state.is_collecting():
                return self._json(
                    {
                        "ok": False,
                        "error": "Сбор данных уже выполняется",
                        "status": self.state.snapshot(),
                    },
                    status=409,
                )
            started = self.on_refresh()
            if not started:
                return self._json(
                    {
                        "ok": False,
                        "error": "Не удалось запустить обновление",
                        "status": self.state.snapshot(),
                    },
                    status=409,
                )
            return self._json({"ok": True, "status": self.state.snapshot()})

        self.send_error(404, "Not found")

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static_no_cache(self, path: str) -> None:
        file_path = self.translate_path(path)
        try:
            data = Path(file_path).read_bytes()
        except OSError:
            self.send_error(404, "File not found")
            return

        content_type = self.guess_type(file_path)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def serve_app(
    state: "AppState",
    *,
    host: str,
    port: int,
    web_dir: Path,
    open_browser: bool = True,
    on_refresh: Callable[[], bool] | None = None,
) -> None:
    handler = partial(
        ReportHandler, state=state, web_dir=web_dir, on_refresh=on_refresh
    )
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"

    print(f"Открываю: {url}")
    print("Остановка: Ctrl+C")

    if open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        server.server_close()
