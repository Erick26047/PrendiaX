from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import SessionLocal
from models import DatosUsuario
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import psycopg2
import base64
import logging


def get_db_connection():
    return psycopg2.connect(
        host="localhost",
        database="prendia_db",
        user="postgres",
        password="Elbicho7",  
    )


router = APIRouter()

@router.post("/guardar_datos")
async def guardar_datos(
    request: Request,
    nombre_empresa: str = Form(...),
    direccion: str = Form(None),
    ubicacion_google_maps: str = Form(None),
    telefono: str = Form(None),
    horario: str = Form(None),
    categoria: str = Form(None),
    otra_categoria: str = Form(None),
    servicios: str = Form(None),
    sitio_web: str = Form(None),
    foto: UploadFile = File(None)
):
    user = request.session.get("user")
    if not user or "id" not in user:
        return RedirectResponse(url="/login", status_code=302)

    contenido_foto = await foto.read() if foto else None
    db: Session = SessionLocal()

    nuevo_dato = DatosUsuario(
        user_id=user["id"],
        nombre_empresa=nombre_empresa,
        direccion=direccion,
        ubicacion_google_maps=ubicacion_google_maps,
        telefono=telefono,
        horario=horario,
        categoria=categoria,
        otra_categoria=otra_categoria,
        servicios=servicios,
        sitio_web=sitio_web,
        foto=contenido_foto
    )
    db.add(nuevo_dato)
    db.commit()
    db.close()

    return RedirectResponse(url="/perfil", status_code=302)


templates = Jinja2Templates(directory=".")
templates.env.filters["b64encode"] = lambda data: base64.b64encode(data).decode('utf-8') if data else ""
logging.basicConfig(level=logging.DEBUG)    

@router.get("/perfil")
async def perfil(request: Request):
    try:
        if 'user' in request.session and 'id' in request.session['user']:
            user_id = request.session['user']['id']
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(""" SELECT * FROM datos_usuario WHERE user_id = %s; """, (user_id,))
            datos_usuario = cur.fetchone()
            cur.close()
            conn.close()
            if datos_usuario[11]:
                foto_base64 = base64.b64encode(datos_usuario[11]).decode('utf-8')
                datos_usuario = list(datos_usuario)
                datos_usuario[11] = foto_base64
            return templates.TemplateResponse("perfil.html", {"request": request, "datos_usuario": datos_usuario})
        else:
            return RedirectResponse(url="/login")
    except Exception as e:
        logging.error(f"Error al obtener datos del usuario: {e}")
        return RedirectResponse(url="/dashboard")
       
@router.get("/perfil_cliente")
async def perfil_cliente(request: Request):
    if "user" not in request.session:
        return RedirectResponse(url="/login", status_code=302)

    user_id = request.session["user"]
    email = user_id["email"]

    # Conexión a PostgreSQL (ajusta con tus datos)
    conn = await get_db_connection(
        user="postgres",
        password="Elbicho7",
        database="prendia_db",
        host="localhost"
    )

    row = await conn.fetchrow("SELECT nombre, email FROM usuarios WHERE email = $1", email)
    await conn.close()

    if row:
        return templates.TemplateResponse("perfil_cliente.html", {
            "request": request,
            "nombre": row["nombre"],
            "email": row["email"]
        })
    else:
        return RedirectResponse(url="/login", status_code=302)