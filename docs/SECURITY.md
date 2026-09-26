# BestNAI Security Guide

## 1. Security goals

BestNAI is designed for internal or controlled deployments, but it still needs
basic operational protections.

The default posture is:

- auth enabled by default in code
- CORS configurable
- admin endpoints separated
- upstream key usage isolated from the frontend

## 2. Authentication model

### Client auth
Regular requests may use a client token:

```http
Authorization: Bearer <client_secret>
```

### Admin auth
Admin endpoints use a separate admin token.

### BYOK
BYOK mode accepts a per-request NovelAI key via:

```http
X-NAI-Key: pst-...
```

This key is used only for the specific request and is not stored server-side.

## 3. What must never happen

- never expose admin secret to the browser
- never hardcode upstream keys into frontend code
- never assume client-side validation is enough
- never allow unrestricted public deployment without rate limiting

## 4. Upload security

Recommended controls:

- file size limit
- MIME / content validation
- image decode validation
- request body size limit
- upload count limit

## 5. Request and traffic controls

Recommended additions for production:

- per-IP rate limiting
- per-token rate limiting
- request ID / audit log
- queue length alarm
- request timeout monitoring
- max payload size middleware

## 6. CORS

CORS should be configured through environment variables.

For internal use, a permissive configuration may be fine.
For public deployment, use a strict origin allowlist.

## 7. Queue and key pool safety

The scheduler is designed to avoid concurrent use of the same upstream key.

This is important because:

- it reduces upstream key collisions
- it makes request latency more predictable
- it keeps failed requests isolated to a single lease

## 8. BYOK safety notes

- BYOK requests bypass the scheduler's upstream-key pool
- the supplied key is used in a temporary client instance
- the gateway still performs its own auth and request validation

## 9. Recommended deployment profile

For a real deployment, enable:

- HTTPS behind a reverse proxy
- strict CORS
- auth required
- rate limiting
- request size limits
- logging

## 10. Operational note

BestNAI is currently most suitable for:

- personal use
- LAN / intranet deployments
- controlled team environments

It can be hardened for public use, but that should be done intentionally.
