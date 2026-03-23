"""
Dummy Service A — simulates a real backend service.

In a real deployment this would be a separate microservice (orders, users, etc.).
This exists purely to demonstrate that the gateway correctly proxies requests,
injects headers, and alternates between backends via round-robin.
"""

import random
import time

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI(title="Backend Service A", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": "A"}


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def catch_all(path: str, request: Request):
    # Simulate realistic variable latency
    await __import__("asyncio").sleep(random.uniform(0.01, 0.05))
    return {
        "service": "A",
        "path": f"/{path}",
        "method": request.method,
        "forwarded_user_id": request.headers.get("X-Gateway-User-Id"),
        "forwarded_role": request.headers.get("X-Gateway-User-Role"),
        "request_id": request.headers.get("X-Request-ID"),
        "forwarded_for": request.headers.get("X-Forwarded-For"),
        "timestamp": time.time(),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)