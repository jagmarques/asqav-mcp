<p align="center">
  <a href="https://asqav.com">
    <img src="https://asqav.com/logo-text-white.png" alt="asqav" width="200">
  </a>
</p>
<p align="center">
  Governance for AI agents. Audit trails, policy enforcement, and compliance.
</p>
<p align="center">
  <a href="https://pypi.org/project/asqav-mcp/"><img src="https://img.shields.io/pypi/v/asqav-mcp?style=flat-square&logo=pypi&logoColor=white" alt="PyPI version"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square&logo=opensourceinitiative&logoColor=white" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/pypi/pyversions/asqav-mcp?style=flat-square&logo=python&logoColor=white" alt="Python versions"></a>
  <a href="https://github.com/jagmarques/asqav-mcp"><img src="https://img.shields.io/github/stars/jagmarques/asqav-mcp?style=social" alt="GitHub stars"></a>
</p>
<p align="center">
  <a href="https://asqav.com">Website</a> |
  <a href="https://asqav.com/docs">Docs</a> |
  <a href="https://asqav.com/docs/sdk">SDK Guide</a> |
  <a href="https://asqav.com/compliance">Compliance</a>
</p>

# asqav MCP Server

MCP server that gives AI agents governance capabilities - policy checks, signed audit trails, and compliance verification. Plug it into Claude Desktop, Claude Code, Cursor, or any MCP client.

<p align="center">
  <a href="https://glama.ai/mcp/servers/jagmarques/asqav-mcp"><img src="https://glama.ai/mcp/servers/jagmarques/asqav-mcp/badges/card.svg" alt="asqav-mcp MCP server"></a>
</p>

## What is this?

AI agents act autonomously - calling APIs, reading data, making decisions. Without governance, there is no record of what happened and no way to enforce boundaries.

