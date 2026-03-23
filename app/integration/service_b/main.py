"""
Dummy Service B — mirrors Service A but identifies itself as "B".
Slightly higher simulated latency to show that round-robin handles
backends with different response times gracefully.
"""

import random
import time

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI(title="Backend Service B", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": "B"}


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def catch_all(path: str, request: Request):
    await __import__("asyncio").sleep(random.uniform(0.02, 0.08))
    return {
        "service": "B",
        "path": f"/{path}",
        "method": request.method,
        "forwarded_user_id": request.headers.get("X-Gateway-User-Id"),
        "forwarded_role": request.headers.get("X-Gateway-User-Role"),
        "request_id": request.headers.get("X-Request-ID"),
        "forwarded_for": request.headers.get("X-Forwarded-For"),
        "timestamp": time.time(),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)