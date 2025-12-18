from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException, Header, Response
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from database import SessionLocal
from models import DatosUsuario
from fastapi.templating import Jinja2Templates
import psycopg2
import base64
import logging

# Configuración básica
logging.basicConfig(level=logging.DEBUG)
router = APIRouter()
templates = Jinja2Templates(directory=".")
templates.env.filters["b64encode"] = lambda data: base64.b64encode(data).decode('utf-8') if data else ""

# Tu conexión original para la parte WEB (No la borramos)
def get_db_connection():
    return psycopg2.connect(
        host="localhost",
        database="prendia_db",
        user="postgres",
        password="Elbicho7",
    )

# ==============================================================================
#  SECCIÓN 1: RUTAS WEB (HTML/JINJA2) - ESTO ES TU CÓDIGO ORIGINAL
# ==============================================================================

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
            if datos_usuario and datos_usuario[11]:
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

        # Preparar la respuesta JSON para el frontend WEB
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


# ==============================================================================
#  SECCIÓN 2: API PARA LA APP MÓVIL (FLUTTER) - AQUI ESTAN LAS CORRECCIONES
# ==============================================================================

# 1. Endpoint para ACTUALIZAR perfil desde la APP (Tu código original, correcto)
@router.post("/api/perfil/actualizar")
async def actualizar_perfil_api(
    nombre_empresa: str = Form(...),
    direccion: str = Form(None),
    ubicacion_google_maps: str = Form(None),
    telefono: str = Form(None),
    horario: str = Form(None),
    categoria: str = Form(None),
    otra_categoria: str = Form(None),
    servicios: str = Form(None),
    sitio_web: str = Form(None),
    foto: UploadFile = File(None),
    authorization: str = Header(None) 
):
    print(f"[API] Recibiendo actualización para: {nombre_empresa}")

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token no proporcionado")
    
    try:
        token_str = authorization.split(" ")[1]
        user_id = int(token_str.split("_")[-1])
    except:
        raise HTTPException(status_code=401, detail="Token inválido")

    contenido_foto = None
    if foto:
        contenido_foto = await foto.read()
        if not foto.content_type.startswith('image/'):
             raise HTTPException(status_code=400, detail="El archivo debe ser una imagen")

    db: Session = SessionLocal()
    try:
        datos_usuario = db.query(DatosUsuario).filter(DatosUsuario.user_id == user_id).first()

        if datos_usuario:
            datos_usuario.nombre_empresa = nombre_empresa
            datos_usuario.direccion = direccion
            datos_usuario.ubicacion_google_maps = ubicacion_google_maps
            datos_usuario.telefono = telefono
            datos_usuario.horario = horario
            datos_usuario.categoria = categoria
            datos_usuario.otra_categoria = otra_categoria
            datos_usuario.servicios = servicios
            datos_usuario.sitio_web = sitio_web
            if contenido_foto:
                datos_usuario.foto = contenido_foto
        else:
            nuevo_dato = DatosUsuario(
                user_id=user_id,
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
        return JSONResponse(content={"status": "ok", "message": "Perfil guardado correctamente"}, status_code=200)

    except Exception as e:
        db.rollback()
        logging.error(f"Error API actualizar: {e}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")
    finally:
        db.close()


# 2. Endpoint IMPORTANTE: Sirve la imagen como archivo JPG para que Flutter la pueda leer
@router.get("/api/imagenes/perfil/{user_id}")
def obtener_imagen_perfil(user_id: int):
    db = SessionLocal()
    try:
        datos = db.query(DatosUsuario).filter(DatosUsuario.user_id == user_id).first()
        
        if datos and datos.foto:
            # RETORNAMOS LOS BYTES DIRECTAMENTE
            return Response(content=datos.foto, media_type="image/jpeg")
        else:
            return Response(status_code=404)
    except Exception as e:
        logging.error(f"Error sirviendo imagen: {e}")
        return Response(status_code=500)
    finally:
        db.close()


# 3. Endpoint corregido para OBTENER DATOS en la APP
# (Este reemplaza al que tenias con psycopg2 que estaba duplicado)
# ==============================================================================
#  ENDPOINT EXCLUSIVO PARA LA APP (Une Usuario + Posts en una sola llamada)
# ==============================================================================

@router.get("/api/perfil/{user_id}")
async def perfil_api_combo(user_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 1. BUSCAR DATOS DEL USUARIO (Igual que hace la web en /user/{id})
        cur.execute("""
            SELECT u.id, 
                   COALESCE(du.nombre_empresa, u.nombre),
                   du.direccion, 
                   du.ubicacion_google_maps, 
                   du.telefono, 
                   du.horario, 
                   du.categoria, 
                   du.otra_categoria, 
                   du.servicios, 
                   du.sitio_web,
                   du.foto
            FROM usuarios u
            LEFT JOIN datos_usuario du ON u.id = du.user_id
            WHERE u.id = %s
        """, (user_id,))
        
        datos = cur.fetchone()
        
        if not datos:
            return JSONResponse(status_code=404, content={"message": "Usuario no encontrado"})

        # Preparar URL de foto de perfil
        foto_url = ""
        # Si es emprendedor (tiene categoría) y tiene foto
        if datos[6] and datos[6] != '' and datos[10]: 
             foto_url = f"/foto_perfil/{user_id}"

        user_object = {
            "id": datos[0],
            "nombre_empresa": datos[1],
            "direccion": datos[2] if datos[2] else "",
            "ubicacion_google_maps": datos[3] if datos[3] else "",
            "telefono": datos[4] if datos[4] else "",
            "horario": datos[5] if datos[5] else "",
            "categoria": datos[6] if datos[6] else "",
            "otra_categoria": datos[7] if datos[7] else "",
            "descripcion": datos[8] if datos[8] else "",
            "sitio_web": datos[9] if datos[9] else "",
            "foto_perfil_url": foto_url
        }

        # 2. BUSCAR PUBLICACIONES (Lo que te faltaba: Igual que /user/{id}/publicaciones)
        cur.execute("""
            SELECT id, contenido, imagen, video, fecha_creacion
            FROM publicaciones 
            WHERE user_id = %s
            ORDER BY fecha_creacion DESC
        """, (user_id,))
        
        posts_rows = cur.fetchall()
        posts_list = []
        
        for row in posts_rows:
            # Construir URL de la imagen del post
            post_img_url = ""
            if row[2]: # Si la columna 'imagen' tiene datos (bytes)
                post_img_url = f"/media/{row[0]}" 
            
            posts_list.append({
                "id": row[0],
                "contenido": row[1] if row[1] else "",
                "imagen_url": post_img_url, # <--- Esto es lo que lee Flutter
                "fecha_creacion": str(row[4])
            })

        # 3. ENVIAR PAQUETE COMPLETO
        response_data = {
            "user": user_object,
            "posts": posts_list  # <--- ¡Aquí van las publicaciones!
        }
        
        return JSONResponse(content=response_data)
        
    except Exception as e:
        logging.error(f"Error API perfil combo: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})
    finally:
        if cur: cur.close()
        if conn: conn.close()