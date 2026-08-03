from __future__ import annotations

import re
import unicodedata

_TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def _transliterate(text: str) -> str:
    out = []
    for ch in text.lower():
        out.append(_TRANSLIT.get(ch, ch))
    return "".join(out)


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", name or "").strip().lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[^\w\s-]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


_PATRONYMIC_RE = re.compile(
    r"(ovich|evich|ovna|evna|ichna|inichna|yevich|yovich)$"
)


def name_tokens(name: str) -> list[str]:
    norm = normalize_name(name)
    latin = _transliterate(norm)
    return [t for t in re.split(r"[\s_-]+", latin) if len(t) >= 2]


def _is_patronymic(token: str) -> bool:
    return bool(_PATRONYMIC_RE.search(token or ""))


_NAME_VARIANTS = {
    "alexander": "aleksandr",
    "aleksandr": "alexander",
    "dmitry": "dmitriy",
    "dmitriy": "dmitry",
    "dmitrii": "dmitriy",
    "vasiliev": "vasilev",
    "vasilev": "vasiliev",
    "yury": "yuriy",
    "yuriy": "yury",
}


def _token_equiv(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    if _NAME_VARIANTS.get(a) == b or _NAME_VARIANTS.get(b) == a:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 4 and long.startswith(short)


def _given_prefix_match(a: str, b: str) -> bool:
    return _token_equiv(a, b)


def _surname_candidates(tokens: list[str]) -> set[str]:
    """Possible surnames; never treat patronymics as family names."""
    if not tokens:
        return set()
    if len(tokens) == 1:
        return set() if _is_patronymic(tokens[0]) else {tokens[0]}
    # First/last tokens cover RU «Фамилия Имя Отчество» and EN «First Last»
    out = set()
    for t in (tokens[0], tokens[-1]):
        if t and not _is_patronymic(t) and len(t) >= 4:
            out.add(t)
    return out


def names_match(a: str, b: str) -> bool:
    """
    Strict-ish person match for Jira/GitLab names.

    Requires surname equality (not patronymic / given-name-as-surname) plus
    given-name overlap. Avoids mapping one Дмитрий onto another.
    """
    if not a or not b:
        return False
    na, nb = normalize_name(a), normalize_name(b)
    if na == nb:
        return True
    if _transliterate(na) == _transliterate(nb):
        return True

    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return False

    sa, sb = _surname_candidates(ta), _surname_candidates(tb)
    # Pair surnames allowing latin spelling variants (vasilev↔vasiliev)
    surname_pairs: list[tuple[str, str]] = []
    for xa in sa:
        for xb in sb:
            if _token_equiv(xa, xb):
                surname_pairs.append((xa, xb))
    if not surname_pairs:
        return False

    for surname_a, surname_b in sorted(
        surname_pairs, key=lambda p: -max(len(p[0]), len(p[1]))
    ):
        rest_a = [t for t in ta if t != surname_a and not _is_patronymic(t)]
        rest_b = [t for t in tb if t != surname_b and not _is_patronymic(t)]
        if not rest_a or not rest_b:
            continue
        for ga in rest_a:
            for gb in rest_b:
                if _given_prefix_match(ga, gb):
                    return True
    return False


def resolve_avatar(
    display_name: str,
    *,
    local_avatar: str | None,
    gitlab_people: list[dict],
) -> str | None:
    if local_avatar:
        return local_avatar

    for person in gitlab_people:
        gname = person.get("name") or ""
        if names_match(display_name, gname):
            return person.get("avatar_url")
    return None


def best_gitlab_avatar(display_name: str, gitlab_people: list[dict]) -> str | None:
    from .team import canonical_team_name

    target = canonical_team_name(display_name) or display_name
    # Prefer exact/alias canonical match, then fuzzy name match
    for person in gitlab_people:
        gname = person.get("name") or ""
        if not gname:
            continue
        if canonical_team_name(gname) == target or names_match(display_name, gname):
            avatar = person.get("avatar_url")
            if avatar:
                return avatar
    return None


def collect_gitlab_people(gitlab_raw: dict | None) -> list[dict]:
    people: dict[str, dict] = {}
    if not gitlab_raw:
        return []
    for project in gitlab_raw.get("projects") or []:
        for bucket in (
            project.get("merge_requests_merged") or [],
            project.get("merge_requests_open") or [],
        ):
            for mr in bucket:
                author = mr.get("author") or {}
                name = (author.get("name") or "").strip()
                if not name:
                    continue
                key = normalize_name(name)
                entry = people.setdefault(
                    key,
                    {
                        "name": name,
                        "avatar_url": author.get("avatar_url"),
                        "projects": set(),
                        "mr_count": 0,
                    },
                )
                if author.get("avatar_url") and not entry.get("avatar_url"):
                    entry["avatar_url"] = author.get("avatar_url")
                entry["projects"].add(project.get("ref") or project.get("name"))
                entry["mr_count"] += 1
                # Also index commit authors for avatar matching
                for author_name in (mr.get("commits_by_author") or {}):
                    ckey = normalize_name(author_name)
                    people.setdefault(
                        ckey,
                        {
                            "name": author_name,
                            "avatar_url": None,
                            "projects": set(),
                            "mr_count": 0,
                        },
                    )

    result = []
    for entry in people.values():
        result.append(
            {
                "name": entry["name"],
                "avatar_url": entry.get("avatar_url"),
                "projects": sorted(entry["projects"]),
                "mr_count": entry["mr_count"],
            }
        )
    return result
