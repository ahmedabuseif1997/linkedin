"""Checks for the standalone 971 Labs company site in 971labs/.

The page ships English in the markup and Arabic in a JSON dictionary
(<script id="i18n">). These tests keep the two in step.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

PAGE = Path(__file__).resolve().parent.parent / "971labs" / "index.html"


@pytest.fixture(scope="module")
def soup() -> BeautifulSoup:
    return BeautifulSoup(PAGE.read_text(encoding="utf-8"), "html.parser")


@pytest.fixture(scope="module")
def strings(soup: BeautifulSoup) -> dict[str, dict[str, str]]:
    return json.loads(soup.find("script", id="i18n").string)


def attr_specs(soup: BeautifulSoup) -> list[tuple[object, str, str]]:
    specs = []
    for el in soup.select("[data-i18n-attr]"):
        for pair in el["data-i18n-attr"].split(";"):
            attr, key = (part.strip() for part in pair.split(":", 1))
            specs.append((el, attr, key))
    return specs


def markup_keys(soup: BeautifulSoup) -> set[str]:
    keys = {el["data-i18n"] for el in soup.select("[data-i18n]")}
    return keys | {key for _, _, key in attr_specs(soup)}


def test_every_translatable_key_has_arabic(soup, strings):
    missing = sorted(markup_keys(soup) - strings["ar"].keys())
    assert not missing, f"No Arabic for: {missing}"


def test_dynamic_strings_exist_in_both_languages(strings):
    missing = sorted(strings["en"].keys() - strings["ar"].keys())
    assert not missing, f"Dynamic strings without Arabic: {missing}"


def test_no_unused_arabic_entries(soup, strings):
    unused = sorted(strings["ar"].keys() - markup_keys(soup) - strings["en"].keys())
    assert not unused, f"Arabic entries nothing uses: {unused}"


def test_no_empty_translations(strings):
    empty = [key for lang in ("en", "ar") for key, value in strings[lang].items() if not value.strip()]
    assert not empty, f"Empty strings: {empty}"


def test_translated_elements_hold_plain_text(soup):
    # The script swaps textContent, which would delete any child elements.
    nested = [el["data-i18n"] for el in soup.select("[data-i18n]") if el.find(True)]
    assert not nested, f"data-i18n elements with child elements: {nested}"


def test_translated_attributes_have_english_defaults(soup):
    missing = [f"{key} ({attr})" for el, attr, key in attr_specs(soup) if not el.get(attr, "").strip()]
    assert not missing, f"Attributes with no English value in the markup: {missing}"


def test_ids_are_unique(soup):
    duplicates = [i for i, n in Counter(el["id"] for el in soup.select("[id]")).items() if n > 1]
    assert not duplicates, f"Duplicate ids: {duplicates}"


def test_form_controls_have_labels(soup):
    unlabelled = [
        el["id"]
        for el in soup.select("#brief-form input, #brief-form select, #brief-form textarea")
        if not soup.select_one(f'label[for="{el["id"]}"]')
    ]
    assert not unlabelled, f"Form controls without a label: {unlabelled}"
