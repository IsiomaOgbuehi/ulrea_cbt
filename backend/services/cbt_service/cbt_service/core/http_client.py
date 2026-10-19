import httpx

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Shared client: reuses TCP/TLS connections across requests."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=2.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None