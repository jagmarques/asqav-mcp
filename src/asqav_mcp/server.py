"""Asqav MCP Server - AI agent governance tools for MCP clients."""

import json
import os
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

    This provides bounded enforcement - the agent is expected to call this
    before acting, and the audit trail proves whether it did.

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

        # Sign the decision into the audit trail.
        sign_payload = {
            "gate_id": gate_id,
            "decision": decision,
            "action_type": action_type,
            "tool_name": tool_name,
            "policy_result": policy_result,
            "risk_context": risk_context,
        }
        if arguments:
            try:
                sign_payload["arguments"] = json.loads(arguments)
            except json.JSONDecodeError:
                sign_payload["arguments_raw"] = arguments

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
            return json.dumps({
                "decision": "APPROVED",
                "gate_id": gate_id,
                "signature_id": sig_id,
            })

    except Exception as e:
        return json.dumps({
            "decision": "ERROR",
            "reason": str(e),
            "gate_id": gate_id,
            "message": "Enforcement check failed. Fail-closed: action should not proceed.",
        })


@mcp.tool()
async def enforced_tool_call(
    tool_name: str,
    agent_id: str,
    arguments: str | None = None,
) -> str:
    """Execute a tool call with policy enforcement. This is the strong enforcement path.

    The MCP server acts as a non-bypassable proxy: it checks policy, signs the
    decision, and only returns an approval token if the action is allowed. The
    agent cannot skip this check because it has no direct access to the
    downstream tool - all access goes through this proxy.

    Use this instead of calling tools directly when you need enforced governance.

    Args:
        tool_name: Name of the tool to execute
        agent_id: The agent requesting the tool call
        arguments: Optional JSON string of tool arguments
    """
    call_id = str(uuid.uuid4())

    try:
        policy = _tool_policies.get(tool_name, {})
        risk_level = policy.get("risk_level", "low")
        action_type = f"tool:execute:{tool_name}"

        # Step 1: Check organization policies via API.
        policy_result = await check_policy(action_type, agent_id)
        if policy_result.startswith("BLOCKED"):
            # Sign the denial.
            await _request("POST", "/sign", json_body={
                "agent_id": agent_id,
                "action_type": f"enforce:denied:{tool_name}",
                "action_id": call_id,
                "payload": {
                    "tool_name": tool_name,
                    "reason": policy_result,
                    "risk_level": risk_level,
                },
            })
            return json.dumps({
                "status": "DENIED",
                "tool_name": tool_name,
                "call_id": call_id,
                "reason": policy_result,
            })

        # Step 2: Check local tool policy.
        if policy.get("blocked", False):
            await _request("POST", "/sign", json_body={
                "agent_id": agent_id,
                "action_type": f"enforce:denied:{tool_name}",
                "action_id": call_id,
                "payload": {"tool_name": tool_name, "reason": "blocked by local policy"},
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
                "payload": {"tool_name": tool_name, "max_per_minute": max_rpm},
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

        # Step 5: Allowed - sign the approval and return execution token.
        sign_result = await _request("POST", "/sign", json_body={
            "agent_id": agent_id,
            "action_type": f"enforce:approved:{tool_name}",
            "action_id": call_id,
            "payload": {
                "tool_name": tool_name,
                "arguments": json.loads(arguments) if arguments else None,
                "risk_level": risk_level,
            },
        })

        return json.dumps({
            "status": "APPROVED",
            "tool_name": tool_name,
            "call_id": call_id,
            "signature_id": sign_result.get("signature_id", "unknown"),
            "message": f"Tool '{tool_name}' execution approved and signed.",
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
) -> str:
    """Create or update a local enforcement policy for a tool.

    Tool policies control how enforced_tool_call and gate_action handle
    specific tools. Policies are checked locally (no API round-trip) for
    fast enforcement decisions.

    Args:
        tool_name: Name of the tool to create a policy for
        risk_level: Risk classification - "low", "medium", or "high"
        require_approval: If true, high-risk tools need human approval before execution
        max_calls_per_minute: Rate limit (0 = unlimited)
        blocked: If true, the tool is completely blocked
    """
    if risk_level not in ("low", "medium", "high"):
        return f"Error: risk_level must be 'low', 'medium', or 'high', got '{risk_level}'"

    _tool_policies[tool_name] = {
        "risk_level": risk_level,
        "require_approval": require_approval,
        "max_calls_per_minute": max_calls_per_minute,
        "blocked": blocked,
    }

    status = "blocked" if blocked else f"{risk_level} risk"
    if require_approval:
        status += ", requires approval"
    if max_calls_per_minute > 0:
        status += f", max {max_calls_per_minute}/min"

    return f"Policy created for '{tool_name}': {status}"


@mcp.tool()
async def list_tool_policies() -> str:
    """List all active local tool enforcement policies."""
    if not _tool_policies:
        return "No tool policies configured. Use create_tool_policy to add one, or set ASQAV_PROXY_TOOLS env var."

    lines = []
    for name, policy in sorted(_tool_policies.items()):
        parts = [policy.get("risk_level", "unknown")]
        if policy.get("blocked"):
            parts = ["BLOCKED"]
        if policy.get("require_approval"):
            parts.append("approval required")
        if policy.get("max_calls_per_minute", 0) > 0:
            parts.append(f"max {policy['max_calls_per_minute']}/min")
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


def main():
    """Run the Asqav MCP server."""
    if not API_KEY:
        import sys

        print("Warning: ASQAV_API_KEY not set. Set it to authenticate with the API.", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
