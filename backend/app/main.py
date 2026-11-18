from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api.internal import router as internal_router
from app.api.routes import router as public_router
from app.database import engine, init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB tables on startup
    await init_db()
    yield
    # Cleanup DB connection pool on shutdown
    await engine.dispose()

app = FastAPI(
    title="Kiwi Music API",
    version="1.0.0",
    description="Independent high-speed music search, Yukisbox storage cache, and delivery service.",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom HTTP Exception Handler to enforce uniform error schema
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        code = exc.detail.get("code", "ERROR")
        message = exc.detail.get("message", "An error occurred")
    else:
        code = "HTTP_ERROR"
        message = str(exc.detail)

    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": message}}
    )

# Generic Exception Handler
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_SERVER_ERROR", "message": str(exc)}}
    )

# Include API Routers
app.include_router(public_router)
app.include_router(internal_router)

@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def root():
    return {
        "service": "Kiwi Music API",
        "version": "1.0.0",
        "docs": "/docs",
        "status": "operational"
    }

