from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import psycopg2
import logging
import os
from auth_google import router as google_router
from datos_usuario import router as datos_usuario_router
from publicaciones import router as publicaciones_router
from auth_email import email_router
from chats import router as chats_router
from resenas import router as resenas_router
from apple_auth import router as apple_router
from pydantic import BaseModel
from typing import Optional
import firebase_admin
from firebase_admin import credentials

# --- Configurar logs ---
logging.basicConfig(level=logging.DEBUG)

# 🔥 INICIALIZAR FIREBASE (ANTES DE CREAR LA APP) 🔥
try:
    if not firebase_admin._apps:
        # ¡Asegúrate de que firebase_key.json esté en esta carpeta!
        cred = credentials.Certificate("firebase_key.json")
        firebase_admin.initialize_app(cred)
        logging.info("🔥 Firebase Admin inicializado correctamente")
except Exception as e:
    logging.error(f"⚠️ Error al inicializar Firebase: {e}")

# --- Esquema del perfil público ---
class UserProfile(BaseModel):
    id: int
    nombre_empresa: str
    email: Optional[str] = None

# --- Conexión a la base de datos PostgreSQL ---
def get_db_connection():
    try:
        conn = psycopg2.connect(
            host="localhost",
            database="prendia_db",
            user="postgres",
            password="Elbicho7",
        )
        logging.debug("Conexión a la base de datos establecida correctamente")
        return conn
    except Exception as e:
        logging.error(f"Error al conectar a la base de datos: {e}")
        raise HTTPException(status_code=500, detail="Error de conexión a la base de datos")

# --- Configuración general ---
app = FastAPI()
templates = Jinja2Templates(directory=".")
app.add_middleware(SessionMiddleware, secret_key="Elbicho7")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


# --- Routers ---
app.include_router(datos_usuario_router)
app.include_router(google_router)
app.include_router(publicaciones_router)
app.include_router(email_router)
app.include_router(chats_router)
app.include_router(resenas_router)
app.include_router(apple_router)

# --- Rutas principales ---
@app.get("/")
def home():
    return FileResponse("index.html")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    try:
        # Limpiar datos residuales de la sesión
        if 'tipo' in request.session:
            del request.session['tipo']
        if 'target' in request.session:
            del request.session['target']
        logging.debug("Página de login renderizada, sesión limpiada")
        return templates.TemplateResponse("login.html", {"request": request})
    except Exception as e:
        logging.error(f"Error en /login: {e}")
        raise HTTPException(status_code=500, detail="Error al cargar la página de login")

@app.get("/dashboard")
def dashboard(request: Request):
    user = request.session.get("user")
    if user:
        return FileResponse("dashboard.html")
    return RedirectResponse(url="/login")

@app.get("/inicio")
def mostrar_inicio():
    return FileResponse("inicio.html")

@app.get("/perfil", response_class=HTMLResponse)
async def perfil(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("perfil.html", {"request": request, "user": user})

@app.get("/salir")
async def salir(request: Request):
    try:
        # Limpiar completamente la sesión
        request.session.clear()
        logging.debug("Sesión cerrada correctamente")
        return RedirectResponse(url="/login", status_code=302)
    except Exception as e:
        logging.error(f"Error al cerrar sesión: {e}")
        raise HTTPException(status_code=500, detail="Error al cerrar sesión")

@app.get("/{filename}")
def serve_static_files(filename: str):
    if os.path.exists(filename):
        return FileResponse(filename)
    return {"error": "Archivo no encontrado"}

