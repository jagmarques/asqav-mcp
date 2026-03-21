"""Asqav MCP Server - AI agent governance tools for MCP clients."""

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("asqav")

API_URL = os.environ.get("ASQAV_API_URL", "https://api.asqav.com")
API_KEY = os.environ.get("ASQAV_API_KEY", "")


async def _request(method: str, path: str, json: dict | None = None) -> dict[str, Any]:
    """Make an authenticated request to the Asqav API."""
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method, f"{API_URL}/api/v1{path}", headers=headers, json=json, timeout=30.0
        )
        response.raise_for_status()
        return response.json()


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
        import json

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

        result = await _request("POST", "/sign", json=body)
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
        import json

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


def main():
    """Run the Asqav MCP server."""
    if not API_KEY:
        import sys

        print("Warning: ASQAV_API_KEY not set. Set it to authenticate with the API.", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
