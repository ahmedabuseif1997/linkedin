"""Deterministic SEO audit of the built site (dist/).

Checks technical SEO signals (title, description, canonical, Open Graph,
structured data, headings, images, links, robots.txt, sitemap.xml) plus
site-specific integrity: Arabic translation coverage and the facts that must
never change without a human (metrics, contact links).

Usage: python -m site_tools.audit [--dist dist] [--config site.config.json]
                                  [--format md|json] [--fail-on error|warning|never]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Comment

from site_tools.build import NOINDEX_RE, NOT_IN_SITEMAP, ROOT, load_site_url, page_url

SEVERITIES = ("error", "warning", "info")
TITLE_RANGE = (30, 65)
DESCRIPTION_RANGE = (70, 160)
MAX_HTML_BYTES = 150_000
MAX_IMAGE_BYTES = 250_000
MAX_INLINE_IMAGE_CHARS = 5_000
REQUIRED_OG = ("og:title", "og:description", "og:type", "og:url", "og:image")
NON_TRANSLATED_TAGS = {"script", "style", "noscript", "template"}
DICTIONARY_RE = re.compile(r"const\s+arabic\s*=\s*\{(.*?)\n\s*\};", re.DOTALL)
DICTIONARY_PAIR_RE = re.compile(r"'((?:[^'\\]|\\.)*)'\s*:\s*'((?:[^'\\]|\\.)*)'")
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


@dataclass
class Finding:
    severity: str
    check: str
    page: str
    message: str


@dataclass
class AuditResult:
    site_url: str
    findings: list[Finding] = field(default_factory=list)
    untranslated: list[str] = field(default_factory=list)
    facts: dict = field(default_factory=dict)

    def add(self, severity: str, check: str, page: str, message: str) -> None:
        assert severity in SEVERITIES
        self.findings.append(Finding(severity, check, page, message))

    def count(self, severity: str) -> int:
        return sum(1 for f in self.findings if f.severity == severity)

    def counts(self) -> dict[str, int]:
        return {s: self.count(s) for s in SEVERITIES}

    def to_dict(self) -> dict:
        return {
            "site_url": self.site_url,
            "counts": self.counts(),
            "findings": [asdict(f) for f in self.findings],
            "untranslated_text": self.untranslated,
            "facts": self.facts,
        }

    def to_markdown(self) -> str:
        c = self.counts()
        lines = [
            f"**SEO audit for {self.site_url}** — {c['error']} error(s), {c['warning']} warning(s), {c['info']} note(s)",
            "",
        ]
        for severity, heading in zip(SEVERITIES, ("Errors", "Warnings", "Notes")):
            items = [f for f in self.findings if f.severity == severity]
            if not items:
                continue
            lines.append(f"### {heading}")
            lines.extend(f"- `{f.check}` ({f.page}): {f.message}" for f in items)
            lines.append("")
        if not self.findings:
            lines.append("No findings.")
        return "\n".join(lines).rstrip() + "\n"


def parse_translation_dictionary(html: str) -> dict[str, str] | None:
    """Parse the `const arabic = {...}` dictionary used by the language toggle."""
    match = DICTIONARY_RE.search(html)
    if not match:
        return None
    unescape = lambda s: re.sub(r"\\(.)", r"\1", s)  # noqa: E731 - JS single-quoted string escapes
    return {unescape(k): unescape(v) for k, v in DICTIONARY_PAIR_RE.findall(match.group(1))}


def untranslated_text(soup: BeautifulSoup, dictionary: dict[str, str]) -> list[str]:
    """Visible English text nodes the Arabic toggle cannot translate (mirrors the page's TreeWalker)."""
    missing: list[str] = []
    if soup.body is None:
        return missing
    for node in soup.body.find_all(string=True):
        if isinstance(node, Comment):
            continue
        text = node.strip()
        if not text or not re.search(r"[A-Za-z]", text):
            continue
        skip = False
        for parent in node.parents:
            if parent.name in NON_TRANSLATED_TAGS or parent.get("id") == "lang-toggle" or parent.get("translate") == "no":
                skip = True
                break
        if not skip and text not in dictionary:
            missing.append(text)
    return missing


def page_facts(soup: BeautifulSoup) -> dict:
    """Facts an automated edit must never change: metric values, contact links, section anchors."""
    links = sorted(
        {a["href"] for a in soup.find_all("a", href=True) if a["href"].startswith(("mailto:", "tel:", "https://wa.me/"))}
    )
    return {
        "metrics": [el["data-count"] for el in soup.find_all(attrs={"data-count": True})],
        "contact_links": links,
        "section_ids": [s.get("id", "") for s in soup.find_all("section")],
    }


def local_file_for(url: str, page_path: Path, dist: Path, site_url: str) -> Path | None:
    """Map an asset URL (absolute on this site, or relative) to a file in dist, or None if external."""
    if url.startswith("data:"):
        return None
    if url.startswith(site_url):
        return dist / url[len(site_url):].split("#")[0].split("?")[0]
    if urlparse(url).scheme or url.startswith("//"):
        return None
    clean = url.split("#")[0].split("?")[0]
    if not clean:
        return None
    return (dist / clean.lstrip("/")) if clean.startswith("/") else (page_path.parent / clean)


def find_person(data) -> dict | None:
    nodes = data.get("@graph", [data]) if isinstance(data, dict) else data if isinstance(data, list) else []
    for node in nodes:
        if isinstance(node, dict):
            types = node.get("@type")
            if types == "Person" or (isinstance(types, list) and "Person" in types):
                return node
    return None


def audit_page(result: AuditResult, page_path: Path, dist: Path, expected_url: str | None) -> None:
    site_url = result.site_url
    name = page_path.relative_to(dist).as_posix()
    raw = page_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(raw, "html.parser")
    head = soup.head or soup
    indexable = expected_url is not None

    html_tag = soup.find("html")
    if not html_tag or not html_tag.get("lang"):
        result.add("error", "html-lang", name, "<html> has no lang attribute.")
    if not head.find("meta", attrs={"name": "viewport"}):
        result.add("error", "viewport", name, "Missing <meta name=\"viewport\">.")
    if not head.find("meta", charset=True):
        result.add("warning", "charset", name, "Missing <meta charset>.")

    title = head.find("title")
    title_text = title.get_text(strip=True) if title else ""
    if not title_text:
        result.add("error", "title", name, "Missing or empty <title>.")
    elif indexable and not TITLE_RANGE[0] <= len(title_text) <= TITLE_RANGE[1]:
        result.add("warning", "title-length", name,
                   f"Title is {len(title_text)} characters; aim for {TITLE_RANGE[0]}–{TITLE_RANGE[1]}: {title_text!r}")

    robots = head.find("meta", attrs={"name": "robots"})
    robots_content = (robots.get("content") or "").lower() if robots else ""
    if indexable and "noindex" in robots_content:
        result.add("error", "robots-meta", name, "Indexable page carries noindex.")

    if not indexable:
        return  # 404 and noindex pages: only the basics above apply

    desc = head.find("meta", attrs={"name": "description"})
    desc_text = (desc.get("content") or "").strip() if desc else ""
    if not desc_text:
        result.add("error", "meta-description", name, "Missing meta description.")
    elif not DESCRIPTION_RANGE[0] <= len(desc_text) <= DESCRIPTION_RANGE[1]:
        result.add("warning", "meta-description-length", name,
                   f"Meta description is {len(desc_text)} characters; aim for "
                   f"{DESCRIPTION_RANGE[0]}–{DESCRIPTION_RANGE[1]} so it is not truncated in results.")

    canonical = head.find("link", rel="canonical")
    canonical_href = canonical.get("href", "") if canonical else ""
    if not canonical_href:
        result.add("error", "canonical", name, "Missing <link rel=\"canonical\">.")
    elif canonical_href != expected_url:
        result.add("error", "canonical", name, f"Canonical is {canonical_href!r}, expected {expected_url!r}.")

    # hreflang only works when each language lives at its own URL; this page switches language in the browser.
    languages_by_url: dict[str, list[str]] = {}
    for link in head.find_all("link", rel="alternate", hreflang=True):
        if link["hreflang"].lower() != "x-default":
            languages_by_url.setdefault(link.get("href", ""), []).append(link["hreflang"])
    for href, languages in languages_by_url.items():
        if len(languages) > 1:
            result.add("warning", "hreflang", name,
                       f"hreflang {', '.join(languages)} all point to {href!r}; hreflang only helps when each "
                       "language has its own URL. Remove these tags or publish a separate page per language.")
    has_other_language_url = any(href != expected_url for href in languages_by_url)
    if head.find("meta", property="og:locale:alternate") and not has_other_language_url:
        result.add("warning", "og-locale", name,
                   "og:locale:alternate declares another language version, but no other language has its own URL.")

    og = {m.get("property"): (m.get("content") or "") for m in head.find_all("meta", property=True)}
    for prop in REQUIRED_OG:
        if not og.get(prop):
            result.add("warning", "open-graph", name, f"Missing {prop}.")
    if og.get("og:url") and og["og:url"] != expected_url:
        result.add("warning", "open-graph", name, f"og:url {og['og:url']!r} differs from canonical {expected_url!r}.")
    if og.get("og:image"):
        if not og["og:image"].startswith("https://"):
            result.add("error", "open-graph", name, "og:image must be an absolute https URL.")
        else:
            image_file = local_file_for(og["og:image"], page_path, dist, site_url)
            if image_file is not None and not image_file.is_file():
                result.add("error", "open-graph", name, f"og:image file not found in build: {og['og:image']}")
    if og.get("og:title") and title_text and og["og:title"] != title_text:
        result.add("info", "open-graph", name, "og:title differs from <title> (fine if intentional).")
    if og.get("og:description") and desc_text and og["og:description"] != desc_text:
        result.add("info", "open-graph", name, "og:description differs from the meta description (fine if intentional).")
    if not head.find("meta", attrs={"name": "twitter:card"}):
        result.add("warning", "twitter-card", name, "Missing <meta name=\"twitter:card\">.")
    if not head.find("link", rel=lambda v: v and "icon" in v):
        result.add("warning", "favicon", name, "No <link rel=\"icon\">.")

    ld_scripts = soup.find_all("script", type="application/ld+json")
    person = None
    for index, script in enumerate(ld_scripts):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError as error:
            result.add("error", "structured-data", name, f"JSON-LD block {index + 1} is invalid JSON: {error}")
            continue
        person = person or find_person(data)
    if not ld_scripts:
        result.add("warning", "structured-data", name, "No JSON-LD structured data.")
    elif person is None:
        result.add("warning", "structured-data", name, "No schema.org Person in the structured data.")
    else:
        for key in ("name", "url", "image", "jobTitle"):
            if not person.get(key):
                result.add("warning", "structured-data", name, f"Person is missing {key!r}.")
        if person.get("url") and person["url"] != site_url:
            result.add("warning", "structured-data", name, f"Person url {person['url']!r} differs from {site_url!r}.")
        image = person.get("image")
        if isinstance(image, str):
            image_file = local_file_for(image, page_path, dist, site_url)
            if image_file is not None and not image_file.is_file():
                result.add("error", "structured-data", name, f"Person image not found in build: {image}")
        if not person.get("sameAs"):
            result.add("info", "structured-data", name,
                       "Person has no sameAs profile links (e.g. LinkedIn). Needs the real profile URLs from the owner.")

    h1s = soup.find_all("h1")
    if len(h1s) != 1:
        result.add("warning", "h1", name, f"Expected exactly one <h1>, found {len(h1s)}.")
    previous = 0
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        level = int(heading.name[1])
        if previous and level > previous + 1:
            result.add("info", "heading-order", name,
                       f"<{heading.name}> follows <h{previous}> (skipped level): {heading.get_text(' ', strip=True)[:60]!r}")
        previous = level

    for img in soup.find_all("img"):
        src = img.get("src", "")
        label = src[:40] + ("…" if len(src) > 40 else "")
        if img.get("alt") is None:
            result.add("error", "img-alt", name, f"Image without alt attribute: {label}")
        if not (img.get("width") and img.get("height")):
            result.add("warning", "img-dimensions", name, f"Image without width/height (layout shift): {label}")
        if src.startswith("data:") and len(src) > MAX_INLINE_IMAGE_CHARS:
            result.add("warning", "img-inline", name,
                       f"Large inline data: image ({len(src)} chars); serve it as a file so it can be cached and indexed.")
        image_file = local_file_for(src, page_path, dist, site_url)
        if image_file is not None:
            if not image_file.is_file():
                result.add("error", "img-missing", name, f"Image file not found: {src}")
            elif image_file.stat().st_size > MAX_IMAGE_BYTES:
                result.add("warning", "img-weight", name,
                           f"{src} is {image_file.stat().st_size // 1000} KB; compress below {MAX_IMAGE_BYTES // 1000} KB.")

    ids = {el["id"] for el in soup.find_all(id=True)}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("#") and len(href) > 1 and href[1:] not in ids:
            result.add("warning", "broken-anchor", name, f"Link to {href} has no matching id.")
        if a.get("target") == "_blank":
            rel = a.get("rel") or []
            if not {"noopener", "noreferrer"} & set(rel):
                result.add("warning", "link-rel", name, f"target=_blank link without rel=noopener: {href}")
        target = local_file_for(href, page_path, dist, site_url)
        if target is not None and href.split("#")[0] and not href.startswith(("mailto:", "tel:")):
            if not (target.is_file() or (target / "index.html").is_file()):
                result.add("error", "broken-link", name, f"Internal link target not found: {href}")

    size = len(raw.encode("utf-8"))
    if size > MAX_HTML_BYTES:
        result.add("warning", "html-weight", name, f"HTML is {size // 1000} KB; keep it under {MAX_HTML_BYTES // 1000} KB.")

    dictionary = parse_translation_dictionary(raw)
    if dictionary is not None:
        missing = untranslated_text(soup, dictionary)
        result.untranslated.extend(f"{name}: {text}" for text in missing)
        for text in missing:
            result.add("warning", "arabic-translation", name,
                       f"Visible text has no Arabic entry in the `arabic` dictionary: {text[:80]!r}")
    if name == "index.html":
        result.facts = page_facts(soup)


def audit_site_files(result: AuditResult, dist: Path, page_urls: list[str]) -> None:
    site_url = result.site_url
    robots = dist / "robots.txt"
    if not robots.is_file():
        result.add("error", "robots-txt", "robots.txt", "robots.txt is missing.")
    else:
        text = robots.read_text(encoding="utf-8")
        if re.search(r"^\s*Disallow:\s*/\s*$", text, re.MULTILINE):
            result.add("error", "robots-txt", "robots.txt", "robots.txt disallows the whole site.")
        if f"Sitemap: {site_url}sitemap.xml" not in text:
            result.add("warning", "robots-txt", "robots.txt", "robots.txt does not reference the absolute sitemap URL.")

    sitemap = dist / "sitemap.xml"
    if not sitemap.is_file():
        result.add("error", "sitemap", "sitemap.xml", "sitemap.xml is missing.")
    else:
        try:
            locs = [el.text or "" for el in ET.parse(sitemap).getroot().iter(f"{SITEMAP_NS}loc")]
        except ET.ParseError as error:
            result.add("error", "sitemap", "sitemap.xml", f"sitemap.xml is not valid XML: {error}")
            locs = []
        for url in page_urls:
            if url not in locs:
                result.add("error", "sitemap", "sitemap.xml", f"Indexable page missing from sitemap: {url}")
        for loc in locs:
            if not loc.startswith(site_url):
                result.add("error", "sitemap", "sitemap.xml", f"Sitemap URL outside the site: {loc}")

    for path in dist.rglob("*"):
        if path.is_file() and path.suffix in {".html", ".xml", ".txt", ".json", ".svg"}:
            if "{{SITE_URL}}" in path.read_text(encoding="utf-8", errors="replace"):
                result.add("error", "template-token", path.relative_to(dist).as_posix(), "Unresolved {{SITE_URL}} token.")
    if not (dist / "404.html").is_file():
        result.add("info", "404-page", "404.html", "No custom 404 page.")


def audit(dist: Path, site_url: str) -> AuditResult:
    result = AuditResult(site_url=site_url)
    if not (dist / "index.html").is_file():
        result.add("error", "build", "index.html", f"{dist}/index.html not found — run the build first.")
        return result
    page_urls: list[str] = []
    for page in sorted(dist.rglob("*.html")):
        relative = PurePosixPath(page.relative_to(dist).as_posix())
        text = page.read_text(encoding="utf-8")
        # The home page must always be indexable, so a stray noindex there is reported instead of skipped.
        is_home = relative == PurePosixPath("index.html")
        indexable = is_home or (relative.name not in NOT_IN_SITEMAP and not NOINDEX_RE.search(text))
        expected = page_url(site_url, relative) if indexable else None
        if expected:
            page_urls.append(expected)
        audit_page(result, page, dist, expected)
    audit_site_files(result, dist, page_urls)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--config", type=Path, default=ROOT / "site.config.json")
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--fail-on", choices=("error", "warning", "never"), default="error")
    args = parser.parse_args(argv)

    result = audit(args.dist, load_site_url(args.config))
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) if args.format == "json" else result.to_markdown())
    if args.fail_on == "error" and result.count("error"):
        return 1
    if args.fail_on == "warning" and (result.count("error") or result.count("warning")):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
