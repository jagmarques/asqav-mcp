"""Asqav MCP Server - AI agent governance tools for MCP clients."""

import json
import os
import re
import time
import uuid
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("asqav")

API_URL = os.environ.get("ASQAV_API_URL", "https://api.asqav.com")
API_KEY = os.environ.get("ASQAV_API_KEY", "")

# In-memory tool policies for enforcement.
# Loaded from ASQAV_PROXY_TOOLS env var (JSON) on startup,
# and managed at runtime via create_tool_policy / delete_tool_policy.
_tool_policies: dict[str, dict[str, Any]] = {}

# Rate tracking for per-tool rate limits.
_rate_tracker: dict[str, list[float]] = {}

# Pending gate approvals waiting for completion via complete_action.
# Maps gate_id -> {"signature_id": str, "payload": dict}
_pending_gates: dict[str, dict[str, Any]] = {}


def _load_tool_policies():
    """Load tool policies from ASQAV_PROXY_TOOLS env var."""
    raw = os.environ.get("ASQAV_PROXY_TOOLS", "")
    if raw:
        try:
            policies = json.loads(raw)
            _tool_policies.update(policies)
        except json.JSONDecodeError:
            pass


_load_tool_policies()


async def _request(method: str, path: str, json_body: dict | None = None) -> dict[str, Any]:
    """Make an authenticated request to the Asqav API."""
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method, f"{API_URL}/api/v1{path}", headers=headers, json=json_body, timeout=30.0
        )
        response.raise_for_status()
        return response.json()


def _check_rate_limit(tool_name: str, max_per_minute: int) -> bool:
    """Check if a tool call is within its rate limit. Returns True if allowed."""
    now = time.time()
    if tool_name not in _rate_tracker:
        _rate_tracker[tool_name] = []

    # Remove entries older than 60 seconds.
    _rate_tracker[tool_name] = [t for t in _rate_tracker[tool_name] if now - t < 60]

    if len(_rate_tracker[tool_name]) >= max_per_minute:
        return False

    _rate_tracker[tool_name].append(now)
    return True


# ---------------------------------------------------------------------------
# Existing tools: policy check, signing, agent management, verification
# ---------------------------------------------------------------------------


@mcp.tool()
async def check_policy(action_type: str, agent_id: str | None = None) -> str:
    """Check if an action is allowed by the organization's policies.

    Args:
        action_type: The action to check (e.g. "data:read:users", "api:external:call")
        agent_id: Optional agent ID to check policies for
    """
    try:
        policies = await _request("GET", "/policies")
        matching = []
        for p in policies:
            if not p.get("is_active"):
                continue
            pattern = p.get("action_pattern", "")
            if pattern == "*" or action_type.startswith(pattern.rstrip("*")):
                matching.append(p)

        if not matching:
            return f"ALLOWED: No policies match action '{action_type}'"

        blocked = [p for p in matching if p["action"] in ("block", "block_and_alert")]
        if blocked:
            names = ", ".join(p["name"] for p in blocked)
            return f"BLOCKED: Action '{action_type}' blocked by: {names}"

        alerted = [p for p in matching if p["action"] in ("alert", "block_and_alert")]
        if alerted:
            names = ", ".join(p["name"] for p in alerted)
            return f"ALLOWED with ALERT: Action '{action_type}' triggers alerts: {names}"

        return f"ALLOWED: Action '{action_type}' passes all policies"
    except Exception as e:
        return f"Error checking policies: {e}"


