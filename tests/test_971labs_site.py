"""Checks for the standalone 971 Labs company site in 971labs/."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

SITE = Path(__file__).resolve().parent.parent / "971labs"
EXTERNAL_PREFIXES = ("http://", "https://", "//", "data:", "mailto:", "tel:", "#")


@pytest.fixture(scope="module")
def soup() -> BeautifulSoup:
    return BeautifulSoup((SITE / "index.html").read_text(encoding="utf-8"), "html.parser")


def test_local_files_exist(soup):
    refs = [el.get("src") or el.get("href") for el in soup.select("[src], link[href], a[href]")]
    local = [ref for ref in refs if ref and not ref.startswith(EXTERNAL_PREFIXES)]
    missing = [ref for ref in local if not (SITE / ref).is_file()]
    assert not missing, f"Referenced files that do not exist: {missing}"


def test_in_page_links_have_targets(soup):
    ids = {el["id"] for el in soup.select("[id]")}
    broken = [a["href"] for a in soup.select('a[href^="#"]') if a["href"][1:] not in ids]
    assert not broken, f"Links to missing ids: {broken}"


def test_ids_are_unique(soup):
    duplicates = [i for i, n in Counter(el["id"] for el in soup.select("[id]")).items() if n > 1]
    assert not duplicates, f"Duplicate ids: {duplicates}"


def test_images_have_alt_text_attribute(soup):
    missing = [img.get("src") for img in soup.find_all("img") if img.get("alt") is None]
    assert not missing, f"Images without an alt attribute: {missing}"


def test_page_has_title_and_description(soup):
    assert soup.title and soup.title.string.strip()
    description = soup.find("meta", attrs={"name": "description"})
    assert description and description.get("content", "").strip()
