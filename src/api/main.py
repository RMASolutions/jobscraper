from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from ..core.config import settings
from ..db.connection import init_db
from .routes import health, jobs

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info("Starting up...")

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    logger.info("Application started")

    yield

    # Cleanup
    logger.info("Shutting down...")

    # Close browser
    from ..browser.manager import _browser_manager
    if _browser_manager:
        await _browser_manager.stop()

    logger.info("Application stopped")


app = FastAPI(
    title="AI Workflow Engine",
    description="Workflow automation engine with AI agents",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, tags=["Health"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["Jobs"])
