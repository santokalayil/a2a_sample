"""
MOCK TESTS — Real execute() logic, but dependencies are faked.

What's mocked here:
  - RequestContext  → MagicMock (fake context with controllable .task_id, .message etc.)
  - EventQueue      → AsyncMock (fake queue that records what was enqueued)
  - TaskUpdater     → patched so we can assert what state transitions happened

Why mock?
  execute() depends on RequestContext and EventQueue, which normally
  come from the HTTP layer and the event system. We don't want a real
  server just to test execute() logic — so we hand it fakes.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    DataPart, Part, Message, Role,
    Task, TaskStatus, TaskState,
)

from a2a_server.executor import AgentExecutor
from a2a_server.models import AgentRequest


# ──────────────────────────────────────────────
# Helpers to build fake A2A objects
# ──────────────────────────────────────────────

def make_fake_message(user_id: str, session_id: str, request_text: str, task_id: str = "task-001") -> Message:
    """Creates a real A2A Message with DataPart, as the client would send."""
    data = {"user_id": user_id, "session_id": session_id, "request": request_text}
    return Message(
        message_id="msg-test-001",
        role=Role.user,
        task_id=task_id,
        context_id=session_id,
        parts=[Part(root=DataPart(data=data))],
    )


def make_fake_context(
    task_id: str = "task-001",
    context_id: str = "ctx-001",
    current_task: Task | None = None,
    message: Message | None = None,
) -> MagicMock:
    """Creates a fake RequestContext with controllable properties."""
    ctx = MagicMock()
    ctx.task_id = task_id
    ctx.context_id = context_id
    ctx.current_task = current_task        # None = new task, Task object = continuation
    ctx.message = message or make_fake_message("u1", context_id, "hello", task_id)
    return ctx


def make_fake_queue() -> AsyncMock:
    """Creates a fake EventQueue that records what events are enqueued."""
    queue = AsyncMock(spec=EventQueue)
    queue.enqueue_event = AsyncMock()
    return queue


# ──────────────────────────────────────────────
# Tests for the NEW TASK flow
# ──────────────────────────────────────────────

class TestExecuteNewTask:
    """
    Tests execute() when context.current_task is None → brand new task.
    
    Expected flow:
      1. updater.submit()          → task state: submitted
      2. updater.start_work()      → task state: working
      3. updater.requires_input()  → task state: input-required
    """

    @pytest.mark.asyncio
    async def test_new_task_calls_submit(self):
        """execute() must always call submit() first for new tasks."""
        ctx = make_fake_context(current_task=None)
        queue = make_fake_queue()
        executor = AgentExecutor()

        with patch("a2a_server.executor.TaskUpdater") as MockUpdater:
            mock_updater = AsyncMock()
            MockUpdater.return_value = mock_updater
            mock_updater.task_id = "task-001"

            await executor.execute(ctx, queue)

            mock_updater.submit.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_task_calls_start_work(self):
        """After submit, execute() should call start_work() for new tasks."""
        ctx = make_fake_context(current_task=None)
        queue = make_fake_queue()
        executor = AgentExecutor()

        with patch("a2a_server.executor.TaskUpdater") as MockUpdater:
            mock_updater = AsyncMock()
            MockUpdater.return_value = mock_updater
            mock_updater.task_id = "task-001"

            await executor.execute(ctx, queue)

            mock_updater.start_work.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_task_calls_requires_input(self):
        """execute() should call requires_input() because process_task() returns input needed."""
        ctx = make_fake_context(current_task=None)
        queue = make_fake_queue()
        executor = AgentExecutor()

        with patch("a2a_server.executor.TaskUpdater") as MockUpdater:
            mock_updater = AsyncMock()
            MockUpdater.return_value = mock_updater
            mock_updater.task_id = "task-001"

            await executor.execute(ctx, queue)

            mock_updater.requires_input.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_task_does_not_call_complete(self):
        """A new task that requires input should NOT call complete() yet."""
        ctx = make_fake_context(current_task=None)
        queue = make_fake_queue()
        executor = AgentExecutor()

        with patch("a2a_server.executor.TaskUpdater") as MockUpdater:
            mock_updater = AsyncMock()
            MockUpdater.return_value = mock_updater
            mock_updater.task_id = "task-001"

            await executor.execute(ctx, queue)

            mock_updater.complete.assert_not_called()


# ──────────────────────────────────────────────
# Tests for the CONTINUATION TASK flow (user replied to input_required)
# ──────────────────────────────────────────────

class TestExecuteExistingTask:
    """
    Tests execute() when context.current_task is an existing Task
    in input_required state — i.e., the user has provided their answer.

    Expected flow:
      1. updater.submit()     → confirms user input received
      2. updater.start_work() → task resuming
      3. updater.complete()   → task finishes
    """

    def make_input_required_task(self, task_id: str = "task-001") -> Task:
        """Creates a fake Task object that's waiting for user input."""
        return Task(
            id=task_id,
            context_id="ctx-001",
            status=TaskStatus(state=TaskState.input_required),
        )

    @pytest.mark.asyncio
    async def test_continuation_calls_complete(self):
        """When continuing an input_required task, execute() should call complete()."""
        task = self.make_input_required_task()
        msg = make_fake_message("u1", "ctx-001", "B", task_id="task-001")  # user's answer
        ctx = make_fake_context(current_task=task, message=msg)
        queue = make_fake_queue()
        executor = AgentExecutor()

        with patch("a2a_server.executor.TaskUpdater") as MockUpdater:
            mock_updater = AsyncMock()
            MockUpdater.return_value = mock_updater
            mock_updater.task_id = "task-001"

            await executor.execute(ctx, queue)

            mock_updater.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_continuation_calls_start_work(self):
        """Continuation should call start_work() before completing."""
        task = self.make_input_required_task()
        msg = make_fake_message("u1", "ctx-001", "A", task_id="task-001")
        ctx = make_fake_context(current_task=task, message=msg)
        queue = make_fake_queue()
        executor = AgentExecutor()

        with patch("a2a_server.executor.TaskUpdater") as MockUpdater:
            mock_updater = AsyncMock()
            MockUpdater.return_value = mock_updater
            mock_updater.task_id = "task-001"

            await executor.execute(ctx, queue)

            mock_updater.start_work.assert_called_once()

    @pytest.mark.asyncio
    async def test_continuation_does_not_call_requires_input(self):
        """A continuation task that completes should NOT put task back into input_required."""
        task = self.make_input_required_task()
        msg = make_fake_message("u1", "ctx-001", "C", task_id="task-001")
        ctx = make_fake_context(current_task=task, message=msg)
        queue = make_fake_queue()
        executor = AgentExecutor()

        with patch("a2a_server.executor.TaskUpdater") as MockUpdater:
            mock_updater = AsyncMock()
            MockUpdater.return_value = mock_updater
            mock_updater.task_id = "task-001"

            await executor.execute(ctx, queue)

            mock_updater.requires_input.assert_not_called()