@mcp.tool()
async def sign_action(
    agent_id: str, action_type: str, action_id: str, payload: str | None = None
) -> str:
    """Create a signed audit record for an AI agent action.

    Args:
        agent_id: The agent performing the action
        action_type: Type of action (e.g. "data:read", "api:call")
        action_id: Unique identifier for this action
        payload: Optional JSON payload describing the action details
    """
    try:
        body: dict[str, Any] = {
            "agent_id": agent_id,
            "action_type": action_type,
            "action_id": action_id,
        }
        if payload:
            try:
                body["payload"] = json.loads(payload)
            except json.JSONDecodeError:
                body["payload"] = {"raw": payload}

        result = await _request("POST", "/sign", json_body=body)
        sig_id = result.get("signature_id", "unknown")
        algorithm = result.get("algorithm", "unknown")
        return f"Signed: {sig_id} (algorithm: {algorithm})"
    except Exception as e:
        return f"Error signing action: {e}"


@mcp.tool()
async def list_agents() -> str:
    """List all registered AI agents in the organization."""
    try:
        agents = await _request("GET", "/agents")
        if not agents:
            return "No agents registered."
        lines = []
        for a in agents:
            status = a.get("status", "unknown")
            name = a.get("name", "unnamed")
            aid = a.get("agent_id", "?")
            lines.append(f"- {name} ({aid}) [{status}]")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing agents: {e}"


@mcp.tool()
async def get_agent(agent_id: str) -> str:
    """Get details for a specific AI agent.

    Args:
        agent_id: The agent ID to look up
    """
    try:
        agent = await _request("GET", f"/agents/{agent_id}")
        return json.dumps(agent, indent=2, default=str)
    except Exception as e:
        return f"Error getting agent: {e}"


@mcp.tool()
async def verify_signature(signature_id: str) -> str:
    """Verify a previously created signature.

    Args:
        signature_id: The signature ID to verify
    """
    try:
        result = await _request("GET", f"/verify/{signature_id}")
        valid = result.get("valid", False)
        status = "VALID" if valid else "INVALID"
        return f"{status}: Signature {signature_id}"
    except Exception as e:
        return f"Error verifying signature: {e}"


# ---------------------------------------------------------------------------
# Enforcement tools: gate_action, enforced_tool_call, policy management
# ---------------------------------------------------------------------------


