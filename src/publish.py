from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from .config import DATA_DIR, ROOT


REQUIRED_WEB_FILES = (
    "index.html",
    "app.js",
    "styles.css",
    "favicon.svg",
)

AVATAR_EXT_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}


class PublishError(RuntimeError):
    pass


def validate_report(report_path: Path) -> dict:
    if not report_path.exists():
        raise PublishError(f"Нет файла отчёта: {report_path}")
    if report_path.stat().st_size < 200:
        raise PublishError("report.json слишком маленький — похоже на битый файл")

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PublishError(f"report.json невалидный JSON: {exc}") from exc

    if not isinstance(report, dict):
        raise PublishError("report.json: ожидается объект верхнего уровня")

    if report.get("error") and not report.get("sprint_report"):
        raise PublishError(f"Отчёт с ошибкой, публикация отменена: {report.get('error')}")

    sr = report.get("sprint_report")
    if not isinstance(sr, dict):
        raise PublishError("В report.json нет sprint_report")

    sprint = sr.get("sprint")
    if not isinstance(sprint, dict) or not (sprint.get("name") or "").strip():
        raise PublishError("В отчёте нет корректного sprint.name")

    meta = report.get("meta")
    if not isinstance(meta, dict) or not meta.get("fetched_at"):
        raise PublishError("В отчёте нет meta.fetched_at")

    if not isinstance(sr.get("directions"), list):
        raise PublishError("В отчёте нет directions")

    # Soft checks — warn via exception only if clearly broken UI payload
    if sr.get("team_mood") is not None and not isinstance(sr.get("team_mood"), dict):
        raise PublishError("team_mood имеет неверный формат")

    return report


def validate_web_dir(web_dir: Path) -> None:
    missing = [name for name in REQUIRED_WEB_FILES if not (web_dir / name).exists()]
    if missing:
        raise PublishError(f"В web/ не хватает файлов: {', '.join(missing)}")


def _collect_avatar_urls(obj) -> set[str]:
    found: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            url = node.get("avatar_url")
            if isinstance(url, str):
                text = url.strip()
                if text.startswith(("http://", "https://")):
                    found.add(text)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return found


def _rewrite_avatar_urls(obj, mapping: dict[str, str]):
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key == "avatar_url" and isinstance(value, str) and value in mapping:
                out[key] = mapping[value]
            else:
                out[key] = _rewrite_avatar_urls(value, mapping)
        return out
    if isinstance(obj, list):
        return [_rewrite_avatar_urls(item, mapping) for item in obj]
    return obj


def _guess_ext(url: str, content_type: str | None) -> str:
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime in AVATAR_EXT_BY_MIME:
        return AVATAR_EXT_BY_MIME[mime]
    path_ext = Path(urlparse(url).path).suffix.lower()
    if path_ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
        return ".jpg" if path_ext == ".jpeg" else path_ext
    guessed = mimetypes.guess_extension(mime or "") or ".bin"
    return ".jpg" if guessed == ".jpe" else guessed


def _avatar_request_headers(url: str) -> dict[str, str]:
    headers = {"Accept": "image/*,*/*;q=0.8"}
    host = (urlparse(url).hostname or "").lower()
    gitlab_url = (os.getenv("GITLAB_URL") or "").rstrip("/").lower()
    jira_url = (os.getenv("JIRA_URL") or "").rstrip("/").lower()
    gitlab_host = urlparse(gitlab_url).hostname if gitlab_url else None
    jira_host = urlparse(jira_url).hostname if jira_url else None

    if gitlab_host and host == gitlab_host.lower():
        token = (os.getenv("GITLAB_TOKEN") or "").strip()
        if token:
            headers["PRIVATE-TOKEN"] = token
    if jira_host and host == jira_host.lower():
        token = (os.getenv("JIRA_TOKEN") or "").strip()
        email = (os.getenv("JIRA_EMAIL") or "").strip()
        user = (os.getenv("JIRA_USER") or "").strip()
        if token and email:
            # Cloud basic auth style is rare for avatars; keep bearer primary
            headers["Authorization"] = f"Bearer {token}"
        elif token:
            headers["Authorization"] = f"Bearer {token}"
        elif user and token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def localize_avatars(report: dict, avatars_dir: Path) -> tuple[dict, dict[str, str]]:
    """
    Download remote avatar_url values into avatars_dir and rewrite report links
    to /report/avatars/<file>.
    """
    load_dotenv(ROOT / ".env")
    urls = sorted(_collect_avatar_urls(report))
    if not urls:
        return report, {}

    avatars_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = DATA_DIR / "avatars_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    mapping: dict[str, str] = {}
    ok = 0
    failed = 0

    for url in urls:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        # Probe cache first (any extension)
        cached = next(iter(sorted(cache_dir.glob(f"{digest}.*"))), None)
        if cached and cached.is_file() and cached.stat().st_size > 0:
            dest = avatars_dir / cached.name
            shutil.copy2(cached, dest)
            mapping[url] = f"/report/avatars/{dest.name}"
            ok += 1
            continue

        try:
            resp = requests.get(
                url,
                headers=_avatar_request_headers(url),
                timeout=25,
                allow_redirects=True,
            )
            if resp.status_code >= 400 or not resp.content:
                raise PublishError(f"HTTP {resp.status_code}")
            ext = _guess_ext(url, resp.headers.get("Content-Type"))
            filename = f"{digest}{ext}"
            cached_path = cache_dir / filename
            cached_path.write_bytes(resp.content)
            dest = avatars_dir / filename
            shutil.copy2(cached_path, dest)
            mapping[url] = f"/report/avatars/{filename}"
            ok += 1
        except Exception as exc:  # noqa: BLE001 - keep publish resilient
            failed += 1
            print(f"  ! avatar skip: {url[:90]}… ({exc})")

    print(f"Аватары: скачано/из кэша {ok}, ошибок {failed}, уникальных URL {len(urls)}")
    if not mapping:
        return report, mapping
    return _rewrite_avatar_urls(report, mapping), mapping


