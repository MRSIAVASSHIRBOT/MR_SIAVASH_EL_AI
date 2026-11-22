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

- Runtime transport selection (`local_polling` or `cloudflare_bridge`).
- Telegram bot token presence and basic shape only for local polling (ASCII numeric bot ID, colon, and non-empty token part); the value is always redacted. Bridge mode correctly marks the bot token as not required.
- At least one positive numeric Telegram owner ID.
- Supported environment and boolean configuration values.
- Polling timeout (local mode only), request timeout, and rate-limit ranges.
- In bridge mode, the HTTPS bridge URL, minimum shared-secret length (without displaying the value), bridge timeout range, owner allowlist, and required Hub opt-in.
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
