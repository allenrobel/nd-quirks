# nd-quicks MCP Server

## Summary

An MCP Server that leverages notes written in Markdown.  The server is tailored for those
developing Cisco Nexus Dashboard applications which use the REST API, but could easily be
leveraged for other uses.

My use case is providing Claude Code with a resource through which it can determine the
suitability of Nexus Dashboard endpoints for a given task, and whether an endpoint exhibits
any behavioral quirks and, if so, what version(s) exhibit the behavior and what
version (if any) fixes the behavior.  Notes might also contain workaround(s).

**The actual notes are not included in this repository.**

## Setup

### 1. Install Obsidian and login

### 2. Setup sync with the ND Vault (vault should be in `$HOME/Obsidian/ND`)

### 3. Edit `com.nd-quirks-mcp.plist` such that the paths match your environment

- `OBSIDIAN_VAULT_PATH` should point to your ND value (e.g. `$HOME/Obsidian/ND`)
- `ProgramArguments` should call `uv server.py` via their full paths e.g.
  - `/Users/arobel/repos/mcp/nd-quirks/.venv/bin/uv`
  - run
  - `/Users/arobel/repos/mcp/nd-quirks/server.py`
- `PYTHONPATH` should point to the .venv python e.g.
  - `/Users/arobel/repos/nd-quirks/.venv/lib/python3.14/site-packages`

```bash
cd $HOME/repos/mcp/nd-quirks
vi com.nd-quirks-mcp.plist
cp com.nd-quirks-mcp.plist $HOME/Library/LaunchAgents
chmod 644 $HOME/Library/LaunchAgents/com.nd-quirks-mcp.plist
```

### 4. Edit Claude Code's config on the client Mac to point to this MCP server

- edit $HOME/.claude.json
- Search for the `mcpServers` block
- Add the following (where `mm1e` is the hostname or IP address of the Mac that's hosting the MCP server)

```json
  "mcpServers": {
    "nd-quirks": {
      "type": "http",
      "url": "http://mm1e:8001/mcp"
    }
  }
```

### 5. Restart Claude Code and check the MCP server status using the `/mcp` slash command
