from __future__ import annotations


RELEASE_PUBLICATION_POLICY = {
    "usual_publication_window": "вечером после завершения рабочего дня в плановую дату",
    "release_day_rule": (
        "В плановую дату released=false — штатное состояние до вечернего окна выпуска, "
        "а не факт задержки."
    ),
    "overdue_rule": (
        "Публикация считается просроченной только со следующего календарного дня, "
        "если релиз всё ещё не выпущен."
    ),
    "readiness_rule": (
        "Риски и незавершённые задачи показывают готовность к вечернему выпуску, "
        "но сами по себе не доказывают, что публикация уже опоздала."
    ),
}


def release_publication_facts(release: dict | None) -> dict:
    """Prepare publication facts for LLM snapshots without external requests."""
    row = release if isinstance(release, dict) else {}
    released = bool(row.get("released"))
    try:
        days_left = int(row.get("days_left"))
    except (TypeError, ValueError):
        days_left = None

    if released:
        return {
            "phase": "released",
            "release_day": days_left == 0,
            "publication_expected_this_evening": False,
            "publication_overdue": False,
            "model_fact": "Релиз уже выпущен; повторно оценивать публикацию не нужно.",
        }
    if days_left is None:
        return {
            "phase": "date_unknown",
            "release_day": False,
            "publication_expected_this_evening": False,
            "publication_overdue": None,
            "model_fact": "Недостаточно данных, чтобы определить срок публикации.",
        }
    if days_left < 0:
        return {
            "phase": "past_release_day",
            "release_day": False,
            "publication_expected_this_evening": False,
            "publication_overdue": True,
            "model_fact": "Плановая дата прошла, а релиз не выпущен: публикация просрочена.",
        }
    if days_left == 0:
        return {
            "phase": "release_day",
            "release_day": True,
            "publication_expected_this_evening": True,
            "publication_overdue": False,
            "model_fact": (
                "Сегодня плановая дата. Выпуск обычно выполняется вечером после рабочего "
                "дня; отсутствие публикации сейчас не является задержкой."
            ),
        }
    return {
        "phase": "before_release_day",
        "release_day": False,
        "publication_expected_this_evening": False,
        "publication_overdue": False,
        "model_fact": f"До плановой даты осталось {days_left} дн.; публикация ещё не ожидается.",
    }
