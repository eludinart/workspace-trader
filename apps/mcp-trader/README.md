# MCP trader

Stdio MCP server for Cursor. Each tool calls the engine control API over HTTPS:

`Authorization: Bearer $TRADER_CONTROL_TOKEN` and `X-Control-Actor: cursor`.

The token is read from the environment. Do not commit it, and do not paste it into a file in this repo.

## Environment

| Variable | Purpose |
| --- | --- |
| `TRADER_BASE_URL` | Engine origin plus proxy prefix, no trailing slash. Example: `https://trader.eludein.art/api/engine` |
| `TRADER_CONTROL_TOKEN` | Bearer token for `/control/*`. Same value as on the engine. |

`TRADER_BASE_URL` must be `https`. Tools append `/control/status`, `/control/params`, `/control/kill`, `/control/resume`, and `/control/tick`.

## Setup

Requires Node.js 22.18 or newer (`node` runs this TypeScript file directly).

```bash
cd apps/mcp-trader
npm install
```

## Cursor `mcp.json`

User config is `%USERPROFILE%\.cursor\mcp.json`. A project file is `.cursor/mcp.json`. Either one may be used. Do not commit a copy that inlines the token.

Set `TRADER_CONTROL_TOKEN` in the Windows user environment, then restart Cursor so the desktop process can see it. Cursor substitutes `${env:NAME}` in `command`, `args`, and `env`. The snippet below keeps only that reference.

`${workspaceFolder}` is the folder that contains the project `.cursor/mcp.json`. In the user-level file, use an absolute path instead.

```json
{
  "mcpServers": {
    "trader": {
      "type": "stdio",
      "command": "node",
      "args": ["${workspaceFolder}/apps/mcp-trader/src/index.ts"],
      "env": {
        "TRADER_BASE_URL": "https://trader.eludein.art/api/engine",
        "TRADER_CONTROL_TOKEN": "${env:TRADER_CONTROL_TOKEN}"
      }
    }
  }
}
```

`node` resolves `@modelcontextprotocol/sdk` from `apps/mcp-trader/node_modules`, so `npm install` has to have been run there.

## Tools

| Tool | HTTP | Effect |
| --- | --- | --- |
| `get_status` | `GET /control/status` | Paper mode, lease, kill flag, cash, positions |
| `get_params` | `GET /control/params` | Current bot params (secrets masked by the engine) |
| `set_params` | `PUT /control/params` | Partial update. Omitted fields stay unchanged. `watchlist` is an alias for `symbols` |
| `kill` | `POST /control/kill` | Paper kill switch. Also sets `auto_trade` to false. Optional `reason` |
| `resume` | `POST /control/resume` | Clears the kill flag. Does not turn `auto_trade` back on |
| `tick` | `POST /control/tick` | One forced paper decision. A 409 is expected while the API process does not hold the lease |

Non-2xx responses are returned as tool errors with `status` and the engine body (`401` unauthorized, `403` when mode is not paper, `422` invalid params, `409` tick refused).

This process does not open a database connection or a shell on the VPS.
