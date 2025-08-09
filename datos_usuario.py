from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
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
templates = Jinja2Templates(directory=".")
templates.env.filters["b64encode"] = lambda data: base64.b64encode(data).decode('utf-8') if data else ""
logging.basicConfig(level=logging.DEBUG)

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

@router.put("/perfil/actualizar")
async def actualizar_perfil(
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
        raise HTTPException(status_code=401, detail="No autorizado")

    user_id = user["id"]
    db: Session = SessionLocal()
    try:
        # Buscar el registro existente
        datos_usuario = db.query(DatosUsuario).filter(DatosUsuario.user_id == user_id).first()
        if not datos_usuario:
            raise HTTPException(status_code=404, detail="No se encontraron datos para este usuario")

        # Leer la foto si se proporcionó
        contenido_foto = await foto.read() if foto else datos_usuario.foto
        if foto and not foto.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="El archivo debe ser una imagen")

        # Actualizar los campos
        datos_usuario.nombre_empresa = nombre_empresa
        datos_usuario.direccion = direccion
        datos_usuario.ubicacion_google_maps = ubicacion_google_maps
        datos_usuario.telefono = telefono
        datos_usuario.horario = horario
        datos_usuario.categoria = categoria
        datos_usuario.otra_categoria = otra_categoria
        datos_usuario.servicios = servicios
        datos_usuario.sitio_web = sitio_web
        datos_usuario.foto = contenido_foto

        db.commit()
        db.refresh(datos_usuario)

        # Preparar la respuesta
        response_data = {
            "nombre_empresa": datos_usuario.nombre_empresa,
            "direccion": datos_usuario.direccion,
            "ubicacion_google_maps": datos_usuario.ubicacion_google_maps,
            "telefono": datos_usuario.telefono,
            "horario": datos_usuario.horario,
            "categoria": datos_usuario.categoria,
            "otra_categoria": datos_usuario.otra_categoria,
            "servicios": datos_usuario.servicios,
            "sitio_web": datos_usuario.sitio_web,
            "foto_perfil": f"data:image/jpeg;base64,{base64.b64encode(datos_usuario.foto).decode('utf-8')}" if datos_usuario.foto else None
        }
        return JSONResponse(content=response_data)
    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        logging.error(f"Error al actualizar datos: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        db.close()