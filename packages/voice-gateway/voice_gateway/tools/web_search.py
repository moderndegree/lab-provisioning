"""Web search via the lab's own SearXNG.

SearXNG already runs on ser5 for Open WebUI (ser5/ansible/roles/searxng). It
publishes on 127.0.0.1:8888 for debugging, which is exactly where the gateway
sits, so this needs no new service and no new exposure.

`format=json` works because the role's settings.yml.j2 sets
`formats: [html, json]` and turns the limiter off — both are non-default in
upstream SearXNG, and without them this returns 403.

The result is injected into the SAME model call that answers, not a second one.
A tool-call round trip would double time-to-first-word, which is the number this
whole design exists to minimise.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


class WebSearch:
    def __init__(self, url: str, *, results: int = 5, timeout: float = 8.0) -> None:
        self._url = url
        self._results = results
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, query: str) -> list[dict[str, str]]:
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
            await self.search("ping")
        except Exception:  # noqa: BLE001 - health must not raise
            return False
        return True
