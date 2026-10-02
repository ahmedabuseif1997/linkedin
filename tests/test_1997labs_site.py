"""Checks for the 1997 Labs company site in 1997labs/site/."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

SITE = Path(__file__).resolve().parent.parent / "1997labs" / "site"
DOMAIN = "https://1997labs.com/"
PAGES = ["index.html", "privacy.html", "404.html"]
EXTERNAL = ("http://", "https://", "//", "data:", "mailto:", "tel:", "#")
DESCRIPTOR = re.compile(r"ai\s*&\s*software company", re.IGNORECASE)


def load(name: str) -> BeautifulSoup:
    return BeautifulSoup((SITE / name).read_text(encoding="utf-8"), "html.parser")


@pytest.fixture(scope="module")
def home() -> BeautifulSoup:
    return load("index.html")


@pytest.mark.parametrize("page", PAGES)
def test_local_files_exist(page):
    soup = load(page)
    refs = [el.get("src") or el.get("href") for el in soup.select("[src], link[href], a[href]")]
    missing = []
    for ref in refs:
        if not ref or ref.startswith(EXTERNAL) or ref in ("/", "./"):
            continue
        path = SITE / ref.lstrip("/").split("#")[0]
        if not path.is_file():
            missing.append(ref)
    assert not missing, f"{page} references missing files: {missing}"


def test_in_page_links_have_targets(home):
    ids = {el["id"] for el in home.select("[id]")}
    broken = [a["href"] for a in home.select('a[href^="#"]') if a["href"][1:] not in ids]
    assert not broken, f"Links to missing sections: {broken}"


def test_ids_are_unique(home):
    duplicates = [i for i, n in Counter(el["id"] for el in home.select("[id]")).items() if n > 1]
    assert not duplicates, f"Duplicate ids: {duplicates}"


def test_images_have_alt_attribute(home):
    assert all(img.get("alt") is not None for img in home.find_all("img"))


def test_tech_company_is_stated_up_front(home):
    """Visitors and search engines should see 'AI & Software Company' immediately."""
    assert DESCRIPTOR.search(home.title.string)
    assert DESCRIPTOR.search(home.find("meta", attrs={"name": "description"})["content"])
    assert DESCRIPTOR.search(home.select_one(".brand small").get_text())
    assert "AI agents" in home.select_one(".hero .hero-copy").get_text()
    labels = [li.get_text(strip=True) for li in home.select(".hero .tech-row li")]
    assert labels == ["AI agents", "Websites", "Mobile apps", "CRM systems", "Cloud & APIs"]


def test_copy_lives_in_the_html_not_in_a_rewrite_script(home):
    scripts = " ".join(s.get_text() for s in home.find_all("script"))
    assert ".hero h1" not in scripts, "A script rewrites the hero headline again"
    assert home.select_one(".hero h1").get_text(" ", strip=True) == "More customers. Less work. More revenue."
    for removed in ("profile", "process"):
        assert not home.select(f"#{removed}"), f"Removed section #{removed} is back"


def test_old_red_accent_is_gone():
    html = (SITE / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"(?i)ff3b30|255,\s*59,\s*48", html)


def test_example_charts_are_labelled(home):
    assert "not client results" in home.select_one(".charts-head .example-badge").get_text()


def test_consent_banner_waits_for_an_analytics_id():
    html = (SITE / "index.html").read_text(encoding="utf-8")
    assert "analyticsConfig.ga4||analyticsConfig.clarity" in html


def test_privacy_notice_is_linked(home):
    assert home.select_one('footer a[href="privacy.html"]')
    assert home.select_one('.form-note a[href="privacy.html"]')


def test_search_and_social_metadata_agree(home):
    assert home.find("link", rel="canonical")["href"] == DOMAIN
    assert home.find("meta", property="og:url")["content"] == DOMAIN
    og_image = home.find("meta", property="og:image")["content"]
    assert og_image.startswith(DOMAIN) and (SITE / og_image[len(DOMAIN):]).is_file()
    data = json.loads(home.find("script", type="application/ld+json").string)
    assert data["url"] == DOMAIN and data["email"] == "info@1997labs.com"
    assert f"Sitemap: {DOMAIN}sitemap.xml" in (SITE / "robots.txt").read_text()
    assert f"<loc>{DOMAIN}</loc>" in (SITE / "sitemap.xml").read_text()


def test_assets_stay_light():
    sizes = {p.name: p.stat().st_size for p in (SITE / "assets").iterdir()}
    heavy = {name: size for name, size in sizes.items() if size > 400_000}
    assert not heavy, f"Assets over 400 KB: {heavy}"
    total = sum(p.stat().st_size for p in SITE.rglob("*") if p.is_file())
    assert total < 1_000_000, f"Site is {total} bytes; keep it under 1 MB"
