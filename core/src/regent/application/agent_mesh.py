"""V3 Agent Mesh — A2A (Agent-to-Agent) protocol and MCP (Model Context Protocol) client.

P2 candidate: provides inter-agent delegation and tool-sharing capabilities.
No existing implementation in the codebase; retained from V3 component set.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# A2A Protocol
# ---------------------------------------------------------------------------


class A2ATaskStatus(StrEnum):
    """Status of an A2A delegation task."""

    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class A2ATask:
    """A single delegation task between two agents."""

    task_id: str
    from_agent: str
    to_agent: str
    task_description: str
    status: A2ATaskStatus = A2ATaskStatus.PENDING
    output_data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class A2AProtocol:
    """Minimal Agent-to-Agent delegation protocol.

    Supports the lifecycle: create → accept → complete / fail.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, A2ATask] = {}

    # -- lifecycle ----------------------------------------------------------

    def create_delegation(
        self,
        from_agent: str,
        to_agent: str,
        description: str,
    ) -> A2ATask:
        """Create a new delegation task."""
        task = A2ATask(
            task_id=str(uuid.uuid4()),
            from_agent=from_agent,
            to_agent=to_agent,
            task_description=description,
        )
        self._tasks[task.task_id] = task
        return task

    def accept_task(self, task_id: str, agent_id: str) -> A2ATask:
        """Accept a pending task."""
        task = self._tasks[task_id]
        if task.to_agent != agent_id:
            raise ValueError(f"Task {task_id} not assigned to {agent_id}")
        task.status = A2ATaskStatus.ACCEPTED
        return task

    def complete_task(
        self,
        task_id: str,
        agent_id: str,
        output: dict[str, Any],
    ) -> A2ATask:
        """Mark a task as completed with output data."""
        task = self._tasks[task_id]
        if task.to_agent != agent_id:
            raise ValueError(f"Task {task_id} not assigned to {agent_id}")
        task.status = A2ATaskStatus.COMPLETED
        task.output_data = output
        return task

    def fail_task(self, task_id: str, agent_id: str, reason: str) -> A2ATask:
        """Mark a task as failed."""
        task = self._tasks[task_id]
        if task.to_agent != agent_id:
            raise ValueError(f"Task {task_id} not assigned to {agent_id}")
        task.status = A2ATaskStatus.FAILED
        task.error = reason
        return task

    def get_task(self, task_id: str) -> A2ATask | None:
        """Look up a task by id."""
        return self._tasks.get(task_id)


# ---------------------------------------------------------------------------
# MCP (Model Context Protocol) Client
# ---------------------------------------------------------------------------


@dataclass
class MCPToolDefinition:
    """Definition of a tool exposed via MCP."""

    tool_id: str
    name: str
    description: str
    input_schema: dict[str, Any] | None = None


@dataclass
class MCPCallRequest:
    """Request to invoke an MCP tool."""

    tool_id: str
    caller_id: str
    input_data: dict[str, Any] | None = None


@dataclass
class MCPCallResult:
    """Result of an MCP tool invocation."""

    success: bool
    output_data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class MCPClient:
    """Minimal MCP client that manages tool definitions and dispatches calls.

    This is a local/simulated implementation — no real network I/O.
    """

    def __init__(self) -> None:
        self._tools: dict[str, MCPToolDefinition] = {}

    def register_tool(self, tool: MCPToolDefinition) -> None:
        """Register a tool definition."""
        self._tools[tool.tool_id] = tool

    def call_tool(self, request: MCPCallRequest) -> MCPCallResult:
        """Invoke a registered tool.

        Validates required input fields against the tool's input_schema.
        """
        tool = self._tools.get(request.tool_id)
        if tool is None:
            return MCPCallResult(success=False, error=f"Tool '{request.tool_id}' not found")

        # Validate required input fields
        if tool.input_schema:
            required = tool.input_schema.get("required", [])
            input_data = request.input_data or {}
            missing = [f for f in required if f not in input_data]
            if missing:
                return MCPCallResult(
                    success=False,
                    error=f"Required input fields missing: {missing}",
                )

        # Simulated successful execution
        return MCPCallResult(
            success=True,
            output_data={"tool_id": request.tool_id, "caller": request.caller_id},
        )

    def list_tools(self) -> list[MCPToolDefinition]:
        """Return all registered tools."""
        return list(self._tools.values())


