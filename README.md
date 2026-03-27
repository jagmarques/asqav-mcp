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
  <a href="https://github.com/jagmarques/asqav-mcp"><img src="https://img.shields.io/github/stars/jagmarques/asqav-mcp?style=social" alt="GitHub stars"></a>
</p>
<p align="center">
  <a href="https://asqav.com">Website</a> |
  <a href="https://asqav.com/docs">Docs</a> |
  <a href="https://asqav.com/docs/sdk">SDK Guide</a> |
  <a href="https://asqav.com/compliance">Compliance</a>
</p>

# MCP Server

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