from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .admin_routes import router as admin_router
from .chat_routes import router as chat_router
from .database import init_db
from .whatsapp_routes import router as whatsapp_router

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


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/admin")
async def admin(request: Request):
    return templates.TemplateResponse(request, "admin.html")