# ---------------------------------------------------------------------------
# Agent Mesh — combines A2A + MCP
# ---------------------------------------------------------------------------


class AgentMesh:
    """High-level facade combining A2A delegation and MCP tool sharing.

    M3 Read-switch: when a durable ``AgentTaskService`` is attached, delegations
    are forwarded there. M6 Contract closes production in-memory A2A entirely
    (override ``use_memory=True`` only for unit tests).
    """

    def __init__(
        self,
        *,
        durable_tasks: Any | None = None,
        use_memory: bool | None = None,
    ) -> None:
        from regent.application.aar1_contract import is_contract_phase, memory_a2a_allowed
        from regent.config import get_settings

        self.a2a = A2AProtocol()
        self.mcp = MCPClient()
        self._durable = durable_tasks
        phase = get_settings().aar1_phase
        if use_memory is None and phase == "enforce" and durable_tasks is not None:
            self._use_memory = False
        else:
            self._use_memory = memory_a2a_allowed(
                phase=phase, use_memory_override=use_memory
            )
        self._phase = phase
        self._contract = is_contract_phase(phase)

    def _reject_memory_if_closed(self) -> None:
        if self._use_memory:
            return
        if self._durable is not None:
            raise RuntimeError(
                "memory A2A path closed; use AgentTaskService.offer_task"
            )
        raise RuntimeError(
            "memory A2A path closed in contract phase; wire AgentTaskService"
        )

    def delegate_task(
        self,
        from_agent: str,
        to_agent: str,
        description: str,
    ) -> A2ATask:
        """Create a delegation task via the A2A protocol."""
        self._reject_memory_if_closed()
        return self.a2a.create_delegation(from_agent, to_agent, description)

    def call_tool(
        self,
        caller_id: str,
        tool_name: str,
        input_data: dict[str, Any] | None = None,
    ) -> MCPCallResult:
        """Call a tool by name, resolving to tool_id automatically.

        If *tool_name* matches a registered tool_id directly it is used as-is;
        otherwise the first tool whose ``name`` matches is selected.
        """
        # Direct tool_id match
        if tool_name in self.mcp._tools:
            return self.mcp.call_tool(
                MCPCallRequest(tool_id=tool_name, caller_id=caller_id, input_data=input_data),
            )

        # Resolve by name
        for tool in self.mcp._tools.values():
            if tool.name == tool_name:
                return self.mcp.call_tool(
                    MCPCallRequest(
                        tool_id=tool.tool_id,
                        caller_id=caller_id,
                        input_data=input_data,
                    ),
                )

        return MCPCallResult(success=False, error=f"Tool '{tool_name}' not found")

    def route_with_envelope(
        self,
        envelope: Any,
        *,
        description: str = "",
    ) -> A2ATask:
        """Route a message via AgentEnvelope with capability scope propagation.

        The envelope's capability_scope is propagated to the delegated task.
        Child agent permissions are a subset of the parent's authorization.
        """
        from regent.application.agent_envelope import AgentEnvelope

        if not isinstance(envelope, AgentEnvelope):
            raise TypeError(f"expected AgentEnvelope, got {type(envelope)}")

        self._reject_memory_if_closed()

        # Verify content trust
        if not envelope.verify_trust():
            return A2ATask(
                task_id=str(uuid.uuid4()),
                from_agent=envelope.source_agent,
                to_agent=envelope.dest_agent,
                task_description="REJECTED: content trust verification failed",
                status=A2ATaskStatus.FAILED,
            )

        # Create delegation with capability scope in metadata
        task = self.a2a.create_delegation(
            envelope.source_agent,
            envelope.dest_agent,
            description or f"envelope:{envelope.envelope_id}",
        )
        # Attach capability scope and permit refs to task metadata
        task.metadata.update({
            "capability_scope": sorted(envelope.capability_scope),
            "permit_refs": list(envelope.permit_refs),
            "envelope_id": str(envelope.envelope_id),
            "content_digest": envelope.content_digest,
        })
        return task
