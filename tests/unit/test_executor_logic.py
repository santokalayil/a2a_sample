"""
UNIT TESTS — No mocking, no server, no network.
Tests pure Python functions directly.

What's tested here:
  - process_task()           → business logic function
  - AgentRequest model       → pydantic validation
  - AgentResponse model      → pydantic validation
  - create_agent_response_from_text() → helper that builds A2A Message
  - construct_a2a_response()          → helper that wraps AgentResponse in A2A Message
"""

import pytest
from a2a.types import Message

from a2a_server.models import AgentRequest, AgentResponse
from a2a_server.executor import (
    process_task,
    create_agent_response_from_text,
    construct_a2a_response,
)


# ──────────────────────────────────────────────
# process_task() — business logic
# ──────────────────────────────────────────────

class TestProcessTask:
    """Tests for the process_task() function.
    
    This is pure Python — no A2A, no HTTP, nothing external.
    Just call it and assert the return value.
    """

    def test_new_task_returns_input_required_response(self):
        """When input_provided=False (default), task should ask for user input."""
        req = AgentRequest(user_id="u1", session_id="s1", request="hello")

        result = process_task(req, input_provided=False)

        # It is an AgentResponse
        assert isinstance(result, AgentResponse)
        # Response text contains the prompt to user
        assert "Input required" in result.response
        # user_id and session_id are preserved
        assert result.user_id == "u1"
        assert result.session_id == "s1"

    def test_existing_task_with_input_returns_completed_response(self):
        """When input_provided=True, task should process and return completion."""
        req = AgentRequest(user_id="u1", session_id="s1", request="B")

        result = process_task(req, input_provided=True)

        assert isinstance(result, AgentResponse)
        assert "completed" in result.response.lower()
        assert result.user_id == "u1"
        assert result.session_id == "s1"

    def test_user_id_is_passed_through(self):
        """Ensure user_id flows from request to response."""
        req = AgentRequest(user_id="special_user_99", session_id="sess", request="hi")
        result = process_task(req)
        assert result.user_id == "special_user_99"

    def test_session_id_is_passed_through(self):
        """Ensure session_id flows from request to response."""
        req = AgentRequest(user_id="u", session_id="my_session_xyz", request="hi")
        result = process_task(req)
        assert result.session_id == "my_session_xyz"


# ──────────────────────────────────────────────
# AgentRequest — pydantic model validation
# ──────────────────────────────────────────────

class TestAgentRequestModel:
    """Tests for AgentRequest pydantic model.
    
    Verifies what data is accepted or rejected before it even
    reaches execute() or process_task().
    """

    def test_valid_request_is_created(self):
        req = AgentRequest(user_id="u1", session_id="s1", request="what is AI?")
        assert req.user_id == "u1"
        assert req.session_id == "s1"
        assert req.request == "what is AI?"

    def test_missing_user_id_raises_error(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc_info:
            AgentRequest(session_id="s1", request="hello")  # user_id missing
        assert "user_id" in str(exc_info.value)

    def test_missing_session_id_raises_error(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AgentRequest(user_id="u1", request="hello")     # session_id missing

    def test_missing_request_field_raises_error(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AgentRequest(user_id="u1", session_id="s1")     # request missing

    def test_model_validate_from_dict(self):
        """model_validate() is how execute() parses incoming data."""
        data = {"user_id": "u1", "session_id": "s1", "request": "hi"}
        req = AgentRequest.model_validate(data)
        assert req.request == "hi"

    def test_model_validate_rejects_wrong_field_name(self):
        """AgentResponse.response ≠ AgentRequest.request — ensure it's caught."""
        from pydantic import ValidationError
        # 'response' is AgentResponse's field, NOT AgentRequest's field
        data = {"user_id": "u1", "session_id": "s1", "response": "hi"}
        with pytest.raises(ValidationError):
            AgentRequest.model_validate(data)


# ──────────────────────────────────────────────
# AgentResponse — pydantic model validation
# ──────────────────────────────────────────────

class TestAgentResponseModel:
    """Tests for AgentResponse pydantic model."""

    def test_valid_response_is_created(self):
        resp = AgentResponse(user_id="u1", session_id="s1", response="done")
        assert resp.response == "done"

    def test_missing_response_field_raises_error(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AgentResponse(user_id="u1", session_id="s1")   # response missing

    def test_model_dump_has_correct_keys(self):
        """model_dump() is used when serializing into A2A DataPart."""
        resp = AgentResponse(user_id="u", session_id="s", response="ok")
        dumped = resp.model_dump()
        assert set(dumped.keys()) == {"user_id", "session_id", "response"}


# ──────────────────────────────────────────────
# Helper functions — create_agent_response_from_text / construct_a2a_response
# ──────────────────────────────────────────────

class TestHelperFunctions:
    """Tests for the helper functions that build A2A Message objects.
    
    These are still pure unit tests — no HTTP, no server.
    They just verify the A2A Message is built correctly.
    """

    def test_create_agent_response_from_text_returns_message(self):
        msg = create_agent_response_from_text(
            text="hello there",
            user_id="u1",
            session_id="s1",
            task_id="task-001",
        )
        assert isinstance(msg, Message)

    def test_create_agent_response_has_correct_task_id(self):
        msg = create_agent_response_from_text(
            text="hi", user_id="u", session_id="s", task_id="my-task-id"
        )
        assert msg.task_id == "my-task-id"

    def test_create_agent_response_has_correct_context_id(self):
        msg = create_agent_response_from_text(
            text="hi", user_id="u", session_id="my-session", task_id="t1"
        )
        assert msg.context_id == "my-session"

    def test_create_agent_response_data_part_contains_response_text(self):
        """Verify the Message's DataPart contains the response text in 'response' key."""
        from a2a.utils import get_data_parts
        msg = create_agent_response_from_text(
            text="Task done!", user_id="u", session_id="s", task_id="t"
        )
        parts = get_data_parts(msg.parts)
        assert len(parts) == 1
        assert parts[0]["response"] == "Task done!"

    def test_construct_a2a_response_wraps_agent_response(self):
        agent_resp = AgentResponse(user_id="u", session_id="s", response="result")
        msg = construct_a2a_response("task-xyz", agent_resp)
        assert isinstance(msg, Message)
        assert msg.task_id == "task-xyz"