@mcp.tool()
async def gate_action(
    action_type: str,
    agent_id: str,
    tool_name: str | None = None,
    arguments: str | None = None,
    risk_context: str | None = None,
) -> str:
    """Pre-execution enforcement gate. MUST be called before any irreversible action.

    Checks policy, signs the decision (allow or deny), and returns a verdict.
    Both approvals and denials are recorded in the audit trail so there is
    cryptographic proof that the check happened.

    After the agent completes an approved action, call complete_action(gate_id, result)
    to create a bilateral receipt that binds the approval and the outcome together.
    This closes the audit gap where an auditor could prove approval but not outcome.

    Args:
        action_type: The action to gate (e.g. "data:delete:users", "tool:execute:sql")
        agent_id: The agent requesting the action
        tool_name: Optional name of the tool being invoked
        arguments: Optional JSON string of the tool arguments
        risk_context: Optional description of why this action is risky
    """
    gate_id = str(uuid.uuid4())

    try:
        # Check organization policies via API.
        policy_result = await check_policy(action_type, agent_id)
        decision = "DENIED" if policy_result.startswith("BLOCKED") else "APPROVED"

        # Check local tool policies if a tool name is provided.
        if tool_name and tool_name in _tool_policies:
            tp = _tool_policies[tool_name]

            # Hidden tools are treated as nonexistent.
            if tp.get("hidden", False):
                return json.dumps({
                    "decision": "ERROR",
                    "reason": f"Tool '{tool_name}' not found",
                    "gate_id": gate_id,
                })

            # Check rate limit.
            max_rpm = tp.get("max_calls_per_minute", 0)
            if max_rpm > 0 and not _check_rate_limit(tool_name, max_rpm):
                decision = "DENIED"
                policy_result = f"BLOCKED: Rate limit exceeded ({max_rpm}/min) for tool '{tool_name}'"

            # High-risk tools require multi-party approval.
            if tp.get("risk_level") == "high" and tp.get("require_approval", False):
                decision = "PENDING_APPROVAL"
                policy_result = f"REQUIRES APPROVAL: Tool '{tool_name}' is high-risk"

            # Blocked tools.
            if tp.get("blocked", False):
                decision = "DENIED"
                policy_result = f"BLOCKED: Tool '{tool_name}' is blocked by local policy"

        # Build the approval payload to sign.
        parsed_arguments: Any = None
        if arguments:
            try:
                parsed_arguments = json.loads(arguments)
            except json.JSONDecodeError:
                parsed_arguments = {"raw": arguments}

        sign_payload = {
            "gate_id": gate_id,
            "decision": decision,
            "action_type": action_type,
            "tool_name": tool_name,
            "arguments": parsed_arguments,
            "policy_result": policy_result,
            "risk_context": risk_context,
            "timestamp": time.time(),
            "receipt_type": "approval",
        }

        sign_result = await _request("POST", "/sign", json_body={
            "agent_id": agent_id,
            "action_type": f"gate:{action_type}",
            "action_id": gate_id,
            "payload": sign_payload,
        })
        sig_id = sign_result.get("signature_id", "unknown")

        if decision == "DENIED":
            return json.dumps({
                "decision": "DENIED",
                "reason": policy_result,
                "gate_id": gate_id,
                "signature_id": sig_id,
            })
        elif decision == "PENDING_APPROVAL":
            # Request multi-party approval via the API.
            try:
                approval = await _request("POST", "/actions/request", json_body={
                    "agent_id": agent_id,
                    "action_type": action_type,
                    "description": risk_context or f"High-risk tool call: {tool_name}",
                })
                return json.dumps({
                    "decision": "PENDING_APPROVAL",
                    "reason": policy_result,
                    "gate_id": gate_id,
                    "signature_id": sig_id,
                    "approval_id": approval.get("action_id", "unknown"),
                    "message": "Action requires human approval before proceeding.",
                })
            except Exception:
                return json.dumps({
                    "decision": "PENDING_APPROVAL",
                    "reason": policy_result,
                    "gate_id": gate_id,
                    "signature_id": sig_id,
                    "message": "Action requires human approval. Check the dashboard.",
                })
        else:
            # Store gate context so complete_action can reference it.
            _pending_gates[gate_id] = {
                "agent_id": agent_id,
                "approval_signature_id": sig_id,
                "action_type": action_type,
                "tool_name": tool_name,
                "arguments": parsed_arguments,
            }
            return json.dumps({
                "decision": "APPROVED",
                "gate_id": gate_id,
                "signature_id": sig_id,
                "message": (
                    "Action approved. After completing the action, call "
                    "complete_action(gate_id, result) to close the bilateral receipt."
                ),
            })

    except Exception as e:
        return json.dumps({
            "decision": "ERROR",
            "reason": str(e),
            "gate_id": gate_id,
            "message": "Enforcement check failed. Fail-closed: action should not proceed.",
        })


