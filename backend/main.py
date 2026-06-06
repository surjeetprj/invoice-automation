"""
Invoice Automation — FastAPI Application Entrypoint

This is a clean bootstrapper file that registers middleware, database lifespans,
and includes modular API routers for stats and invoices.
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from database import init_db
from services.doc_extraction_engine import initialize_parser
from routers.invoices import router as invoices_router
from routers.stats import router as stats_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress /health endpoint logs in terminal
class HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()

logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())


# ── Lifespan ──────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting Invoice Automation…")
    await init_db()
    logger.info("✅ Database initialised.")
    initialize_parser()
    yield
    logger.info("👋 Shutting down.")


app = FastAPI(
    title="Invoice Automation",
    description="Invoice processing: pdfplumber → AI Parse → Validate → HITL Review → Export",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(invoices_router)
app.include_router(stats_router)


@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/docs")
