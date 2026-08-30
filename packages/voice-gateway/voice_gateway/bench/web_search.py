"""Web search via the lab's own SearXNG.

SearXNG already runs on ser5 for Open WebUI (ser5/ansible/roles/searxng). It
publishes on 127.0.0.1:8888 for debugging, which is exactly where the gateway
sits, so this needs no new service and no new exposure.

`format=json` works because the role's settings.yml.j2 sets
`formats: [html, json]` and turns the limiter off — both are non-default in
upstream SearXNG, and without them this returns 403.

THIS IS THE ONLY THING IN THE SYSTEM THAT LEAVES THE PROPERTY, and it leaves as
SEARCH TERMS. The utterance is not a search query and is never forwarded as one:
what goes out is the fragment Judgment extracted, which is why `search()` takes a
query string and has no way to be handed a transcript by accident.

Every call writes an `egress` row naming the brief that justified it. The
recording happens HERE rather than at the call site so that no future caller can
add a path that reaches the internet without appearing in the audit trail. A call
with no brief is still recorded, with a null brief_id — which is what makes the
transitional presence-side path visible as the anomaly it is, rather than
invisible.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from ..ledger import Ledger

log = logging.getLogger(__name__)


class WebSearch:
    def __init__(
        self,
        url: str,
        *,
        results: int = 5,
        timeout: float = 8.0,
        ledger: "Ledger | None" = None,
    ) -> None:
        self._url = url
        self._results = results
        self._ledger = ledger
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(
        self, query: str, *, brief_id: str | None = None
    ) -> list[dict[str, str]]:
        if self._ledger is not None:
            # Recorded BEFORE the request, not after. A query that hangs or
            # errors still left the house, and an audit trail that only lists
            # successful requests is not an audit trail.
            await self._ledger.record_egress(brief_id, self._url, query)
        resp = await self._client.get(
            self._url,
            params={"q": query, "format": "json"},
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        out: list[dict[str, str]] = []
        for item in (resp.json().get("results") or [])[: self._results]:
            out.append(
                {
                    "title": (item.get("title") or "").strip(),
                    "url": (item.get("url") or "").strip(),
                    # SearXNG calls the snippet "content". The role sets
                    # bypass_web_loader for Open WebUI because fetching whole
                    # pages is the usual hang; we want snippets for the same
                    # reason plus one more — a spoken answer cannot use a page.
                    "snippet": (item.get("content") or "").strip(),
                }
            )
        return out

    @staticmethod
    def as_context(query: str, results: list[dict[str, str]]) -> str:
        if not results:
            return f"Web search for {query!r} returned no results."
        lines = [f"Web search results for {query!r}:"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']} — {r['snippet']}")
        return "\n".join(lines)

    async def health(self) -> bool:
        try:
            # Health checks go out too, so they are recorded like anything else.
            await self.search("ping")
        except Exception:  # noqa: BLE001 - health must not raise
            return False
        return True