@mcp.tool()
async def complete_action(gate_id: str, result: str) -> str:
    """Report the outcome of a gate-approved action and close the bilateral receipt.

    Call this after completing an action that was approved by gate_action. It signs
    the outcome and binds it to the original approval, creating a bilateral receipt:
    cryptographic proof of both what was approved and what actually happened.

    Without this call, an auditor can prove the action was approved but cannot prove
    what the result was. The bilateral receipt closes that gap.

    Args:
        gate_id: The gate_id returned by gate_action when it approved the action
        result: A description or JSON string of the action's outcome
    """
    if gate_id not in _pending_gates:
        return json.dumps({
            "status": "ERROR",
            "reason": f"No pending gate found for gate_id '{gate_id}'. "
                      "Either it was already completed, denied, or the server restarted.",
        })

    gate = _pending_gates.pop(gate_id)
    receipt_id = str(uuid.uuid4())

    try:
        parsed_result: Any
        try:
            parsed_result = json.loads(result)
        except json.JSONDecodeError:
            parsed_result = {"raw": result}

        receipt_payload = {
            "receipt_id": receipt_id,
            "gate_id": gate_id,
            "receipt_type": "bilateral",
            "approval_signature_id": gate["approval_signature_id"],
            "action_type": gate["action_type"],
            "tool_name": gate.get("tool_name"),
            "arguments": gate.get("arguments"),
            "result": parsed_result,
            "completed_at": time.time(),
        }

        sign_result = await _request("POST", "/sign", json_body={
            "agent_id": gate["agent_id"],
            "action_type": f"receipt:{gate['action_type']}",
            "action_id": receipt_id,
            "payload": receipt_payload,
        })
        receipt_sig_id = sign_result.get("signature_id", "unknown")

        return json.dumps({
            "status": "RECEIPT_CREATED",
            "receipt_id": receipt_id,
            "gate_id": gate_id,
            "approval_signature_id": gate["approval_signature_id"],
            "receipt_signature_id": receipt_sig_id,
            "message": (
                "Bilateral receipt created. Both the approval and the outcome are "
                "signed and linked. Verify either signature to confirm the full chain."
            ),
        })

    except Exception as e:
        return json.dumps({
            "status": "ERROR",
            "gate_id": gate_id,
            "reason": str(e),
            "message": "Failed to create bilateral receipt.",
        })


