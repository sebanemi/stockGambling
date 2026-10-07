"""HTTP access for metadata providers.

Metadata sources are public, anonymous and unauthenticated, so no credential
plumbing lives here. What *is* here is everything an adapter must not re-invent:
a browser-like User-Agent (both sources 403 the default ``python-httpx``), a
hard timeout, bounded retries with backoff for transient failures, and a typed
error so a failed fetch can never be mistaken for an empty universe.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from email.utils import formatdate

import httpx

from app.providers.base import ProviderError

#: Callers inject a fetcher of this shape, which is how adapter tests are
#: driven from recorded fixtures instead of the network.
FetchCallable = Callable[[str], bytes]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 1.5


class HttpFetcher:
    """Fetch a URL and return the raw response body.

    Retries only on transport errors and 5xx responses. A 404 or a 403 is
    returned to the caller as an error immediately: retrying a page that will
    never appear only delays a report that something upstream has changed.
    """

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
        backoff: float = DEFAULT_BACKOFF_SECONDS,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        """Configure the fetcher."""
        self._timeout = timeout
        self._retries = retries
        self._backoff = backoff
        self._user_agent = user_agent

    def _headers(self) -> dict[str, str]:
        """Request headers that make the official endpoints answer as a browser."""
        return {
            "User-Agent": self._user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "If-Modified-Since": formatdate(usegmt=True),
        }

    def __call__(self, url: str) -> bytes:
        """Return the body of ``url``.

        Raises:
            ProviderError: on a transport failure, a non-success status, or an
                empty body.
        """
        last_error: Exception | None = None

        for attempt in range(1, self._retries + 1):
            try:
                with httpx.Client(
                    timeout=self._timeout,
                    follow_redirects=True,
                    headers=self._headers(),
                ) as client:
                    response = client.get(url)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code >= 500:
                    last_error = httpx.HTTPStatusError(
                        f"server error {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                elif response.is_success:
                    if not response.content:
                        raise ProviderError(f"{url} returned an empty body")
                    return response.content
                else:
                    raise ProviderError(
                        f"{url} returned HTTP {response.status_code}; "
                        "the source layout or availability has probably changed"
                    )

            if attempt < self._retries:
                time.sleep(self._backoff * attempt)

        raise ProviderError(
            f"{url} could not be fetched after {self._retries} attempts: {last_error}"
        )


def decode_html(payload: bytes, default_encoding: str = "utf-8") -> str:
    """Decode an HTML document, guessing between UTF-8 and a Latin-1 fallback.

    ``cajadevalores.com.ar`` serves UTF-8; other Argentine institutional sites
    serve ``windows-1252`` without saying so. UTF-8 is tried first and the
    fallback is used only when it produced replacement characters, so correct
    UTF-8 documents are never double-decoded into mojibake.

    A charset declared in ``Content-Type`` or a ``<meta>`` tag is deliberately
    not read: the sources disagree with themselves about it, and a wrong
    declared value is worse than the trial decode. The wire charset is passed
    out of band by callers that know it.
    """
    text = payload.decode("utf-8", errors="replace")
    if "�" not in text:
        return text
    return payload.decode(default_encoding, errors="replace")
