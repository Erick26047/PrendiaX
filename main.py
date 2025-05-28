from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from auth_google import router as google_router
import os
from datos_usuario import router as datos_usuario_router
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates 
from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, Response
from publicaciones import router as publicaciones_router

app = FastAPI()
templates = Jinja2Templates(directory=".")
app.add_middleware(SessionMiddleware, secret_key="Elbicho7")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(datos_usuario_router)
app.include_router(google_router)
app.include_router(publicaciones_router)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

@app.get("/")
def home():
    return FileResponse("index.html")

@app.get("/login")
def login():
    return FileResponse("login.html")


@app.get("/dashboard")
def dashboard(request: Request):
    user = request.session.get("user")
    if user:
        return FileResponse("dashboard.html")
    return RedirectResponse(url="/login")

@app.get("/inicio")
def mostrar_inicio():
    return FileResponse("inicio.html")  # o tu lógica con Jinja2/HTMLResponse


@app.get("/perfil", response_class=HTMLResponse)
async def perfil(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("perfil.html", {"request": request, "user": user})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/salir")
def cerrar_sesion(response: Response):
    response.delete_cookie("session_token")  # Ajusta el nombre a tu cookie real
    return {"mensaje": "Sesión cerrada"}



@app.get("/{filename}")
def serve_static_files(filename: str):
    if os.path.exists(filename):
        return FileResponse(filename)
    return {"error": "Archivo no encontrado"}