@mcp.tool()
async def enforced_tool_call(
    tool_name: str,
    agent_id: str,
    arguments: str | None = None,
    tool_endpoint: str | None = None,
) -> str:
    """Execute a tool call with policy enforcement. This is the strong enforcement path.

    The MCP server acts as a non-bypassable proxy: it checks policy, and if approved,
    optionally forwards the call to a downstream tool endpoint and signs BOTH the
    request and the response as a single bilateral receipt.

    If tool_endpoint is provided, the call is forwarded and the response is captured
    and signed together with the approval - proving both what was approved and what
    the tool returned. If no tool_endpoint is configured, the approval token is
    returned and the agent can call the tool directly (bounded enforcement).

    Use this instead of calling tools directly when you need enforced governance.

    Args:
        tool_name: Name of the tool to execute
        agent_id: The agent requesting the tool call
        arguments: Optional JSON string of tool arguments
        tool_endpoint: Optional HTTP endpoint to forward the approved call to
    """
    call_id = str(uuid.uuid4())

    try:
        policy = _tool_policies.get(tool_name, {})
        risk_level = policy.get("risk_level", "low")
        action_type = f"tool:execute:{tool_name}"

        parsed_arguments: Any = None
        if arguments:
            try:
                parsed_arguments = json.loads(arguments)
            except json.JSONDecodeError:
                parsed_arguments = {"raw": arguments}

        # Step 1: Check organization policies via API.
        policy_result = await check_policy(action_type, agent_id)
        if policy_result.startswith("BLOCKED"):
            await _request("POST", "/sign", json_body={
                "agent_id": agent_id,
                "action_type": f"enforce:denied:{tool_name}",
                "action_id": call_id,
                "payload": {
                    "tool_name": tool_name,
                    "reason": policy_result,
                    "risk_level": risk_level,
                    "receipt_type": "denial",
                    "timestamp": time.time(),
                },
            })
            return json.dumps({
                "status": "DENIED",
                "tool_name": tool_name,
                "call_id": call_id,
                "reason": policy_result,
            })

        # Step 2: Check local tool policy.
        # Hidden tools are treated as nonexistent - no audit record, no denial message.
        if policy.get("hidden", False):
            return json.dumps({
                "status": "ERROR",
                "tool_name": tool_name,
                "call_id": call_id,
                "reason": f"Tool '{tool_name}' not found",
            })

        if policy.get("blocked", False):
            await _request("POST", "/sign", json_body={
                "agent_id": agent_id,
                "action_type": f"enforce:denied:{tool_name}",
                "action_id": call_id,
                "payload": {
                    "tool_name": tool_name,
                    "reason": "blocked by local policy",
                    "receipt_type": "denial",
                    "timestamp": time.time(),
                },
            })
            return json.dumps({
                "status": "DENIED",
                "tool_name": tool_name,
                "call_id": call_id,
                "reason": f"Tool '{tool_name}' is blocked by local policy",
            })

        # Step 3: Check rate limit.
        max_rpm = policy.get("max_calls_per_minute", 0)
        if max_rpm > 0 and not _check_rate_limit(tool_name, max_rpm):
            await _request("POST", "/sign", json_body={
                "agent_id": agent_id,
                "action_type": f"enforce:rate_limited:{tool_name}",
                "action_id": call_id,
                "payload": {
                    "tool_name": tool_name,
                    "max_per_minute": max_rpm,
                    "receipt_type": "denial",
                    "timestamp": time.time(),
                },
            })
            return json.dumps({
                "status": "DENIED",
                "tool_name": tool_name,
                "call_id": call_id,
                "reason": f"Rate limit exceeded ({max_rpm}/min)",
            })

        # Step 4: High-risk tools require multi-party approval.
        if risk_level == "high" and policy.get("require_approval", False):
            try:
                approval = await _request("POST", "/actions/request", json_body={
                    "agent_id": agent_id,
                    "action_type": action_type,
                    "description": f"High-risk tool call: {tool_name}",
                })
                await _request("POST", "/sign", json_body={
                    "agent_id": agent_id,
                    "action_type": f"enforce:pending:{tool_name}",
                    "action_id": call_id,
                    "payload": {
                        "tool_name": tool_name,
                        "approval_id": approval.get("action_id"),
                        "receipt_type": "pending",
                        "timestamp": time.time(),
                    },
                })
                return json.dumps({
                    "status": "PENDING_APPROVAL",
                    "tool_name": tool_name,
                    "call_id": call_id,
                    "approval_id": approval.get("action_id", "unknown"),
                    "message": "Human approval required before this tool can execute.",
                })
            except Exception:
                return json.dumps({
                    "status": "PENDING_APPROVAL",
                    "tool_name": tool_name,
                    "call_id": call_id,
                    "message": "Human approval required. Check the dashboard.",
                })

        # Step 5: Approved. Optionally forward to the downstream tool endpoint.
        effective_endpoint = tool_endpoint or policy.get("tool_endpoint")

        if effective_endpoint:
            # Forward the call and capture the response for the bilateral receipt.
            tool_response: Any = None
            forward_error: str | None = None
            try:
                async with httpx.AsyncClient() as client:
                    fwd = await client.post(
                        effective_endpoint,
                        json={"tool_name": tool_name, "arguments": parsed_arguments},
                        timeout=60.0,
                    )
                    fwd.raise_for_status()
                    tool_response = fwd.json()
            except Exception as fwd_exc:
                forward_error = str(fwd_exc)

            # Sign request + response together as one bilateral receipt.
            receipt_payload: dict[str, Any] = {
                "call_id": call_id,
                "receipt_type": "bilateral",
                "tool_name": tool_name,
                "arguments": parsed_arguments,
                "risk_level": risk_level,
                "decision": "APPROVED",
                "timestamp": time.time(),
            }
            if tool_response is not None:
                receipt_payload["response"] = tool_response
            if forward_error is not None:
                receipt_payload["forward_error"] = forward_error

            sign_result = await _request("POST", "/sign", json_body={
                "agent_id": agent_id,
                "action_type": f"enforce:bilateral:{tool_name}",
                "action_id": call_id,
                "payload": receipt_payload,
            })
            sig_id = sign_result.get("signature_id", "unknown")

            if forward_error:
                return json.dumps({
                    "status": "APPROVED_FORWARD_FAILED",
                    "tool_name": tool_name,
                    "call_id": call_id,
                    "signature_id": sig_id,
                    "forward_error": forward_error,
                    "message": (
                        "Approval signed but forwarding to tool endpoint failed. "
                        "The bilateral receipt records both the approval and the error."
                    ),
                })

            return json.dumps({
                "status": "EXECUTED",
                "tool_name": tool_name,
                "call_id": call_id,
                "signature_id": sig_id,
                "response": tool_response,
                "message": (
                    f"Tool '{tool_name}' executed. Bilateral receipt signed - "
                    "both request and response are cryptographically bound."
                ),
            })

        else:
            # No endpoint configured - sign approval only and return token.
            # The agent can use complete_action after calling the tool directly
            # to close the bilateral receipt.
            sign_result = await _request("POST", "/sign", json_body={
                "agent_id": agent_id,
                "action_type": f"enforce:approved:{tool_name}",
                "action_id": call_id,
                "payload": {
                    "call_id": call_id,
                    "receipt_type": "approval",
                    "tool_name": tool_name,
                    "arguments": parsed_arguments,
                    "risk_level": risk_level,
                    "decision": "APPROVED",
                    "timestamp": time.time(),
                },
            })
            sig_id = sign_result.get("signature_id", "unknown")

            # Register as a pending gate so complete_action can close the receipt.
            _pending_gates[call_id] = {
                "agent_id": agent_id,
                "approval_signature_id": sig_id,
                "action_type": action_type,
                "tool_name": tool_name,
                "arguments": parsed_arguments,
            }

            return json.dumps({
                "status": "APPROVED",
                "tool_name": tool_name,
                "call_id": call_id,
                "signature_id": sig_id,
                "message": (
                    f"Tool '{tool_name}' execution approved and signed. "
                    "After executing the tool, call complete_action(call_id, result) "
                    "to create a bilateral receipt linking approval to outcome."
                ),
            })

    except Exception as e:
        # Fail-closed: if enforcement check fails, deny the action.
        return json.dumps({
            "status": "ERROR",
            "tool_name": tool_name,
            "call_id": call_id,
            "reason": str(e),
            "message": "Enforcement check failed. Fail-closed: do not proceed.",
        })


