import re
from collections import Counter
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "spotcheck" / "static"
PACKAGE = STATIC.parent
KEY = re.compile(r'^ {4}"([^"]+)":\s*(".*"|`.*`),?\s*$')
PLACEHOLDER = re.compile(r"\{(\w+)(?:\|[^}]*)?\}")
STATIC_KEY = re.compile(r'\b(?:t|te|th)\("([a-z][\w.]*)"')
MARKUP_KEY = re.compile(r'data-i18n(?:-[a-z]+)?="([a-z][\w.]*)"')
BACKEND_CODE = re.compile(r'\blog\(\s*"(?:info|warn|error)",\s*"([a-z_]+)"')
ERROR_CODE = re.compile(r'(?:JobError|HTTPException)\((?:\d+,\s*)?"([a-z_]+)"')


def dictionaries() -> dict[str, list[tuple[str, str]]]:
    lines = (STATIC / "i18n.js").read_text(encoding="utf-8").splitlines()
    found: dict[str, list[tuple[str, str]]] = {}
    language = None
    for line in lines:
        start = re.match(r"^  (\w+): \{$", line)
        if start:
            language = start.group(1)
            found[language] = []
        elif line == "  },":
            language = None
        elif language:
            m = KEY.match(line)
            if m:
                found[language].append((m.group(1), m.group(2)))
    return found


@pytest.fixture(scope="module")
def messages():
    return dictionaries()


def test_both_languages_have_the_same_keys_and_no_duplicates(messages):
    assert set(messages) == {"en", "cs"}
    for language, entries in messages.items():
        duplicates = [key for key, n in Counter(k for k, _ in entries).items() if n > 1]
        assert duplicates == [], language
    assert {k for k, _ in messages["en"]} == {k for k, _ in messages["cs"]}


def test_placeholders_match_between_languages(messages):
    cs = dict(messages["cs"])
    different = [
        key for key, text in messages["en"] if set(PLACEHOLDER.findall(text)) != set(PLACEHOLDER.findall(cs[key]))
    ]
    assert different == []


def test_every_key_used_by_the_page_exists(messages):
    known = {k for k, _ in messages["en"]}
    used = set(STATIC_KEY.findall((STATIC / "app.js").read_text(encoding="utf-8")))
    used |= set(MARKUP_KEY.findall((STATIC / "index.html").read_text(encoding="utf-8")))
    assert sorted(used - known) == []


def test_every_message_code_of_the_backend_can_be_shown(messages):
    known = {k for k, _ in messages["en"]}
    source = "\n".join(p.read_text(encoding="utf-8") for p in PACKAGE.glob("*.py"))
    codes = set(BACKEND_CODE.findall(source))
    missing = [code for code in codes if f"log.{code}" not in known and f"error.{code}" not in known]
    missing += [code for code in set(ERROR_CODE.findall(source)) if f"error.{code}" not in known]
    assert sorted(missing) == []