# ──────────────────────────────────────────────
# Tests for validation failure (bad input data)
# ──────────────────────────────────────────────

class TestExecuteValidationFailure:
    """
    Tests execute() when the incoming data fails AgentRequest validation.
    
    Expected flow:
      1. updater.submit()  → task accepted
      2. updater.reject()  → validation failed, task rejected
    """

    @pytest.mark.asyncio
    async def test_invalid_data_calls_reject(self):
        """If data is missing 'request' field, execute() should call reject()."""
        # Sending AgentResponse shape (wrong field name) instead of AgentRequest
        bad_data = {"user_id": "u1", "session_id": "s1", "response": "bad input"}
        bad_message = Message(
            message_id="msg-002",
            role=Role.user,
            task_id="task-001",
            context_id="s1",
            parts=[Part(root=DataPart(data=bad_data))],
        )
        ctx = make_fake_context(current_task=None, message=bad_message)
        queue = make_fake_queue()
        executor = AgentExecutor()

        with patch("a2a_server.executor.TaskUpdater") as MockUpdater:
            mock_updater = AsyncMock()
            MockUpdater.return_value = mock_updater
            mock_updater.task_id = "task-001"

            await executor.execute(ctx, queue)

            mock_updater.reject.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_data_does_not_call_complete(self):
        """A rejected task must not call complete()."""
        bad_data = {"user_id": "u1", "session_id": "s1"}  # missing 'request'
        bad_message = Message(
            message_id="msg-003",
            role=Role.user,
            task_id="task-001",
            context_id="s1",
            parts=[Part(root=DataPart(data=bad_data))],
        )
        ctx = make_fake_context(current_task=None, message=bad_message)
        queue = make_fake_queue()
        executor = AgentExecutor()

        with patch("a2a_server.executor.TaskUpdater") as MockUpdater:
            mock_updater = AsyncMock()
            MockUpdater.return_value = mock_updater
            mock_updater.task_id = "task-001"

            await executor.execute(ctx, queue)

            mock_updater.complete.assert_not_called()
