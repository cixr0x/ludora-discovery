from __future__ import annotations

import os
import re
from pathlib import Path

from ludora.webfetch import FetchResult


class BrowserFetchUnavailable(RuntimeError):
    pass


def fetch_sitemap_text_with_browser(url: str, timeout_ms: int = 30_000) -> FetchResult | None:
    return fetch_text_with_browser(url, timeout_ms=timeout_ms)


class BrowserTextFetcher:
    def __init__(self, timeout_ms: int = 30_000) -> None:
        self.timeout_ms = timeout_ms
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._playwright_error = Exception
        self._playwright_timeout_error = Exception

    def __enter__(self) -> BrowserTextFetcher:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on local environment.
            raise BrowserFetchUnavailable("Playwright is not installed. Install discovery dependencies first.") from exc

        self._playwright_error = PlaywrightError
        self._playwright_timeout_error = PlaywrightTimeoutError
        self._playwright = sync_playwright().start()
        chrome_path = _chrome_executable_path()
        self._browser = self._playwright.chromium.launch(
            executable_path=chrome_path,
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        self._context = self._browser.new_context(
            locale="es-MX",
            user_agent=_browser_user_agent(chrome_path),
            viewport={"width": 1365, "height": 900},
            extra_http_headers={"Accept-Language": "es-MX,es;q=0.9,en;q=0.8"},
        )
        self._context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        self._page = self._context.new_page()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def fetch(self, url: str) -> FetchResult | None:
        if self._page is None:
            raise BrowserFetchUnavailable("Browser fetcher has not been started.")

        try:
            response = _navigate_past_reload_challenge(self._page, url, timeout_ms=self.timeout_ms)
            if response is None:
                return FetchResult(url=self._page.url, text=self._page.content())
            return FetchResult(url=response.url, text=response.text())
        except (self._playwright_error, self._playwright_timeout_error, OSError, ValueError):
            return None


def fetch_text_with_browser(url: str, timeout_ms: int = 30_000) -> FetchResult | None:
    with BrowserTextFetcher(timeout_ms=timeout_ms) as fetcher:
        return fetcher.fetch(url)


def _navigate_past_reload_challenge(page, url: str, *, timeout_ms: int):
    response = None
    for attempt in range(3):
        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        text = response.text() if response is not None else page.content()
        if not _looks_like_reload_challenge(text):
            return response
        if attempt < 2:
            page.wait_for_timeout(6_000)
    return response


def _looks_like_reload_challenge(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    has_reload_loop = "window.location.reload" in normalized or "location.reload" in normalized
    has_challenge_title = (
        "<title>one moment" in normalized
        or "<title>un momento" in normalized
        or "<title>just a moment" in normalized
    )
    return has_reload_loop and has_challenge_title


def _chrome_executable_path() -> str | None:
    configured_path = os.environ.get("LUDORA_BROWSER_EXECUTABLE_PATH", "").strip()
    if configured_path:
        return configured_path

    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _browser_user_agent(chrome_path: str | None) -> str:
    configured_user_agent = os.environ.get("LUDORA_BROWSER_USER_AGENT", "").strip()
    if configured_user_agent:
        return configured_user_agent

    version = _chrome_version_from_installation(chrome_path) or "125.0.0.0"
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version} Safari/537.36"
    )


def _chrome_version_from_installation(chrome_path: str | None) -> str | None:
    if not chrome_path:
        return None

    chrome_directory = Path(chrome_path).parent
    versions: list[tuple[tuple[int, ...], str]] = []
    for child in chrome_directory.iterdir():
        if not child.is_dir() or not re.fullmatch(r"\d+(?:\.\d+){1,3}", child.name):
            continue
        versions.append((tuple(int(part) for part in child.name.split(".")), child.name))
    if not versions:
        return None
    return max(versions, key=lambda item: item[0])[1]
