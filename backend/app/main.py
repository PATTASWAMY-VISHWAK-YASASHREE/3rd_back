from contextlib import asynccontextmanager
from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.store.database import init_db
from app.api import routes_generate, routes_tests, routes_export, routes_runner
from app.api.routes_github import auth_router, github_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create DB tables
    await init_db()
    yield
    # Shutdown: nothing needed for SQLite


settings = get_settings()

app = FastAPI(
    title="TestGen AI",
    description="AI-powered test case generator from user stories",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes — order matters: export route must come before
# the generic /{suite_id} route so /export/ paths match first
app.include_router(routes_export.router)
app.include_router(routes_generate.router)
app.include_router(routes_tests.router)
app.include_router(routes_runner.router)
app.include_router(auth_router)
app.include_router(github_router)


@app.get("/", include_in_schema=False)
async def root():
    return {"status": "ok", "health": "/health"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
async def chrome_devtools_probe():
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/health")
async def health():
    return {"status": "ok", "model": settings.gemini_model}