@mcp.tool()
async def create_tool_policy(
    tool_name: str,
    risk_level: str = "medium",
    require_approval: bool = False,
    max_calls_per_minute: int = 0,
    blocked: bool = False,
    hidden: bool = False,
    tool_endpoint: str | None = None,
) -> str:
    """Create or update a local enforcement policy for a tool.

    Tool policies control how enforced_tool_call and gate_action handle
    specific tools. Policies are checked locally (no API round-trip) for
    fast enforcement decisions.

    Set tool_endpoint to enable automatic forwarding in enforced_tool_call,
    which produces a bilateral receipt binding the request and the tool's response.

    Args:
        tool_name: Name of the tool to create a policy for
        risk_level: Risk classification - "low", "medium", or "high"
        require_approval: If true, high-risk tools need human approval before execution
        max_calls_per_minute: Rate limit (0 = unlimited)
        blocked: If true, the tool is completely blocked
        hidden: If true, the tool is invisible - not listed and treated as nonexistent
        tool_endpoint: Optional HTTP endpoint to forward approved calls to
    """
    if risk_level not in ("low", "medium", "high"):
        return f"Error: risk_level must be 'low', 'medium', or 'high', got '{risk_level}'"

    _tool_policies[tool_name] = {
        "risk_level": risk_level,
        "require_approval": require_approval,
        "max_calls_per_minute": max_calls_per_minute,
        "blocked": blocked,
        "hidden": hidden,
        "tool_endpoint": tool_endpoint,
    }

    if hidden:
        status = "hidden"
    elif blocked:
        status = "blocked"
    else:
        status = f"{risk_level} risk"
    if require_approval:
        status += ", requires approval"
    if max_calls_per_minute > 0:
        status += f", max {max_calls_per_minute}/min"
    if tool_endpoint:
        status += f", endpoint: {tool_endpoint}"

    return f"Policy created for '{tool_name}': {status}"


