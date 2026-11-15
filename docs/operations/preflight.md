# EL_AI local preflight

The `el-ai doctor` command performs a read-only, offline preflight of local EL_AI configuration before the Telegram runtime is started.

## Usage

```bash
el-ai doctor
el-ai doctor --json
python -m el_ai doctor
```

- Exit code `0`: required local configuration checks pass.
- Exit code `1`: one or more blocking checks need attention.
- JSON mode emits a machine-readable report suitable for local diagnostics.

## What it checks

- Telegram bot token presence and basic shape only (ASCII numeric bot ID, colon, and non-empty token part); the value is always redacted.
- At least one positive numeric Telegram owner ID.
- Supported environment and boolean configuration values.
- Polling timeout, request timeout, and rate-limit ranges.
- Public website and Gateway base URL syntax and HTTPS policy.
- Hub integration opt-in, Hub URL loopback/HTTPS policy, and trusted OS keyring availability when Hub integration is enabled.
- Whether the configured state directory exists and is locally accessible; it does not create the directory.

## Safety properties

The doctor:

- makes **zero network requests**;
- performs **zero filesystem writes**;
- never prints the Telegram bot token, pairing codes, access tokens, refresh tokens, or stored credential contents;
- does not read saved Hub credentials;
- checks keyring backend availability only when Hub integration is enabled;
- does not start polling, send Telegram messages, create pairing challenges, or modify Cloudflare resources.

A successful report is a local configuration preflight, not proof that the Telegram token is valid, Telegram is reachable, Hub is healthy, or a production deployment exists. Use the Hub health command and controlled integration tests for runtime verification after the relevant services are configured.
