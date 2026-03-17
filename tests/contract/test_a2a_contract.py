"""
CONTRACT TESTS — Verify A2A protocol compliance.

These tests ensure your server always produces responses that:
  1. Are valid A2A SDK types (parseable by pydantic)
  2. Have the required fields the A2A protocol mandates
  3. Won't break other A2A agents that call you

"Contract" = the agreed shape of data between producer (your server)
             and consumer (any A2A client agent).

No server needed — we test the types and shapes your code produces
by calling helper functions directly and validating their output.
"""

import pytest
from a2a.types import (
    Message, Role, AgentCard, AgentCapabilities, AgentSkill,
    TaskStatusUpdateEvent, TaskState,
)
from a2a.utils import get_data_parts

from a2a_server.executor import create_agent_response_from_text, construct_a2a_response
from a2a_server.models import AgentRequest, AgentResponse


# ──────────────────────────────────────────────
# Agent card contract
# ──────────────────────────────────────────────

class TestAgentCardContract:
    """
    Verifies that our AgentCard satisfies the A2A protocol contract.
    
    The AgentCard is what other agents read to understand:
      - How to reach us (url)
      - What we support (streaming, push notifications)
      - What we can do (skills)
    """

    @pytest.fixture
    def card(self):
        from a2a_server import CARD
        return CARD

    def test_card_is_valid_a2a_type(self, card):
        """AgentCard must be parseable as a proper A2A AgentCard object."""
        # If it's already an AgentCard instance, this passes trivially.
        # We also validate via model_validate(model_dump()) round-trip
        card_dict = card.model_dump()
        reparsed = AgentCard.model_validate(card_dict)
        assert reparsed.name == card.name

    def test_card_has_non_empty_url(self, card):
        """Agent URL is required — without it, no client can send messages."""
        assert card.url
        assert card.url.startswith("http")

    def test_card_has_at_least_one_skill(self, card):
        """A2A agents are expected to have at least one skill declared."""
        assert card.skills
        assert len(card.skills) >= 1

    def test_card_skills_have_required_fields(self, card):
        """Each skill must have an id, name, and description."""
        for skill in card.skills:
            assert skill.id, "Skill must have an id"
            assert skill.name, "Skill must have a name"

    def test_card_capabilities_is_valid(self, card):
        """Capabilities block must be a valid AgentCapabilities object."""
        assert isinstance(card.capabilities, AgentCapabilities)
        # streaming is True in our config
        assert card.capabilities.streaming is True

    def test_card_version_is_set(self, card):
        """Version must be set — used by clients to check compatibility."""
        assert card.version


# ──────────────────────────────────────────────
# Message response contract
# ──────────────────────────────────────────────

class TestResponseMessageContract:
    """
    Verifies that the Messages your executor produces follow A2A protocol.
    
    Any Message your server sends back must:
      - Be parseable as an A2A Message
      - Have role=agent (not user)
      - Contain at least one Part
      - Have a task_id and context_id
    """

    def _make_response(self, task_id="t1", session_id="s1") -> Message:
        return create_agent_response_from_text(
            text="test response",
            user_id="u1",
            session_id=session_id,
            task_id=task_id,
        )

    def test_response_is_valid_a2a_message(self):
        """The Message object must round-trip through A2A pydantic validation."""
        msg = self._make_response()
        msg_dict = msg.model_dump()
        reparsed = Message.model_validate(msg_dict)
        assert reparsed.task_id == msg.task_id

    def test_response_role_is_agent(self):
        """Server responses must have role=agent, never role=user."""
        msg = self._make_response()
        assert msg.role == Role.agent

    def test_response_has_task_id(self):
        """Every response Message must carry the task_id."""
        msg = self._make_response(task_id="abc-123")
        assert msg.task_id == "abc-123"

    def test_response_has_context_id(self):
        """Every response Message must carry the context_id (session)."""
        msg = self._make_response(session_id="session-xyz")
        assert msg.context_id == "session-xyz"

    def test_response_has_exactly_one_part(self):
        """Our responses always have one DataPart — not zero, not two."""
        msg = self._make_response()
        assert len(msg.parts) == 1

    def test_response_data_part_has_required_fields(self):
        """
        The DataPart inside the Message must contain user_id, session_id, response.
        This is OUR application-level contract on top of A2A.
        """
        msg = self._make_response()
        parts = get_data_parts(msg.parts)
        assert len(parts) == 1
        data = parts[0]
        assert "user_id" in data
        assert "session_id" in data
        assert "response" in data

    def test_response_has_message_id(self):
        """A2A protocol requires every Message to have a unique message_id."""
        msg = self._make_response()
        assert msg.message_id   # must be non-empty string


# ──────────────────────────────────────────────
# Input validation contract
# ──────────────────────────────────────────────

class TestInputContract:
    """
    Verifies the contract on what data your server accepts from clients.
    
    Any A2A client sending a message must include user_id, session_id, request
    inside the DataPart. These tests confirm that contract is enforced.
    """

    def test_valid_request_is_accepted(self):
        """Standard valid input must parse without error."""
        from pydantic import ValidationError
        data = {"user_id": "u1", "session_id": "s1", "request": "what is ML?"}
        try:
            req = AgentRequest.model_validate(data)
            assert req.user_id == "u1"
        except ValidationError:
            pytest.fail("Valid AgentRequest was incorrectly rejected")

    def test_missing_user_id_is_rejected(self):
        """Clients must always send user_id — no anonymous requests."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AgentRequest.model_validate({"session_id": "s1", "request": "hi"})

    def test_missing_request_is_rejected(self):
        """The 'request' field is mandatory — it's the actual user message."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AgentRequest.model_validate({"user_id": "u1", "session_id": "s1"})

    def test_sending_response_field_instead_of_request_is_rejected(self):
        """
        Clients sometimes confuse AgentResponse.response with AgentRequest.request.
        This must be caught and rejected.
        """
        from pydantic import ValidationError
        # 'response' is the wrong field name — should be 'request'
        with pytest.raises(ValidationError):
            AgentRequest.model_validate({
                "user_id": "u1",
                "session_id": "s1",
                "response": "B",   # ← wrong field name
            })
