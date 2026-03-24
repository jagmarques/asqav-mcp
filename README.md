<p align="center">
  <a href="https://asqav.com">
    <img src="https://asqav.com/logo-text-dark.png" alt="asqav" width="200">
  </a>
</p>
<p align="center">
  Governance for AI agents. Audit trails, policy enforcement, and compliance.
</p>

# Asqav MCP Server

MCP server for AI agent governance. Check policies, create audit trails, and verify compliance through any MCP client.

<p align="center">
  <a href="https://glama.ai/mcp/servers/jagmarques/asqav-mcp"><img src="https://glama.ai/mcp/servers/jagmarques/asqav-mcp/badges/card.svg" alt="asqav-mcp MCP server"></a>
</p>

## Tools

| Tool | Description |
|------|-------------|
| `check_policy` | Check if an action is allowed by policies |
| `sign_action` | Create a signed audit record for an action |
| `list_agents` | List registered agents |
| `get_agent` | Get agent details |
| `verify_signature` | Verify a signature |

## Setup

```bash
# Install
pip install -e .

# Set your API key
export ASQAV_API_KEY="sk_live_..."
export ASQAV_API_URL="https://api.asqav.com"  # optional, this is the default
```

## Usage with Claude Desktop

Add to `claude_desktop_config.json`:

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

## Usage with Claude Code

```bash
claude mcp add asqav -- asqav-mcp
```

## Development

```bash
cd mcp-server
uv venv && source .venv/bin/activate
uv pip install -e .
asqav-mcp
```