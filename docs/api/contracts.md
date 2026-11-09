# API and Integration Contracts

## EL_AI internal capability envelope (planned)

A future internal capability request should carry:
- contract version;
- request and correlation IDs;
- authenticated paired-client identity;
- explicit capability name;
- bounded typed input;
- idempotency key for side effects;
- deadline and resource budget.

The Hub must derive user ownership from the authenticated credential, never from a Telegram payload field.

## Hub API facts verified from current source

The Hub exposes owner session endpoints under `/auth`, task endpoints under `/tasks`, and dedicated Agent, approval and memory routes. Current `/auth/login` issues an owner session; it is not an EL_AI-scoped pairing mechanism. Task routes require a bearer-authenticated owner session and apply Hub policy classification. This is not sufficient for bot integration without a scoped credential boundary.

## Website API

MR_SIAVASH_AI Gateway public endpoints include public module catalog and grounded assistant fallback. The production Gateway currently has no Workers AI binding; public assistant responses must not be described as live model inference. EL_AI must use only public allowlisted routes and must not proxy private Hub requests through the site.

## Versioning

All cross-project contracts must be versioned, schema-validated and covered by positive and negative integration tests before UI copy claims the projects are connected.
