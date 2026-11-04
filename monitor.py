#!/usr/bin/env python3
"""Recursively monitor a public HTTP directory tree and record new files."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

USER_AGENT = "3gpp-file-monitor/1.0 (+GitHub Actions; respectful scheduled polling)"
FILE_EXTENSIONS = {
    ".zip", ".doc", ".docx", ".pdf", ".txt", ".xml", ".xlsx", ".xls",
    ".ppt", ".pptx", ".csv", ".json", ".yaml", ".yml"
}


@dataclass(frozen=True)
class Link:
    href: str
    text: str


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[Link] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = dict(attrs)
        self._href = values.get("href")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append(Link(self._href, html.unescape("".join(self._text)).strip()))
            self._href = None
            self._text = []


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def fetch_text(url: str, timeout: int, retries: int, delay: float) -> str:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"})
            with urlopen(req, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                if "html" not in content_type.lower() and "text" not in content_type.lower():
                    raise RuntimeError(f"Expected a directory listing at {url}, got {content_type!r}")
                return response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(delay * (2**attempt))
    raise RuntimeError(f"Unable to fetch {url}: {last_error}")


def looks_like_file(url: str) -> bool:
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    return suffix in FILE_EXTENSIONS


def parse_listing(page_url: str, body: str, root_url: str) -> tuple[set[str], set[str]]:
    parser = LinkParser()
    parser.feed(body)
    root = canonical_url(root_url)
    directories: set[str] = set()
    files: set[str] = set()

    for link in parser.links:
        href = link.href.strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")) or "sortby=" in href:
            continue
        absolute = canonical_url(urljoin(page_url.rstrip("/") + "/", href))
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if absolute == root or not absolute.startswith(root + "/"):
            continue
        if looks_like_file(absolute):
            files.add(absolute)
        else:
            directories.add(absolute)
    return directories, files


def crawl(config: dict) -> dict[str, dict[str, str]]:
    root = canonical_url(config["root_url"])
    timeout = int(config.get("request_timeout_seconds", 30))
    retries = int(config.get("request_retries", 3))
    delay = float(config.get("request_delay_seconds", 0.25))
    max_directories = int(config.get("max_directories", 1000))

    queue: deque[str] = deque([root])
    visited: set[str] = set()
    found: dict[str, dict[str, str]] = {}

    while queue:
        directory = queue.popleft()
        if directory in visited:
            continue
        if len(visited) >= max_directories:
            raise RuntimeError(f"Safety limit exceeded: more than {max_directories} directories")
        body = fetch_text(directory, timeout, retries, delay)
        subdirs, files = parse_listing(directory, body, root)
        visited.add(directory)
        for subdir in sorted(subdirs):
            if subdir not in visited:
                queue.append(subdir)
        for file_url in sorted(files):
            relative = unquote(urlparse(file_url).path[len(urlparse(root).path):].lstrip("/"))
            found[relative] = {"url": file_url}
        if delay:
            time.sleep(delay)

    if not found:
        raise RuntimeError("The crawl returned zero files; refusing to replace the previous snapshot")
    return dict(sorted(found.items()))


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    temp.replace(path)


def write_github_output(values: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def build_report(new_paths: Iterable[str], current: dict[str, dict[str, str]], checked_at: str) -> str:
    paths = list(new_paths)
    lines = [
        "# New files in the 3GPP latest specification tree",
        "",
        f"Detected at: `{checked_at}`",
        f"New files: **{len(paths)}**",
        "",
    ]
    lines.extend(f"- [{path}]({current[path]['url']})" for path in paths)
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--state", default="state.json")
    parser.add_argument("--report", default="new_files.md")
    args = parser.parse_args()

    config_path = Path(args.config)
    state_path = Path(args.state)
    report_path = Path(args.report)
    config = load_json(config_path, {})
    if "root_url" not in config:
        raise RuntimeError(f"Missing root_url in {config_path}")

    previous_state = load_json(state_path, {"initialized": False, "files": {}})
    previous_files = previous_state.get("files", {})
    initialized = bool(previous_state.get("initialized", False))
    current_files = crawl(config)
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new_paths = sorted(set(current_files) - set(previous_files)) if initialized else []

    state = {
        "initialized": True,
        "root_url": canonical_url(config["root_url"]),
        "last_successful_check_utc": checked_at,
        "file_count": len(current_files),
        "files": current_files,
    }
    write_json(state_path, state)

    if new_paths:
        report_path.write_text(build_report(new_paths, current_files, checked_at), encoding="utf-8", newline="\n")
    elif report_path.exists():
        report_path.unlink()

    write_github_output({
        "initialized_before_run": str(initialized).lower(),
        "new_count": str(len(new_paths)),
        "file_count": str(len(current_files)),
        "report_path": str(report_path),
    })
    print(f"Checked {len(current_files)} files; new files: {len(new_paths)}; baseline existed: {initialized}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
