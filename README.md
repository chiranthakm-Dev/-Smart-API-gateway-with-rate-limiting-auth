# API Gateway

> Production-grade API gateway with JWT authentication, token bucket rate limiting, round-robin load balancing, and graceful Redis degradation.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Redis](https://img.shields.io/badge/Redis-7.x-DC382D?style=flat&logo=redis&logoColor=white)](https://redis.io)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?style=flat&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat&logo=docker&logoColor=white)](https://docker.com)
[![Tests](https://img.shields.io/badge/Tests-14%20passing-22C55E?style=flat)](#testing)

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Features](#features)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Rate Limiting](#rate-limiting)
- [Graceful Degradation](#graceful-degradation)
- [Load Balancing](#load-balancing)
- [Testing](#testing)
- [Deployment](#deployment)
- [Project Structure](#project-structure)
- [Roadmap](#roadmap)

---

## Overview

This gateway sits in front of any collection of backend services and enforces authentication, rate limiting, load balancing, and observability before a single byte reaches an upstream.

```
Client → [API Gateway] → Service A
                      ↘ Service B
          ↕                ↕
        Redis          PostgreSQL
   (rate limiting)   (users + logs)
```

**Why this exists:** Without a gateway layer, every microservice independently implements auth, rate limiting, and logging — duplicating logic and creating inconsistency. A single misconfigured client can exhaust resources for all users. Observability is fragmented with no unified request trace.

---

## Architecture

### Request Flow

Every proxied request passes through this sequence:

```
1. add_request_id middleware     →  Assign / echo X-Request-ID
2. RequestLoggingMiddleware      →  Record start timestamp
3. get_current_user dependency   →  Decode JWT → load user from PostgreSQL
4. check_rate_limit              →  Token bucket check via Redis Lua script
5. get_load_balancer().get_next()→  Round-robin select healthy backend
6. httpx proxy                   →  Forward request with injected headers
7. Attach X-RateLimit-* headers  →  Rate limit metadata on response
8. asyncio.create_task()         →  Fire-and-forget DB log write
9. Return response to client
```

### Components

| Component | Role |
|-----------|------|
| **FastAPI Gateway** | Core process — auth middleware, rate limiter, proxy router, admin endpoints |
| **Redis 7** | Token bucket state store — primary rate limit backend |
| **PostgreSQL 15** | User accounts + request audit log |
| **Service A / B** | Dummy FastAPI backends — accept all routes, return service identity + forwarded headers |
| **Docker Compose** | Local orchestration — one command starts all 5 services |

---

## Features

- **JWT Authentication** — HS256-signed tokens with configurable expiry. Every `/proxy/**` request requires a valid Bearer token. Expired and tampered tokens are rejected with 401.
- **Token Bucket Rate Limiting** — Per-user, backed by Redis via atomic Lua script. Prevents burst attacks that fixed-window counters allow.
- **Graceful Degradation** — If Redis goes down, the gateway switches to in-memory rate limiting automatically. No crashes, no dropped requests. See [Graceful Degradation](#graceful-degradation).
- **Round-Robin Load Balancing** — Distributes traffic evenly across registered backends. Unhealthy backends are removed from the pool after 3 consecutive failures and re-admitted after a successful health check.
- **Structured Request Logging** — Every proxied request is persisted to PostgreSQL with `request_id`, `user_id`, `duration_ms`, and `rate_limit_backend`. Async write — zero added latency.
- **Full OpenAPI Spec** — Auto-generated at `/docs` (Swagger UI), `/redoc`, and `/openapi.json`.
- **Admin Endpoints** — Query request logs, view aggregate stats, and check backend health. Admin-role JWT required.

---

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- That's it.

### 1. Clone and configure

```bash
git clone https://github.com/your-username/api-gateway.git
cd api-gateway
cp .env.example .env
```

Edit `.env` — at minimum, set a real `SECRET_KEY`:

```bash
SECRET_KEY=$(openssl rand -hex 32)
```

### 2. Start all services

```bash
docker compose up --build
```

This starts:
- `gateway` on [http://localhost:8000](http://localhost:8000)
- `service_a` on [http://localhost:8001](http://localhost:8001)
- `service_b` on [http://localhost:8002](http://localhost:8002)
- `redis` on port 6379
- `postgres` on port 5432

### 3. Register a user and get a token

```bash
# Register
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "securepass"}'

# Login
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "securepass"}'
```

Copy the `access_token` from the login response.

### 4. Proxy a request

```bash
curl http://localhost:8000/proxy/api/hello \
  -H "Authorization: Bearer <your_token>"
```

The response will identify which backend (`service_a` or `service_b`) handled the request. Alternate requests go to alternating backends.

### 5. Explore the docs

Open [http://localhost:8000/docs](http://localhost:8000/docs) for the interactive Swagger UI.

---

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` for local development.

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | `change-me` | JWT signing key. **Required in production.** Generate: `openssl rand -hex 32` |
| `DATABASE_URL` | `postgresql+asyncpg://gateway:gateway@db:5432/gateway` | Async PostgreSQL DSN |
| `REDIS_HOST` | `redis` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_PASSWORD` | _(empty)_ | Redis password (leave empty if none) |
| `REDIS_CONNECT_TIMEOUT_MS` | `200` | Max ms to wait for Redis before marking it down |
| `BACKEND_SERVICES` | `["http://service_a:8001","http://service_b:8002"]` | JSON array of backend URLs |
| `RATE_LIMIT_CAPACITY` | `100` | Max tokens in each user's bucket |
| `RATE_LIMIT_REFILL_RATE` | `10.0` | Tokens added per second |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | JWT lifetime in minutes |
| `CORS_ORIGINS` | `["*"]` | Allowed CORS origins — **restrict in production** |
| `DEBUG` | `false` | Enables SQLAlchemy query logging |
| `ENV` | `development` | Environment name (`development` / `production`) |

---

## API Reference

Full interactive spec at `/docs`. Summary:

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Gateway + Redis + backend health |
| `POST` | `/auth/register` | None | Create a new user account |
| `POST` | `/auth/login` | None | Authenticate and receive a JWT |
| `GET` | `/auth/me` | Bearer JWT | Current user profile |
| `ANY` | `/proxy/{path}` | Bearer JWT | Proxy to backend with rate limiting |
| `GET` | `/admin/logs` | Admin JWT | Query request audit log |
| `GET` | `/admin/stats` | Admin JWT | Aggregate metrics + LB status |
| `GET` | `/docs` | None | Swagger UI |
| `GET` | `/redoc` | None | ReDoc |
| `GET` | `/openapi.json` | None | Raw OpenAPI 3.1 spec |

### Response Headers (all proxied requests)

```
X-Request-ID: <uuid>              # Trace ID — correlate with upstream logs
X-Response-Time-Ms: 4.23          # Total gateway latency
X-RateLimit-Limit: 100            # Bucket capacity
X-RateLimit-Remaining: 94         # Tokens left after this request
X-RateLimit-Backend: redis        # "redis" or "in-memory" (degradation indicator)
```

### Error Responses

All errors return JSON:

```json
{
  "detail": "Invalid or expired token",
  "request_id": "3f7a1b2c-..."
}
```

| Status | Cause |
|--------|-------|
| `401` | Missing, invalid, or expired JWT |
| `403` | Valid JWT but insufficient role (e.g. non-admin hitting `/admin/*`) |
| `429` | Rate limit exceeded — includes `Retry-After` header |
| `502` | Backend connection refused or DNS failure |
| `503` | All backends unhealthy |
| `504` | Backend request timed out (>30s) |

---

## Rate Limiting

### Algorithm: Token Bucket

Each authenticated user has a bucket with `RATE_LIMIT_CAPACITY` tokens. Tokens refill at `RATE_LIMIT_REFILL_RATE` per second. Each request consumes one token. An empty bucket returns `429 Too Many Requests`.

**Why token bucket over fixed window?**

Fixed window has a burst problem: a user can fire 100 requests at `00:59` and 100 more at `01:00` — 200 requests in 2 seconds, straddling the window boundary. Token bucket prevents this by smoothing the refill rate.

### Redis Implementation

Bucket state (`token_count`, `last_refill_timestamp`) is stored as a Redis hash keyed by `rate_limit:{user_id}`. A Lua script performs the read-compute-write atomically:

```
Why Lua? Without atomic execution, two concurrent requests can both read
"1 token remaining", both pass, and the bucket goes to -1. The Lua script
runs as a single Redis command — no race condition possible.
```

### 429 Response

```json
{
  "detail": {
    "error": "Rate limit exceeded",
    "retry_after_seconds": 4,
    "limit": 100
  }
}
```

With headers:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 0
X-RateLimit-Backend: redis
Retry-After: 4
```

---

## Graceful Degradation

> **The detail that wins interviews.**

When Redis is unreachable, the gateway does not crash or reject all requests. It transparently switches to per-process in-memory token buckets.

### How it works

```
Request arrives
      │
      ▼
  get_redis_client()
      │
      ├─ Redis reachable? ──YES──▶ Run Lua script ──▶ Allow / Deny
      │
      └─ Redis down? ──────────▶ _check_in_memory() ──▶ Allow / Deny
                                        │
                               X-RateLimit-Backend: in-memory
```

On every request, the gateway pings Redis before use. If the ping fails, `mark_redis_failed()` is called and the in-memory path is used for all subsequent requests. Reconnection is attempted automatically before each rate limit check — recovery is seamless when Redis comes back.

### The tradeoff (documented, not hidden)

During Redis downtime, limits are per-process, not global. With N gateway instances, the effective rate limit becomes N × `RATE_LIMIT_CAPACITY`. This is disclosed in:

- The `/health` endpoint (`rate_limiting_backend: "in-memory"`)
- Every response header (`X-RateLimit-Backend: in-memory`)
- Structured logs (`redis.marked_failed` event)

### Why not fail-open or fail-closed?

| Strategy | Behaviour | Why rejected |
|----------|-----------|--------------|
| **Fail-open** | No rate limiting during outage | An outage could be intentionally triggered to bypass limits |
| **Fail-closed** | Reject all requests during outage | Availability > perfect enforcement for short outages |
| **Graceful degradation** ✅ | Per-process limiting | Best balance — service stays up, limits still enforced |

---

## Load Balancing

### Strategy: Round-Robin

Requests are distributed evenly across all healthy backends. O(1), fair, suitable for homogeneous backends.

### Health Tracking

A backend is marked **unhealthy** after 3 consecutive failures (connection error or timeout). It is automatically re-checked every 15 seconds and re-admitted to the pool on success.

```
backend failure count >= 3  →  removed from healthy pool
background task (15s poll)  →  health check via GET /health
successful check            →  re-added to pool, failure count reset
```

**Fallback:** If all backends are simultaneously unhealthy, the gateway falls back to trying the full list rather than returning `503` to every request — better to attempt than to give up.

### Headers Forwarded to Backends

```
X-Forwarded-For: <client_ip>
X-Forwarded-Proto: https
X-Gateway-User-Id: <uuid>
X-Gateway-User-Role: user | admin
X-Request-ID: <uuid>
```

Hop-by-hop headers (`Connection`, `Transfer-Encoding`, `Keep-Alive`, etc.) are stripped before forwarding.

---

## Testing

### Run all tests

```bash
# With Docker (recommended — no local setup needed)
docker compose run --rm gateway pytest tests/ -v --tb=short

# Locally (requires Python 3.11+ and dev dependencies)
pip install -r requirements-dev.txt
pytest tests/ -v
```

### Test matrix

| ID | Test | Layer |
|----|------|-------|
| TC-01 | Register new user | Integration |
| TC-02 | Register duplicate email → 409 | Integration |
| TC-03 | Login valid credentials → JWT | Integration |
| TC-04 | Login invalid password → 401 | Integration |
| TC-05 | Proxy with no token → 401 | Integration |
| TC-06 | Proxy with expired token → 401 | Integration |
| TC-07 | Rate limit — 100 pass, 101st → 429 | Unit |
| TC-08 | Rate limit — token refill after sleep | Unit |
| TC-09 | Rate limit — Redis error → in-memory fallback | Unit |
| TC-10 | Load balancer — round-robin distribution | Unit |
| TC-11 | Load balancer — skip unhealthy backend | Unit |
| TC-12 | Proxy — forwarded headers present upstream | Integration |
| TC-13 | Admin logs — non-admin → 403 | Integration |
| TC-14 | Health endpoint — reports in-memory backend | Integration |

### Test infrastructure

- **Database:** In-memory SQLite (`sqlite+aiosqlite:///:memory:`) — no external DB required.
- **Redis:** Mocked via `unittest.mock.patch`. Tests explicitly control whether Redis is available or raises an exception to exercise the fallback path.
- **Isolation:** Each test gets a fresh DB state via session-scoped fixtures with per-test rollback.
- **Async:** `pytest-asyncio` with `asyncio_mode=auto` handles all async test functions.

---

## Deployment

### Railway

1. Push to a GitHub repository.
2. Create a new Railway project → **Deploy from GitHub repo**.
3. Add the **Redis** plugin — `REDIS_URL` is injected automatically.
4. Add the **PostgreSQL** plugin — `DATABASE_URL` is injected automatically.
5. Set `SECRET_KEY` as a secret environment variable:
   ```bash
   railway variables set SECRET_KEY=$(openssl rand -hex 32)
   ```
6. Set `BACKEND_SERVICES` to your production backend URLs.
7. Railway auto-detects the `Dockerfile` and deploys on every push.

### Fly.io

```bash
# Install flyctl and authenticate
fly auth login

# Launch (generates fly.toml)
fly launch

# Create managed Redis
fly redis create
fly secrets set REDIS_URL=<connection_string_from_above>

# Create managed Postgres
fly postgres create
fly postgres attach <app_name>

# Set secrets
fly secrets set SECRET_KEY=$(openssl rand -hex 32)

# Deploy
fly deploy
```

### Production checklist

- [ ] `SECRET_KEY` is a 32-byte random hex string (`openssl rand -hex 32`)
- [ ] `CORS_ORIGINS` is set to your actual frontend domain(s), not `*`
- [ ] `DEBUG=false`
- [ ] `ENV=production`
- [ ] PostgreSQL and Redis are on managed services (not containers)
- [ ] TLS is terminated at the ingress layer (Railway/Fly handle this)
- [ ] `RATE_LIMIT_CAPACITY` and `RATE_LIMIT_REFILL_RATE` tuned for your traffic profile

---

## Project Structure

```
api-gateway/
├── app/
│   ├── main.py                  # FastAPI app, middleware, router registration
│   ├── core/
│   │   ├── config.py            # Pydantic Settings — all env vars
│   │   ├── database.py          # Async SQLAlchemy engine + session
│   │   ├── redis_client.py      # Redis connection + failure tracking
│   │   └── security.py          # JWT encode/decode, auth dependencies
│   ├── middleware/
│   │   └── logging.py           # Structured request logging middleware
│   ├── models/
│   │   ├── user.py              # SQLAlchemy User model
│   │   └── request_log.py       # SQLAlchemy RequestLog model
│   ├── routers/
│   │   ├── auth.py              # POST /auth/register, /auth/login, /auth/me
│   │   ├── gateway.py           # ANY /proxy/{path} — core proxy logic
│   │   ├── health.py            # GET /health
│   │   └── admin.py             # GET /admin/logs, /admin/stats
│   └── services/
│       ├── rate_limiter.py      # Token bucket — Redis + in-memory fallback
│       ├── load_balancer.py     # Round-robin LB with health tracking
│       └── user_service.py      # User CRUD, password hashing
├── tests/
│   ├── conftest.py              # Fixtures — in-memory DB, mocked Redis, test client
│   ├── unit/
│   │   ├── test_rate_limiter.py # Token bucket logic, fallback behaviour
│   │   └── test_load_balancer.py# Round-robin, health tracking
│   └── integration/
│       ├── test_auth.py         # Register, login, token validation
│       ├── test_proxy.py        # End-to-end proxy, header forwarding
│       └── test_admin.py        # Admin endpoints, role enforcement
├── dummy_services/
│   ├── service_a/main.py        # Dummy backend A
│   └── service_b/main.py        # Dummy backend B
├── docker-compose.yml           # Full local stack
├── Dockerfile                   # Gateway container
├── fly.toml                     # Fly.io deployment config
├── .env.example                 # Environment variable reference
├── requirements.txt             # Runtime dependencies
├── requirements-dev.txt         # Test + dev dependencies
└── README.md
```

---

## Roadmap

| Priority | Feature | Version | Rationale |
|----------|---------|---------|-----------|
| P1 | JWT token revocation (JTI blocklist in Redis) | 1.1 | Logout support; blocks compromised tokens before natural expiry |
| P1 | RS256 / asymmetric JWT signing | 1.1 | Multiple services verify tokens without sharing the signing secret |
| P1 | Circuit breaker for backends | 1.1 | Faster failure detection; half-open state for gradual recovery |
| P2 | Per-user rate limit overrides via admin API | 1.2 | Different tiers (free / pro) without redeployment |
| P2 | Prometheus `/metrics` endpoint | 1.2 | Scrape into Grafana; alert on p99 latency and 429 rate |
| P2 | Request/response schema validation | 1.2 | Return 422 before hitting upstream on malformed payloads |
| P3 | Weighted round-robin load balancing | 2.0 | Route proportionally more traffic to higher-spec backends |
| P3 | OAuth 2.0 / OIDC integration | 2.0 | Accept tokens from Google, Auth0, Keycloak |
| P3 | Redis Cluster support | 2.0 | Horizontal scaling of rate limit state |

---

## Design Decisions

### Why FastAPI over Flask?

FastAPI has native async support, automatic OpenAPI generation, and Pydantic-based validation. For a gateway that is entirely I/O bound — waiting on Redis, PostgreSQL, and upstream services — async means one worker process handles many concurrent requests without thread overhead.

### Why async SQLAlchemy?

The gateway would be wasteful with synchronous SQLAlchemy. Every DB operation (loading a user on auth, writing a request log) would block the event loop for the duration of the network round-trip to PostgreSQL. Async lets those waits yield back to the event loop so other requests can be served concurrently.

### Why fire-and-forget for request logging?

The client should not wait for a PostgreSQL write to complete before receiving the proxied response. Using `asyncio.create_task()` for the log write means the response is returned immediately. The accepted risk: a log entry may be lost if the process crashes between response send and DB commit. For audit-critical systems, a write-ahead log or message queue (Kafka, SQS) would be more appropriate.

### Why Lua for Redis rate limiting?

Without an atomic read-modify-write, two concurrent requests can both observe "1 token remaining", both pass, and the bucket is over-decremented. The Lua script runs as a single Redis command — the Redis server serialises Lua execution, so no two scripts run concurrently for the same key.

---

## License

MIT