@mcp.tool()
async def list_tool_policies() -> str:
    """List all active local tool enforcement policies."""
    if not _tool_policies:
        return "No tool policies configured. Use create_tool_policy to add one, or set ASQAV_PROXY_TOOLS env var."

    lines = []
    for name, policy in sorted(_tool_policies.items()):
        parts = [policy.get("risk_level", "unknown")]
        if policy.get("hidden"):
            parts = ["HIDDEN"]
        elif policy.get("blocked"):
            parts = ["BLOCKED"]
        if policy.get("require_approval"):
            parts.append("approval required")
        if policy.get("max_calls_per_minute", 0) > 0:
            parts.append(f"max {policy['max_calls_per_minute']}/min")
        if policy.get("tool_endpoint"):
            parts.append(f"endpoint: {policy['tool_endpoint']}")
        lines.append(f"- {name}: {', '.join(parts)}")

    return "\n".join(lines)


@mcp.tool()
async def delete_tool_policy(tool_name: str) -> str:
    """Remove a local enforcement policy for a tool.

    Args:
        tool_name: Name of the tool to remove the policy for
    """
    if tool_name in _tool_policies:
        del _tool_policies[tool_name]
        return f"Policy removed for '{tool_name}'"
    return f"No policy found for '{tool_name}'"


# ---------------------------------------------------------------------------
# Tool definition scanner
# ---------------------------------------------------------------------------

# Patterns that indicate prompt injection attempts in descriptions.
_INJECTION_PATTERNS = [
    re.compile(r"\bignore\s+(all\s+)?(previous|prior|above)\b", re.I),
    re.compile(r"\byou\s+(must|should|will|shall)\b", re.I),
    re.compile(r"\bdo\s+not\s+(follow|obey|use)\b", re.I),
    re.compile(r"\bsystem\s*prompt\b", re.I),
    re.compile(r"\bact\s+as\b", re.I),
    re.compile(r"\bnew\s+instructions?\b", re.I),
    re.compile(r"\boverride\b", re.I),
    re.compile(r"\bdisregard\b", re.I),
    re.compile(r"\bpretend\b", re.I),
    re.compile(r"\bfrom\s+now\s+on\b", re.I),
]

# Field names in input_schema that are commonly abused for code execution.
_DANGEROUS_SCHEMA_FIELDS = {"exec", "eval", "command", "shell", "system", "cmd", "subprocess", "spawn"}

# Patterns for hardcoded secrets.
_SECRET_PATTERNS = [
    re.compile(r"\b(sk|pk|api|secret|token|password|passwd|pwd|key)[-_]?[a-z0-9]{16,}\b", re.I),
    re.compile(r"(?<![a-z])(AKIA|ASIA|ABIA)[A-Z0-9]{16}", re.I),  # AWS key prefix
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),   # GitHub token
    re.compile(r"\bxox[bpoa]-[0-9A-Za-z\-]{10,}"),  # Slack token
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]{20,}"),
]

# Common tool names and their misspelling variants to flag typosquatting.
_TYPOSQUAT_PAIRS = [
    ("bash", {"bsh", "bahs", "bas", "b4sh"}),
    ("python", {"pythn", "pyhon", "pyton", "pythoon"}),
    ("execute", {"excute", "exeucte", "executee", "exectue"}),
    ("read_file", {"raed_file", "read_fiel", "readfile"}),
    ("write_file", {"wrtie_file", "write_fiel", "writefile"}),
    ("delete", {"delet", "deletee", "dleete"}),
    ("search", {"serach", "seach", "saerch"}),
    ("request", {"requets", "reqeust", "rquest"}),
]