def _build_published_index(source_html: str) -> str:
    config_tag = (
        '<script>window.REPORT_CONFIG='
        '{"base":"/report","publish":true};'
        "</script>\n"
    )
    html = source_html
    # Rewrite absolute asset paths to /report/...
    html = html.replace('href="/favicon.svg"', 'href="/report/favicon.svg"')
    html = html.replace('href="/styles.css"', 'href="/report/styles.css"')
    html = html.replace('src="/app.js"', 'src="/report/app.js"')
    if "window.REPORT_CONFIG" not in html:
        if "</head>" in html:
            html = html.replace("</head>", f"  {config_tag}</head>", 1)
        else:
            html = config_tag + html
    # Eyebrow text for published copy
    html = html.replace(
        "Локальный отчёт · GitLab + Jira",
        "Спринтовый отчёт",
    )
    html = html.replace(
        'id="refresh-btn" class="refresh-btn"',
        'id="refresh-btn" class="refresh-btn hidden"',
    )
    return html


def stage_publish_bundle(
    *,
    report_path: Path,
    web_dir: Path,
    staging_dir: Path,
) -> dict:
    validate_web_dir(web_dir)
    report = validate_report(report_path)

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    # Static assets
    for name in REQUIRED_WEB_FILES:
        if name == "index.html":
            continue
        shutil.copy2(web_dir / name, staging_dir / name)

    index_html = _build_published_index((web_dir / "index.html").read_text(encoding="utf-8"))
    (staging_dir / "index.html").write_text(index_html, encoding="utf-8")

    avatars_dir = staging_dir / "avatars"
    report, avatar_map = localize_avatars(report, avatars_dir)

    api_dir = staging_dir / "api"
    api_dir.mkdir(parents=True)
    payload = json.dumps(report, ensure_ascii=False)
    (api_dir / "report.json").write_text(payload, encoding="utf-8")
    # Also expose as api/report (no extension) for parity with local API
    (api_dir / "report").write_text(payload, encoding="utf-8")

    # Tiny ready marker for ops/debug
    (staging_dir / "publish.json").write_text(
        json.dumps(
            {
                "ok": True,
                "sprint": (report.get("sprint_report") or {}).get("sprint", {}).get("name"),
                "fetched_at": (report.get("meta") or {}).get("fetched_at"),
                "avatars": len(avatar_map),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return report


def publish_report(
    *,
    report_path: Path | None = None,
    web_dir: Path | None = None,
    ssh_host: str,
    remote_dir: str,
    delete_raw: bool = True,
) -> dict:
    report_path = report_path or (DATA_DIR / "report.json")
    web_dir = web_dir or (ROOT / "web")
    raw_path = DATA_DIR / "raw.json"

    with tempfile.TemporaryDirectory(prefix="sprint-report-publish-") as tmp:
        staging = Path(tmp) / "bundle"
        report = stage_publish_bundle(
            report_path=report_path,
            web_dir=web_dir,
            staging_dir=staging,
        )

        remote = remote_dir.rstrip("/")
        # IMPORTANT: do not replace the directory inode (breaks Docker bind mounts).
        # Publish into a staging subdir, then replace *contents* of the live dir.
        remote_tmp = f"{remote}/.incoming"
        subprocess.run(
            [
                "ssh",
                ssh_host,
                (
                    f"mkdir -p {shlex.quote(remote)} && "
                    f"rm -rf {shlex.quote(remote_tmp)} && "
                    f"mkdir -p {shlex.quote(remote_tmp)}"
                ),
            ],
            check=True,
        )
        tar_upload = (
            f"COPYFILE_DISABLE=1 tar -C {shlex.quote(str(staging))} "
            f"--exclude='._*' --exclude='.DS_Store' -czf - . | "
            f"ssh {shlex.quote(ssh_host)} "
            f"tar -C {shlex.quote(remote_tmp)} -xzf -"
        )
        subprocess.run(["bash", "-lc", tar_upload], check=True)
        activate_cmd = (
            f"find {shlex.quote(remote)} -mindepth 1 -maxdepth 1 "
            f"! -name '.incoming' -exec rm -rf {{}} + && "
            f"shopt -s dotglob && "
            f"mv {shlex.quote(remote_tmp)}/* {shlex.quote(remote)}/ && "
            f"rmdir {shlex.quote(remote_tmp)}"
        )
        # bash on remote for shopt
        subprocess.run(
            ["ssh", ssh_host, f"bash -lc {shlex.quote(activate_cmd)}"],
            check=True,
        )

    if delete_raw and raw_path.exists():
        raw_path.unlink()
        print(f"Удалён локальный {raw_path.relative_to(ROOT)}")

    return report
