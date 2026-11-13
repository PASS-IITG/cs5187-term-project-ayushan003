"""A read-only GitHub API client.

CLAUDE.md hard rules this file exists to enforce:

* **hard rule 1** - nothing may be filed, edited, commented on or closed
  anywhere. This client has no code path that can write: the only HTTP call in
  the module is ``requests.get``, and :meth:`GitHubClient.request` refuses any
  method other than GET before it gets near the network.
* **hard rule 6** - the token is never printed, echoed or saved. It is read
  from ``GITHUB_TOKEN`` into one attribute, used only to build an
  ``Authorization`` header, and never logged, never written to the cache, and
  never included in an exception message. :func:`redact` scrubs it from any
  string on its way out.

Behaviour:

* ETag caching under ``workdir/cache/`` - every response is stored with its
  ETag, and the next request sends ``If-None-Match``. A 304 costs no rate-limit
  quota and returns the cached body.
* pagination through the ``Link`` header, never by guessing page numbers.
* rate limiting - ``X-RateLimit-Remaining`` is read from every response and,
  when it reaches zero, the client sleeps until ``X-RateLimit-Reset`` before
  the next call. 403/429 with ``Retry-After`` is honoured the same way.
* a request budget - the client raises :class:`RequestBudgetExceeded` rather
  than making an unbounded number of calls.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

import requests

from graft.config import REPO_ROOT
from graft.log import JsonlLogger

API_ROOT = "https://api.github.com"
#: the only HTTP method this client can perform
ALLOWED_METHODS = frozenset({"GET"})
#: GitHub's documented page size cap
MAX_PER_PAGE = 100
#: default ceiling on API calls for one run, so a bug cannot burn the quota
DEFAULT_REQUEST_BUDGET = 4000
#: how long to wait when GitHub gives no reset hint
FALLBACK_SLEEP_S = 60.0
#: Accept header: the timeline API needs the versioned media type
ACCEPT = "application/vnd.github+json"
API_VERSION = "2022-11-28"

_LINK_RE = re.compile(r'<(?P<url>[^>]+)>\s*;\s*rel="(?P<rel>[^"]+)"')


class GitHubError(RuntimeError):
    """Any failure talking to the API."""


class MethodNotAllowed(GitHubError):
    """Raised for every HTTP method except GET (CLAUDE.md hard rule 1)."""


class RequestBudgetExceeded(GitHubError):
    """Raised when a run tries to exceed its configured request budget."""


def redact(text: str, token: str | None) -> str:
    """Return ``text`` with ``token`` replaced by a placeholder.

    Applied to everything this module reports, so a token cannot leak through
    an exception message or a log line even if a URL ever carried one.
    """
    if not token or not text:
        return text
    return text.replace(token, "***REDACTED***")


@dataclass(frozen=True)
class RateLimit:
    """The rate-limit headers from one response."""

    limit: int | None = None
    remaining: int | None = None
    reset_at: int | None = None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "RateLimit":
        """Read the ``X-RateLimit-*`` headers, tolerating missing ones."""

        def number(name: str) -> int | None:
            raw = headers.get(name)
            try:
                return int(raw) if raw is not None else None
            except ValueError:
                return None

        return cls(
            limit=number("X-RateLimit-Limit"),
            remaining=number("X-RateLimit-Remaining"),
            reset_at=number("X-RateLimit-Reset"),
        )

    @property
    def exhausted(self) -> bool:
        """True when the quota is used up."""
        return self.remaining is not None and self.remaining <= 0


@dataclass
class Response:
    """One API response, whether it came from the network or the cache."""

    status: int
    body: Any
    headers: dict[str, str] = field(default_factory=dict)
    from_cache: bool = False
    rate_limit: RateLimit = field(default_factory=RateLimit)

    @property
    def links(self) -> dict[str, str]:
        """The parsed ``Link`` header, as ``rel`` to url."""
        return parse_links(self.headers.get("Link", ""))


def parse_links(header: str) -> dict[str, str]:
    """Parse a GitHub ``Link`` header into ``{rel: url}``."""
    return {m.group("rel"): m.group("url") for m in _LINK_RE.finditer(header or "")}


def cache_dir() -> Path:
    """Return the ETag cache directory (``workdir/cache``, git-ignored)."""
    override = os.environ.get("GRAFT_CACHE_DIR")
    return Path(override).expanduser() if override else REPO_ROOT / "workdir" / "cache"


def cache_key(url: str, params: Mapping[str, Any] | None) -> str:
    """Return a stable file name for one request.

    The key is a hash of the url and its sorted parameters. Tokens are never
    part of a url here, so nothing secret reaches the file name or the file.
    """
    items = sorted((str(k), str(v)) for k, v in (params or {}).items())
    payload = json.dumps([url, items], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class GitHubClient:
    """A GET-only GitHub API client with ETag caching and rate-limit waits."""

    def __init__(
        self,
        token: str | None = None,
        cache_path: str | os.PathLike[str] | None = None,
        api_root: str = API_ROOT,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        session: Any | None = None,
        request_budget: int = DEFAULT_REQUEST_BUDGET,
        logger: JsonlLogger | None = None,
        use_cache: bool = True,
    ) -> None:
        """Build the client.

        Args:
            token: the API token; defaults to ``os.environ["GITHUB_TOKEN"]``.
                An empty token means unauthenticated (60 requests/hour).
            cache_path: ETag cache directory; defaults to :func:`cache_dir`.
            api_root: API base, overridden in tests.
            sleep: sleep function, injected so tests never really wait.
            now: wall-clock source, injected for the same reason.
            session: object with a ``get`` method; defaults to a
                ``requests.Session``. Only ``get`` is ever called on it.
            request_budget: maximum network calls for this client's lifetime.
            logger: where request metadata goes; token values never appear.
            use_cache: set False to bypass the ETag cache entirely.
        """
        self._token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        self.api_root = api_root.rstrip("/")
        self._sleep = sleep
        self._now = now
        self._session = session if session is not None else requests.Session()
        self.request_budget = request_budget
        self.use_cache = use_cache
        self.cache_path = Path(cache_path) if cache_path is not None else cache_dir()
        if self.use_cache:
            self.cache_path.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        #: network calls made (a cache hit still counts, a 304 is cheap)
        self.requests_made = 0
        #: responses served from the ETag cache as a 304
        self.cache_hits = 0
        #: the rate limit reported by the most recent response
        self.rate_limit = RateLimit()

    # --- token handling ------------------------------------------------------

    @property
    def authenticated(self) -> bool:
        """True if a token was supplied. The value itself is never exposed."""
        return bool(self._token)

    def _headers(self, etag: str | None) -> dict[str, str]:
        headers = {
            "Accept": ACCEPT,
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "graft-corpus",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if etag:
            headers["If-None-Match"] = etag
        return headers

    def _scrub(self, text: str) -> str:
        return redact(text, self._token)

    # --- the only public entry points ----------------------------------------

    def request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
    ) -> Response:
        """Perform ``method`` on ``path``; anything but GET is refused.

        Raises:
            MethodNotAllowed: for any method other than GET. There is no code
                below this point that could write to GitHub.
        """
        if method.upper() not in ALLOWED_METHODS:
            raise MethodNotAllowed(
                f"{method.upper()} is not allowed: this client is read-only "
                "(CLAUDE.md hard rule 1)"
            )
        return self.get(path, params=params)

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Response:
        """GET one url or API path and return a :class:`Response`."""
        url = path if path.startswith("http") else f"{self.api_root}/{path.lstrip('/')}"
        cached = self._read_cache(url, params)
        etag = cached.get("etag") if cached else None

        self._wait_for_quota()
        if self.requests_made >= self.request_budget:
            raise RequestBudgetExceeded(
                f"request budget of {self.request_budget} reached"
            )

        try:
            raw = self._session.get(
                url, headers=self._headers(etag), params=dict(params or {}), timeout=30
            )
        except Exception as exc:  # network errors must not leak a token
            raise GitHubError(self._scrub(f"GET {url} failed: {exc}")) from None
        self.requests_made += 1

        headers = dict(raw.headers)
        self.rate_limit = RateLimit.from_headers(headers)

        if raw.status_code == 304:
            if cached is None:
                # We sent no ETag, so a 304 means we have nothing to serve.
                raise GitHubError(
                    self._scrub(f"GET {url} returned 304 with no cached body")
                )
            self.cache_hits += 1
            self._log(url, 304, True)
            return Response(
                status=200,
                body=cached["body"],
                headers=cached.get("headers", {}) | headers,
                from_cache=True,
                rate_limit=self.rate_limit,
            )

        if raw.status_code in (403, 429):
            wait = self._retry_after(headers)
            if wait is not None:
                self._log(url, raw.status_code, False, waited=wait)
                self._sleep(wait)
                return self.get(path, params=params)

        if raw.status_code >= 400:
            self._log(url, raw.status_code, False)
            raise GitHubError(
                self._scrub(f"GET {url} returned {raw.status_code}: {raw.text[:300]}")
            )

        try:
            body = raw.json()
        except ValueError:
            body = None

        self._log(url, raw.status_code, False)
        self._write_cache(url, params, headers.get("ETag"), body, headers)
        return Response(
            status=raw.status_code,
            body=body,
            headers=headers,
            from_cache=False,
            rate_limit=self.rate_limit,
        )

    def paginate(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        max_items: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield items across pages, following the ``Link`` header.

        Args:
            path: API path.
            params: query parameters; ``per_page`` defaults to the maximum.
            max_items: stop after this many items.

        Yields:
            One item per list element, in API order.
        """
        query = dict(params or {})
        query.setdefault("per_page", MAX_PER_PAGE)
        url: str | None = path
        first = True
        yielded = 0

        while url:
            response = self.get(url, params=query if first else None)
            first = False
            items = response.body
            if isinstance(items, dict) and "items" in items:
                items = items["items"]  # search-style payload
            if not isinstance(items, list):
                raise GitHubError(
                    f"expected a list from {path}, got {type(items).__name__}"
                )
            for item in items:
                yield item
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return
            url = response.links.get("next")

    # --- rate limiting -------------------------------------------------------

    def _retry_after(self, headers: Mapping[str, str]) -> float | None:
        """Seconds to wait for a 403/429, or None if it is not a rate limit."""
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                return FALLBACK_SLEEP_S
        limit = RateLimit.from_headers(headers)
        if limit.exhausted:
            return self._seconds_until(limit.reset_at)
        return None

    def _seconds_until(self, reset_at: int | None) -> float:
        if reset_at is None:
            return FALLBACK_SLEEP_S
        return max(0.0, reset_at - self._now() + 1.0)

    def _wait_for_quota(self) -> None:
        """Sleep until the reset time if the last response said remaining == 0."""
        if not self.rate_limit.exhausted:
            return
        wait = self._seconds_until(self.rate_limit.reset_at)
        self._log("(rate-limit)", 0, False, waited=wait)
        self._sleep(wait)
        # Assume the window reopened; the next response refreshes the real value.
        self.rate_limit = RateLimit(
            limit=self.rate_limit.limit, remaining=None, reset_at=None
        )

    # --- ETag cache ----------------------------------------------------------

    def _cache_file(self, url: str, params: Mapping[str, Any] | None) -> Path:
        return self.cache_path / f"{cache_key(url, params)}.json"

    def _read_cache(
        self, url: str, params: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        if not self.use_cache:
            return None
        path = self._cache_file(url, params)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_cache(
        self,
        url: str,
        params: Mapping[str, Any] | None,
        etag: str | None,
        body: Any,
        headers: Mapping[str, str],
    ) -> None:
        """Store a response for conditional reuse. No header with a secret is kept."""
        if not self.use_cache or not etag or body is None:
            return
        keep = {k: v for k, v in headers.items() if k.lower() in ("link", "etag")}
        payload = {"url": url, "etag": etag, "body": body, "headers": keep}
        path = self._cache_file(url, params)
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:  # pragma: no cover - a full disk must not kill a run
            pass

    # --- logging -------------------------------------------------------------

    def _log(self, url: str, status: int, cached: bool, **extra: Any) -> None:
        """Log request metadata. Never headers, never the token."""
        if self.logger is None:
            return
        self.logger.log(
            "github_request",
            url=self._scrub(url),
            status=status,
            cached=cached,
            remaining=self.rate_limit.remaining,
            requests_made=self.requests_made,
            **extra,
        )


def rate_limit_status(client: GitHubClient) -> dict[str, Any]:
    """Return the ``/rate_limit`` core quota. Costs no quota itself."""
    body = client.get("/rate_limit").body or {}
    core = (body.get("resources") or {}).get("core", {})
    return {
        "limit": core.get("limit"),
        "remaining": core.get("remaining"),
        "reset": core.get("reset"),
        "authenticated": client.authenticated,
    }


def labels(client: GitHubClient, repo: str) -> list[dict[str, Any]]:
    """List every label on ``repo`` (owner/name)."""
    return list(client.paginate(f"/repos/{repo}/labels"))
