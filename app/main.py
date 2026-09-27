"""
Main FastAPI application entry point.
"""

# Load .env file for local development (no-op if file absent or dotenv not installed)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import os
import time
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.llm.providers import create_llm_provider
from app.services.composer import ComposerService

# Global start time for uptime reporting
_start_time = time.time()

# Shared application state (thread-safe via GIL for simple types)
_app_state: dict = {
    "composer": None,
    "suppression_set": set(),
}
_state_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize LLM provider on startup."""
    global _app_state

    # Initialize LLM provider
    llm = create_llm_provider()
    composer = ComposerService(llm=llm)

    with _state_lock:
        _app_state["composer"] = composer
        _app_state["suppression_set"] = set()

    if llm:
        provider_name = llm.name()
        print(f"[Vera] LLM provider initialized: {provider_name}")
    else:
        print("[Vera] WARNING: No LLM API key configured. Using deterministic fallback only.")

    yield

    # Cleanup on shutdown
    print("[Vera] Shutting down.")


# Create app
app = FastAPI(
    title="Vera — magicpin Merchant AI Assistant",
    description="AI bot for the magicpin AI Challenge: Build a Merchant AI Assistant (Vera)",
    version=os.getenv("APP_VERSION", "1.0.0"),
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all routes under /v1
app.include_router(router, prefix="/v1")


@app.get("/")
async def root():
    return {
        "service": "Vera — magicpin Merchant AI Assistant",
        "version": os.getenv("APP_VERSION", "1.0.0"),
        "endpoints": ["/v1/healthz", "/v1/metadata", "/v1/context", "/v1/tick", "/v1/reply"],
    }
