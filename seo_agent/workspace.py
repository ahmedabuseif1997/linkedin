"""Tools the SEO agent can call. All file access is confined to the site/ directory."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from site_tools.audit import AuditResult, audit
from site_tools.build import GENERATED_FILES, build

READABLE_SUFFIXES = {".html", ".txt", ".xml", ".json", ".svg", ".webmanifest", ".css", ".js"}
CREATABLE_SUFFIXES = {".html", ".txt", ".xml", ".json", ".svg", ".webmanifest"}
MAX_CREATE_BYTES = 200_000
MAX_READ_CHARS = 150_000
FETCH_TIMEOUT_SECONDS = 20
FETCH_TEXT_PREVIEW = 3_000


class ToolError(Exception):
    """A problem the model can fix; returned to it as an is_error tool result."""


class _RecordingRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str]):
        self.allowed_hosts = allowed_hosts
        self.chain: list[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append(f"{code} -> {newurl}")
        if urlparse(newurl).hostname not in self.allowed_hosts:
            return None  # stop here; the 3xx is reported instead of followed
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class SiteWorkspace:
    def __init__(self, root: Path, site_url: str, extra_hosts: set[str] | None = None):
        self.root = root
        self.site_dir = (root / "site").resolve()
        self.site_url = site_url
        host = urlparse(site_url).hostname or ""
        bare = host[4:] if host.startswith("www.") else host
        self.allowed_hosts = {bare, f"www.{bare}"} | (extra_hosts or set())

    # ----- file access -------------------------------------------------------------------------

    def _resolve(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative.strip():
            raise ToolError("path must be a non-empty string relative to site/, e.g. 'index.html'")
        posix = PurePosixPath(relative.strip())
        if posix.is_absolute() or ".." in posix.parts or "\\" in relative:
            raise ToolError(f"path {relative!r} must be relative to site/ and must not contain '..'")
        path = self.site_dir.joinpath(*posix.parts)
        if path.is_symlink() or not path.resolve().is_relative_to(self.site_dir):
            raise ToolError(f"path {relative!r} is outside site/")
        return path

    def list_files(self) -> str:
        lines = [
            f"{p.relative_to(self.site_dir).as_posix()}  ({p.stat().st_size} bytes)"
            for p in sorted(self.site_dir.rglob("*"))
            if p.is_file()
        ]
        return "\n".join(lines)

    def read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        file = self._resolve(path)
        if not file.is_file():
            raise ToolError(f"{path} does not exist")
        if file.suffix not in READABLE_SUFFIXES:
            raise ToolError(f"{path} is a binary file; only text files can be read")
        lines = file.read_text(encoding="utf-8").splitlines()
        start = max(1, start_line or 1)
        end = min(len(lines), end_line or len(lines))
        if start > end:
            raise ToolError(f"line range {start}-{end} is empty; the file has {len(lines)} lines")
        body = "\n".join(f"{n:>5}\t{lines[n - 1]}" for n in range(start, end + 1))
        if len(body) > MAX_READ_CHARS:
            raise ToolError(f"{path} lines {start}-{end} exceed {MAX_READ_CHARS} characters; read a smaller range")
        return f"{path} (lines {start}-{end} of {len(lines)}):\n{body}"

    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        file = self._resolve(path)
        if not file.is_file() or file.suffix not in READABLE_SUFFIXES:
            raise ToolError(f"{path} is not an editable text file")
        if old_string == new_string:
            raise ToolError("old_string and new_string are identical")
        text = file.read_text(encoding="utf-8")
        count = text.count(old_string) if old_string else 0
        if count == 0:
            raise ToolError(f"old_string was not found in {path}; copy it exactly (without line-number prefixes)")
        if count > 1:
            raise ToolError(f"old_string occurs {count} times in {path}; include more surrounding text to make it unique")
        file.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
        return f"Edited {path}: replaced {len(old_string)} characters with {len(new_string)}."

    def create_file(self, path: str, content: str) -> str:
        file = self._resolve(path)
        if file.exists():
            raise ToolError(f"{path} already exists; use edit_site_file to change it")
        if file.suffix not in CREATABLE_SUFFIXES:
            raise ToolError(f"only {sorted(CREATABLE_SUFFIXES)} files can be created")
        if file.name in GENERATED_FILES:
            raise ToolError(f"{file.name} is generated by the build from site.config.json; do not create it")
        if len(content.encode("utf-8")) > MAX_CREATE_BYTES:
            raise ToolError(f"content exceeds {MAX_CREATE_BYTES} bytes")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
        return f"Created {path} ({len(content)} characters)."

    def snapshot(self) -> dict[str, str]:
        """sha256 of every file under site/, keyed by relative path."""
        return {
            p.relative_to(self.site_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(self.site_dir.rglob("*"))
            if p.is_file()
        }

    # ----- build + audit ----------------------------------------------------------------------

    def build_and_audit(self) -> AuditResult:
        """Build site/ into a temporary directory and audit it. Raises BuildError on build failure."""
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            build(self.site_dir, dist, self.site_url)
            return audit(dist, self.site_url)

    # ----- live site --------------------------------------------------------------------------

    def fetch_live(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or parsed.hostname not in self.allowed_hosts:
            raise ToolError(f"only http(s) URLs on {sorted(self.allowed_hosts)} can be fetched")
        redirects = _RecordingRedirects(self.allowed_hosts)
        opener = urllib.request.build_opener(redirects)
        request = urllib.request.Request(url, headers={"User-Agent": f"seo-agent (+{self.site_url})"})
        lines = [f"GET {url}"]
        try:
            with opener.open(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
                status, final_url, headers = response.status, response.geturl(), response.headers
                body = response.read(2_000_000).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            lines += [f"redirect: {hop}" for hop in redirects.chain]
            lines.append(f"HTTP {error.code} {error.reason}")
            if 300 <= error.code < 400:
                lines.append(f"Location: {error.headers.get('Location')} (not followed: outside {sorted(self.allowed_hosts)})")
            return "\n".join(lines)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return "\n".join(lines + [f"Request failed: {error}"])

        lines += [f"redirect: {hop}" for hop in redirects.chain]
        lines += [f"HTTP {status}", f"final URL: {final_url}"]
        for name in ("content-type", "cache-control", "x-robots-tag", "last-modified", "server"):
            if headers.get(name):
                lines.append(f"{name}: {headers.get(name)}")
        lines.append(f"body: {len(body)} characters")
        if "html" in (headers.get("content-type") or ""):
            soup = BeautifulSoup(body, "html.parser")
            canonical = soup.find("link", rel="canonical")
            description = soup.find("meta", attrs={"name": "description"})
            robots = soup.find("meta", attrs={"name": "robots"})
            h1 = soup.find("h1")
            lines += [
                f"title: {soup.title.get_text(strip=True) if soup.title else None!r}",
                f"meta description: {description.get('content') if description else None!r}",
                f"canonical: {canonical.get('href') if canonical else None!r}",
                f"meta robots: {robots.get('content') if robots else None!r}",
                f"h1: {h1.get_text(' ', strip=True) if h1 else None!r}",
            ]
        else:
            lines.append(body[:FETCH_TEXT_PREVIEW])
        return "\n".join(lines)


def extra_hosts_from_env() -> set[str]:
    """Hosts besides the site's own that fetch_live_url may reach (e.g. the github.io address)."""
    hosts = {h.strip().lower() for h in os.environ.get("SEO_AGENT_EXTRA_HOSTS", "").split(",") if h.strip()}
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "").strip().lower()
    if re.fullmatch(r"[a-z0-9-]+", owner):
        hosts.add(f"{owner}.github.io")
    return hosts