def _scan_tool_definition_impl(
    tool_name: str, description: str, input_schema: dict | None
) -> dict[str, Any]:
    """Core scanner logic. Returns a dict with risk, level, and findings."""
    findings: list[str] = []
    level = "CLEAN"

    # 1. Prompt injection in description.
    for pat in _INJECTION_PATTERNS:
        if pat.search(description):
            findings.append(f"prompt injection pattern in description: '{pat.pattern}'")
            level = "DANGEROUS"

    # 2. Hidden unicode / zero-width characters in name or description.
    zero_width = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\u00ad]")
    if zero_width.search(tool_name):
        findings.append("zero-width or hidden unicode characters in tool name")
        level = "DANGEROUS"
    if zero_width.search(description):
        findings.append("zero-width or hidden unicode characters in description")
        if level != "DANGEROUS":
            level = "WARNING"

    # 3. Suspicious schema field names.
    if input_schema:
        properties = input_schema.get("properties", {})
        for field in properties:
            if field.lower() in _DANGEROUS_SCHEMA_FIELDS:
                findings.append(f"suspicious schema field: '{field}'")
                if level == "CLEAN":
                    level = "WARNING"

    # 4. Typosquatting detection.
    name_lower = tool_name.lower().replace("-", "_")
    for canonical, variants in _TYPOSQUAT_PAIRS:
        if name_lower in variants:
            findings.append(f"possible typosquat of '{canonical}'")
            if level == "CLEAN":
                level = "WARNING"

    # 5. Hardcoded secrets in description.
    for pat in _SECRET_PATTERNS:
        match = pat.search(description)
        if match:
            findings.append(f"possible hardcoded secret in description (matched: '{match.group()[:12]}...')")
            level = "DANGEROUS"

    return {"risk": level, "findings": findings, "tool_name": tool_name}


@mcp.tool()
async def scan_tool_definition(
    tool_name: str,
    description: str,
    input_schema: str | None = None,
) -> str:
    """Scan an MCP tool definition for security threats.

    Checks for prompt injection, hidden unicode, dangerous schema fields,
    typosquatting, and hardcoded secrets. Returns a risk assessment.

    Args:
        tool_name: The tool name to scan
        description: The tool description to scan
        input_schema: Optional JSON string of the tool's input schema
    """
    schema: dict | None = None
    if input_schema:
        try:
            schema = json.loads(input_schema)
        except json.JSONDecodeError:
            return json.dumps({"risk": "ERROR", "reason": "input_schema is not valid JSON"})

    result = _scan_tool_definition_impl(tool_name, description, schema)

    if result["findings"]:
        result["details"] = result.pop("findings")
    else:
        result.pop("findings")
        result["details"] = []

    return json.dumps(result, indent=2)


@mcp.tool()
async def scan_all_tools() -> str:
    """Scan all currently registered tool policies for security threats.

    Checks tool names for typosquatting and hidden unicode. Returns a
    summary with per-tool risk assessments.
    """
    if not _tool_policies:
        return "No tool policies registered. Use create_tool_policy to add tools first."

    results = []
    for name in sorted(_tool_policies.keys()):
        # Use the tool name as the description proxy since policies don't store descriptions.
        result = _scan_tool_definition_impl(name, name, None)
        results.append({
            "tool_name": name,
            "risk": result["risk"],
            "findings": result["findings"],
        })

    dangerous = [r for r in results if r["risk"] == "DANGEROUS"]
    warnings = [r for r in results if r["risk"] == "WARNING"]
    clean = [r for r in results if r["risk"] == "CLEAN"]

    summary = {
        "scanned": len(results),
        "dangerous": len(dangerous),
        "warnings": len(warnings),
        "clean": len(clean),
        "results": results,
    }
    return json.dumps(summary, indent=2)


def main():
    """Run the Asqav MCP server."""
    if not API_KEY:
        import sys

        print("Warning: ASQAV_API_KEY not set. Set it to authenticate with the API.", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
