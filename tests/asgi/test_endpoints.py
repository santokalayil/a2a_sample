"""
ASGI TESTS — Real FastAPI app, no real server, no real network.

httpx.ASGITransport talks directly to the FastAPI app in-process.
This tests the full HTTP layer (routing, middleware, serialization)
without starting uvicorn or touching Redis.

What's tested here:
  - GET /.well-known/agent-card.json  → agent card shape and values
  - POST /                            → A2A JSON-RPC endpoint responds

NOTE: The POST / test uses a minimal A2A request. The Redis TaskStore
      IS called under the hood — so we patch it to avoid needing Redis.
"""

import pytest
import httpx
from unittest.mock import AsyncMock, patch


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def asgi_client():
    """
    Provides an httpx client that talks to the FastAPI app in-process.
    No real server is started. This is the key pattern for ASGI tests.
    """
    from a2a_server import fastapi_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url="http://test",
    ) as client:
        yield client


# ──────────────────────────────────────────────
# Agent Card endpoint tests
# ──────────────────────────────────────────────

class TestAgentCardEndpoint:
    """
    Tests the GET /.well-known/agent-card.json endpoint.
    
    This endpoint is the A2A equivalent of a health check — clients
    hit this first to discover the agent before sending any messages.
    """

    @pytest.mark.anyio
    async def test_agent_card_returns_200(self, asgi_client):
        """The agent card endpoint must return HTTP 200 OK."""
        response = await asgi_client.get("/.well-known/agent-card.json")
        assert response.status_code == 200

    @pytest.mark.anyio
    async def test_agent_card_returns_json(self, asgi_client):
        """The response body must be valid JSON."""
        response = await asgi_client.get("/.well-known/agent-card.json")
        data = response.json()   # raises if not valid JSON
        assert isinstance(data, dict)

    @pytest.mark.anyio
    async def test_agent_card_has_name(self, asgi_client):
        """Agent card must contain a 'name' field."""
        response = await asgi_client.get("/.well-known/agent-card.json")
        data = response.json()
        assert "name" in data
        assert data["name"] == "QnA Agent"

    @pytest.mark.anyio
    async def test_agent_card_has_url(self, asgi_client):
        """Agent card must contain a 'url' field (what clients use to send messages)."""
        response = await asgi_client.get("/.well-known/agent-card.json")
        data = response.json()
        assert "url" in data
        assert data["url"].startswith("http")

    @pytest.mark.anyio
    async def test_agent_card_has_capabilities(self, asgi_client):
        """Agent card must declare its capabilities (streaming, etc.)."""
        response = await asgi_client.get("/.well-known/agent-card.json")
        data = response.json()
        assert "capabilities" in data
        caps = data["capabilities"]
        assert "streaming" in caps

    @pytest.mark.anyio
    async def test_agent_card_has_skills(self, asgi_client):
        """Agent card must list at least one skill."""
        response = await asgi_client.get("/.well-known/agent-card.json")
        data = response.json()
        assert "skills" in data
        assert len(data["skills"]) >= 1
        assert data["skills"][0]["id"] == "QnA_skill"

    @pytest.mark.anyio
    async def test_agent_card_has_version(self, asgi_client):
        """Agent card must include a version."""
        response = await asgi_client.get("/.well-known/agent-card.json")
        data = response.json()
        assert "version" in data


# ──────────────────────────────────────────────
# RPC endpoint shape tests (without execute)
# ──────────────────────────────────────────────

class TestRPCEndpoint:
    """
    Tests the POST / A2A endpoint at the HTTP layer.
    
    We only check that the endpoint exists and rejects invalid requests
    correctly. We don't test full execution here (that's the mock tests).
    """

    @pytest.mark.anyio
    async def test_rpc_endpoint_exists(self, asgi_client):
        """POST / should not return 404. Even with bad body, it means routing works."""
        response = await asgi_client.post("/", json={})
        # 400 or 422 is fine — means the route exists but input was bad
        assert response.status_code != 404

    @pytest.mark.anyio
    async def test_rpc_endpoint_rejects_empty_body(self, asgi_client):
        """An empty JSON-RPC request should return an error response, not 500."""
        response = await asgi_client.post("/", json={})
        # A2A returns 200 with JSON-RPC error body, or 4xx — but NEVER 500 for bad input
        assert response.status_code in (200, 400, 422)

    @pytest.mark.anyio
    async def test_rpc_endpoint_requires_json(self, asgi_client):
        """Non-JSON body should be rejected."""
        response = await asgi_client.post(
            "/",
            content=b"not json at all",
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code in (400, 415, 422)
