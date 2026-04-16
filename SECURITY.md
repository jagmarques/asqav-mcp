# Security Policy

## Reporting Vulnerabilities

Email security@asqav.com with details. We will respond within 48 hours.

Do not open public issues for security vulnerabilities.

## Supported Versions

Only the latest published release is supported.

## Scope

This repository contains the asqav MCP server. It wraps the asqav-sdk and exposes audit tooling over the Model Context Protocol.

Report issues that affect:
- MCP tool definitions and request handling
- Authentication / token forwarding to the asqav API
- Input validation on incoming MCP calls

Cryptographic signing runs server-side via the asqav API. Report signing or key-handling issues against [asqav-sdk](https://github.com/jagmarques/asqav-sdk).
