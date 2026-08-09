import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .admin_routes import router as admin_router
from .chat_routes import router as chat_router
from .database import init_db
from .whatsapp_routes import router as whatsapp_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("taxbot")

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="taxbot", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

app.include_router(whatsapp_router)
app.include_router(chat_router)
app.include_router(admin_router)


@app.exception_handler(Exception)
async def on_unhandled(request: Request, exc: Exception):
    """Any unhandled error → logged + a clean JSON response, never a raw 500/traceback.
    (FastAPI's own handler still returns intentional HTTPExceptions like 401/404 as-is.)"""
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500,
                        content={"reply": "Something went wrong on our side - please try again.",
                                 "done": False, "detail": "internal server error"})


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/chat")
async def chat_page(request: Request):
    return templates.TemplateResponse(request, "chat.html")


@app.get("/admin")
async def admin(request: Request):
    return templates.TemplateResponse(request, "admin.html")