asqav-mcp exposes governance tools through the [Model Context Protocol](https://modelcontextprotocol.io/), so any MCP-compatible AI client can:

- **Enforce tool policies** with three-tier enforcement (strong, bounded, detectable)
- **Gate actions** before execution with signed approval/denial decisions
- **Check policies** before taking an action
- **Sign actions** with quantum-safe cryptography (ML-DSA, FIPS 204)
- **Verify audit trails** for any previous action
- **List and inspect agents** registered in your organization

All features are available on the free tier. All cryptography runs server-side. Zero native dependencies. Just `pip install` and connect.

## Quick start

```bash
pip install asqav-mcp
export ASQAV_API_KEY="sk_live_..."
asqav-mcp
```

Your MCP client now has access to policy enforcement, audit signing, and agent management tools.

## Works with

| Client | Setup |
|--------|-------|
| **Claude Desktop** | Add to `claude_desktop_config.json` ([see below](#claude-desktop)) |
| **Claude Code** | `claude mcp add asqav -- asqav-mcp` |
| **Cursor** | Add to MCP settings ([see below](#cursor)) |
| **Any MCP client** | Point to the `asqav-mcp` binary over stdio |

## Tools

### Governance

| Tool | What it does |
|------|-------------|
| `check_policy` | Check if an action is allowed by your organization's policies |
| `sign_action` | Create a quantum-safe signed audit record for an agent action |
| `verify_signature` | Verify a previously created signature |
| `list_agents` | List all registered AI agents |
| `get_agent` | Get details for a specific agent |

### Enforcement

| Tool | What it does |
|------|-------------|
| `gate_action` | Pre-execution enforcement gate. Checks policy, signs the decision, returns verdict. Bounded enforcement - the audit trail proves the check happened. |
| `enforced_tool_call` | Strong enforcement proxy. Checks policy, rate limits, and approval requirements before allowing a tool call. Fail-closed - if the check fails, the action is denied. |
| `create_tool_policy` | Create or update a local enforcement policy for a tool (risk level, rate limits, approval, blocking) |
| `list_tool_policies` | List all active tool enforcement policies |
| `delete_tool_policy` | Remove a tool enforcement policy |

## Setup

### Install

```bash
pip install asqav-mcp
```

Set your API key (get one free at [asqav.com](https://asqav.com)):

```bash
export ASQAV_API_KEY="sk_live_..."
```

### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "asqav": {
      "command": "asqav-mcp",
      "env": {
        "ASQAV_API_KEY": "sk_live_..."
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add asqav -- asqav-mcp
```

### Cursor

Add to your Cursor MCP settings:

```json
{
  "mcpServers": {
    "asqav": {
      "command": "asqav-mcp",
      "env": {
        "ASQAV_API_KEY": "sk_live_..."
      }
    }
  }
}
```

### Docker

```bash
docker build -t asqav-mcp .
docker run -e ASQAV_API_KEY="sk_live_..." asqav-mcp
```

## Why

| Without governance | With asqav |
|---|---|
| No record of what agents did | Every action signed with ML-DSA (FIPS 204) |
| Any agent can do anything | Policies block dangerous actions in real-time |
| Manual compliance reports | Automated EU AI Act and DORA reports |
| Breaks when quantum computers arrive | Quantum-safe from day one |

## Enforcement

asqav-mcp provides three tiers of enforcement:

**Strong** - `enforced_tool_call` acts as a non-bypassable proxy. The agent calls tools through the MCP server, which checks policy before allowing execution. The agent never has direct tool access.

**Bounded** - `gate_action` is a pre-execution gate. The agent calls it before any irreversible action. The audit trail proves whether the check happened, creating accountability even if the agent could theoretically skip the call.

**Detectable** - `sign_action` records what happened with cryptographic proof. If logs are tampered with or entries omitted, the hash chain breaks and verification fails.

### Tool policies

Control enforcement per tool using `create_tool_policy` or the `ASQAV_PROXY_TOOLS` env var:

```bash
export ASQAV_PROXY_TOOLS='{"sql:execute": {"risk_level": "high", "require_approval": true, "max_calls_per_minute": 5}, "file:delete": {"blocked": true}}'
```

Options per tool:
- `risk_level` - "low", "medium", or "high"
- `require_approval` - high-risk tools require human approval before execution
- `max_calls_per_minute` - rate limit (0 = unlimited)
- `blocked` - completely block a tool

### Example: enforced tool call

```
Agent: "Execute SQL query DROP TABLE users"

1. Agent calls enforced_tool_call(tool_name="sql:execute", agent_id="agent-1", arguments='{"query": "DROP TABLE users"}')
2. MCP server checks policy - sql:execute is high-risk, requires approval
3. Returns PENDING_APPROVAL with approval_id
4. Human approves or denies in the dashboard
5. Every step is signed into the tamper-evident audit trail
```

## Features

- **Strong enforcement** - tool proxy that checks policy before allowing execution
- **Bounded enforcement** - pre-execution gates with signed audit proof
- **Policy enforcement** - check actions against your org's rules before execution
- **Quantum-safe signatures** - ML-DSA-65 with RFC 3161 timestamps on every action
- **Tool policies** - per-tool risk levels, rate limits, approval requirements, blocking
- **Fail-closed** - if enforcement checks fail, actions are denied by default
- **Agent management** - list, inspect, and monitor registered agents
- **Signature verification** - verify any audit record's authenticity
- **Zero dependencies** - no native crypto libraries needed, all server-side
- **Stdio transport** - works with any MCP client over standard I/O

## Ecosystem

| Package | What it does |
|---------|-------------|
| [asqav](https://github.com/jagmarques/asqav-sdk) | Python SDK - decorators, async, framework integrations |
| **asqav-mcp** | MCP server for Claude Desktop, Claude Code, Cursor |
| [asqav-compliance](https://github.com/jagmarques/asqav-compliance) | CI/CD compliance scanner for pipelines |

## Development

```bash
git clone https://github.com/jagmarques/asqav-mcp.git
cd asqav-mcp
uv venv && source .venv/bin/activate
uv pip install -e .
asqav-mcp
```

## Contributing

Contributions welcome. Check the [issues](https://github.com/jagmarques/asqav-mcp/issues) for good first issues.

## License

MIT - see [LICENSE](LICENSE) for details.

---

If asqav-mcp helps you, consider giving it a star. It helps others find the project.

<!-- mcp-name: io.github.jagmarques/asqav-mcp -->
