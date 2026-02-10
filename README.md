# Nexus

Nexus is a WhatsApp-first AI assistant with a Python core and a Node bridge.

## Install

### From source (development)

```bash
git clone https://github.com/liamdatt/NEXUS.git
cd NEXUS
pip install -e .
```

### With uv tool (global install)

```bash
uv tool install flopro-nexus
```

### From PyPI

```bash
pip install flopro-nexus
```

## Quick Start

Run onboarding once:

```bash
nexus onboard
```

Then pair and start:

```bash
nexus whatsapp connect
nexus start
```

Onboarding performs:

- config/data directory setup in your user profile
- bridge runtime preparation
- `npm install` for bridge dependencies
- global Nexus config file creation at `~/.config/nexus/.env` (platform-dependent path)

## CLI Commands

```bash
nexus onboard [--non-interactive] [--yes]
nexus doctor
nexus start
nexus tui
nexus whatsapp connect [--timeout 180] [--exit-delay-ms 30000] [--session-dir PATH]
nexus whatsapp disconnect [--session-dir PATH] [--yes]
nexus whatsapp status [--session-dir PATH]
nexus auth google connect
nexus auth google status
nexus auth google disconnect
```

## Configuration Model

Environment precedence:

1. OS environment variables
2. `.env` in current working directory
3. global config file (`~/.config/nexus/.env` by default)
4. built-in defaults

Supported path control env keys:

- `NEXUS_CONFIG_DIR`
- `NEXUS_DATA_DIR`
- `NEXUS_BRIDGE_DIR`

## Runtime Defaults

If not overridden, Nexus uses user-local directories:

- `db_path`: `<data_dir>/nexus.db`
- `workspace`: `<data_dir>/workspace`
- `memories_dir`: `<data_dir>/memories`
- Google OAuth secret: `<config_dir>/google/client_secret.json`
- Google OAuth token: `<config_dir>/google/token.json`

## TUI

Launch the operator console:

```bash
nexus tui
```

TUI supports:

- start/stop stack
- local chat
- WhatsApp connect/status/disconnect
- Google auth connect/status/disconnect
- config key editing persisted to global `.env`

## Troubleshooting

### `nexus: command not found`

If installed in a virtualenv, activate it first:

```bash
source .venv/bin/activate
```

Or run diagnostics directly through Python:

```bash
python -m nexus.cli_app doctor
```

### Missing `npm` / Node

Install Node.js and verify:

```bash
node -v
npm -v
```

Then rerun:

```bash
nexus onboard
```

### Bridge bootstrap/runtime issues

```bash
nexus doctor
```

If bridge runtime is not ready, rerun:

```bash
nexus onboard
```

### Google OAuth issues

- Ensure Gmail API and Google Calendar API are enabled in Google Cloud.
- Confirm `NEXUS_GOOGLE_CLIENT_SECRET_PATH` points to the OAuth client JSON.
- Reconnect if token is invalid:

```bash
nexus auth google disconnect
nexus auth google connect
```
