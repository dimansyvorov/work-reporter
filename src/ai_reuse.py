from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from .ai_person_notes import _apply_notes_to_people
from .ai_release_briefs import _apply_briefs_to_releases, release_group_key


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _skipped(reason: str) -> dict:
    return {
        "status": "skipped",
        "reason": reason,
        "generated_at": None,
        "reused_at": _iso_now(),
        "error": None,
    }


def _same_sprint(current: dict, previous: dict) -> bool:
    current_id = ((current.get("sprint_report") or {}).get("sprint") or {}).get("id")
    previous_id = ((previous.get("sprint_report") or {}).get("sprint") or {}).get("id")
    return current_id is not None and previous_id is not None and str(current_id) == str(previous_id)


def reuse_previous_ai(report: dict, previous_report: dict | None) -> dict:
    """Reuse successful AI artifacts from the same sprint without any AI calls."""
    if not isinstance(report, dict):
        return report
    sprint_report = report.get("sprint_report")
    if not isinstance(sprint_report, dict):
        return report

    # The fresh metrics report normally has no embedded AI data. Clear it
    # explicitly anyway, so this mode can never leak an unmatched stale note.
    people = sprint_report.get("people")
    if isinstance(people, dict):
        for profile in people.values():
            if isinstance(profile, dict):
                profile.pop("ai_note", None)
    for release in sprint_report.get("releases") or []:
        if isinstance(release, dict):
            release.pop("ai_brief", None)

    if not isinstance(previous_report, dict):
        reason = "no_previous"
        sprint_report["ai_brief"] = _skipped(reason)
        sprint_report["ai_person_notes"] = {**_skipped(reason), "notes": {}}
        sprint_report["ai_release_briefs"] = {**_skipped(reason), "briefs": {}}
        return report

    if not _same_sprint(report, previous_report):
        reason = "different_sprint"
        sprint_report["ai_brief"] = _skipped(reason)
        sprint_report["ai_person_notes"] = {**_skipped(reason), "notes": {}}
        sprint_report["ai_release_briefs"] = {**_skipped(reason), "briefs": {}}
        return report

    previous_sr = previous_report.get("sprint_report") or {}
    reused_at = _iso_now()

    previous_brief = previous_sr.get("ai_brief")
    if isinstance(previous_brief, dict) and previous_brief.get("status") == "ok":
        brief = deepcopy(previous_brief)
        brief["reason"] = "reused"
        brief["reused_at"] = reused_at
        sprint_report["ai_brief"] = brief
    else:
        sprint_report["ai_brief"] = _skipped("no_previous")

    previous_notes = previous_sr.get("ai_person_notes")
    people = sprint_report.get("people") or {}
    if (
        isinstance(previous_notes, dict)
        and previous_notes.get("status") == "ok"
        and isinstance(previous_notes.get("notes"), dict)
    ):
        notes = {
            name: deepcopy(note)
            for name, note in previous_notes["notes"].items()
            if name in people and isinstance(note, dict)
        }
        bundle = deepcopy(previous_notes)
        bundle["notes"] = notes
        bundle["status"] = "ok" if notes else "skipped"
        bundle["reason"] = "reused" if notes else "no_previous"
        bundle["reused_at"] = reused_at
        sprint_report["ai_person_notes"] = bundle
        if notes:
            _apply_notes_to_people(sprint_report, notes)
    else:
        sprint_report["ai_person_notes"] = {**_skipped("no_previous"), "notes": {}}

    previous_releases = previous_sr.get("ai_release_briefs")
    current_group_keys = {
        release_group_key(release)
        for release in (sprint_report.get("releases") or [])
        if isinstance(release, dict) and release.get("name")
    }
    if (
        isinstance(previous_releases, dict)
        and previous_releases.get("status") == "ok"
        and isinstance(previous_releases.get("briefs"), dict)
    ):
        briefs = {}
        for key, old_brief in previous_releases["briefs"].items():
            if key not in current_group_keys or not isinstance(old_brief, dict):
                continue
            if old_brief.get("status") != "ok" or not old_brief.get("markdown"):
                continue
            one = deepcopy(old_brief)
            one["reason"] = "reused"
            one["reused_at"] = reused_at
            briefs[key] = one
        bundle = deepcopy(previous_releases)
        bundle["briefs"] = briefs
        bundle["status"] = "ok" if briefs else "skipped"
        bundle["reason"] = "reused" if briefs else "no_matching_releases"
        bundle["reused_at"] = reused_at
        bundle["ok_count"] = len(briefs)
        bundle["cache_count"] = 0
        bundle["total"] = len(current_group_keys)
        sprint_report["ai_release_briefs"] = bundle
        if briefs:
            _apply_briefs_to_releases(sprint_report, briefs)
    else:
        sprint_report["ai_release_briefs"] = {
            **_skipped("no_previous"),
            "briefs": {},
        }

    report["sprint_report"] = sprint_report
    return report
