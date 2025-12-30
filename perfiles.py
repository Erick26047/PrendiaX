from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException, Header
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse, Response
from fastapi.templating import Jinja2Templates
import psycopg2
from datetime import datetime
import logging
import io
import re  # <--- IMPORTANTE: Necesario para los videos en el celular
from pydantic import BaseModel

router = APIRouter()

# Configurar Jinja2
templates = Jinja2Templates(directory=".")

# Configurar logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('app.log')
    ]
)

# Conexión a la base de datos
def get_db_connection():
    try:
        conn = psycopg2.connect(
            host="localhost",
            database="prendia_db",
            user="postgres",
            password="Elbicho7",
        )
        return conn
    except Exception as e:
        logging.error(f"Error al conectar a la base de datos: {e}")
        raise HTTPException(status_code=500, detail="Error de conexión a la base de datos")

# Tamaño máximo de archivo (10 MB)
MAX_FILE_SIZE = 10 * 1024 * 1024 

# Modelos Pydantic
class InterestRequest(BaseModel):
    user_id: int

class CommentRequest(BaseModel):
    contenido: str

class ReviewRequest(BaseModel):
    texto: str
    calificacion: int

# --- FUNCIÓN HÍBRIDA: Detecta si es App (Token) o Web (Sesión) ---
def get_user_id_hybrid(request: Request):
    # 1. Intentar Token de App Móvil (Header Authorization)
    auth_header = request.headers.get("Authorization")
    if auth_header and "jwt_app_" in auth_header:
        try:
            token_part = auth_header.split("jwt_app_")[1]
            if token_part.isdigit():
                return int(token_part)
        except Exception as e:
            pass
    
    # 2. Intentar Sesión Web (Cookie)
    if 'user' in request.session and 'id' in request.session['user']:
        return int(request.session['user']['id'])
        
    return None

# =======================================================================
#  LA SOLUCIÓN: GET MEDIA CON SOPORTE DE RANGOS (STREAMING)
# =======================================================================
@router.get("/media/{post_id}")
async def get_media(post_id: int, request: Request):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT imagen, video FROM publicaciones WHERE id = %s", (post_id,))
        result = cur.fetchone()
        cur.close()
        conn.close() # Cerramos rápido

        if not result:
            raise HTTPException(status_code=404, detail="Publicación no encontrada")

        imagen_data, video_data = result
        
        # --- CASO 1: IMAGEN (Se envía normal) ---
        if imagen_data:
            return StreamingResponse(
                content=io.BytesIO(imagen_data),
                media_type="image/jpeg",
                headers={"Content-Disposition": "inline"}
            )
        
        # --- CASO 2: VIDEO (Lógica obligatoria para iOS/Flutter) ---
        elif video_data:
            file_size = len(video_data)
            range_header = request.headers.get("range")

            # Headers básicos
            headers = {
                "Accept-Ranges": "bytes",
                "Content-Disposition": f"inline; filename=video_{post_id}.mp4"
            }

            # Si no pide rango, enviamos todo (legacy)
            if not range_header:
                headers["Content-Length"] = str(file_size)
                return StreamingResponse(
                    io.BytesIO(video_data),
                    media_type="video/mp4",
                    headers=headers,
                    status_code=200
                )

            # Parsear el rango solicitado (ej: bytes=0-1024)
            try:
                range_match = re.search(r'bytes=(\d+)-(\d*)', range_header)
                if not range_match:
                    raise ValueError("Rango inválido")
                
                start = int(range_match.group(1))
                end_str = range_match.group(2)
                
                if end_str:
                    end = int(end_str)
                else:
                    end = file_size - 1
            except ValueError:
                start = 0
                end = file_size - 1

            # Validar límites
            if start >= file_size:
                headers["Content-Range"] = f"bytes */{file_size}"
                return Response(status_code=416, headers=headers)

            end = min(end, file_size - 1)
            chunk_length = end - start + 1

            # Cortar los bytes exactos que pide el celular
            chunk_data = video_data[start : end + 1]

            # Headers obligatorios para streaming (206 Partial Content)
            headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            headers["Content-Length"] = str(chunk_length)
            headers["Content-Type"] = "video/mp4"

            return StreamingResponse(
                io.BytesIO(chunk_data),
                status_code=206, # <--- ESTO ES LA CLAVE
                headers=headers,
                media_type="video/mp4"
            )

        else:
            raise HTTPException(status_code=404, detail="Archivo multimedia vacío")

    except Exception as e:
        logging.error(f"Error media {post_id}: {e}")
        raise HTTPException(status_code=500, detail="Error interno")

# ==========================================
#  API PARA LA APP MÓVIL (JSON)
# ==========================================
@router.get("/api/perfil/{user_id}")
async def get_perfil_api(request: Request, user_id: int):
    conn = None
    try:
        viewer_id = get_user_id_hybrid(request)
        conn = get_db_connection()
        cur = conn.cursor()

        # Datos Usuario
        cur.execute("SELECT id, nombre, email FROM usuarios WHERE id = %s", (user_id,))
        user_row = cur.fetchone()
        
        if not user_row:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

        # Datos extra (empresa)
        cur.execute("""
            SELECT nombre_empresa, categoria, descripcion, sitio_web, foto, ubicacion, ubicacion_google_maps, telefono, horario, otra_categoria, servicios 
            FROM datos_usuario WHERE user_id = %s
        """, (user_id,))
        extra_data = cur.fetchone()

        user_data = {
            "id": user_row[0],
            "nombre": user_row[1].strip() if user_row[1] else "Usuario",
            "email": user_row[2] or "",
            "tipo_usuario": "explorador"
        }

        # Si tiene datos de empresa, rellenamos
        if extra_data:
            nombre_empresa, categoria, descripcion, sitio_web, foto, ubicacion, map_link, tel, horario, otra_cat, serv = extra_data
            if categoria:
                user_data["tipo_usuario"] = "emprendedor"
                user_data["nombre_empresa"] = nombre_empresa
                user_data["categoria"] = categoria
                user_data["descripcion"] = descripcion
                user_data["sitio_web"] = sitio_web
                user_data["foto_perfil_url"] = f"/foto_perfil/{user_id}" if foto else ""
                user_data["ubicacion"] = ubicacion
                user_data["ubicacion_google_maps"] = map_link
                user_data["telefono"] = tel
                user_data["horario"] = horario
                user_data["otra_categoria"] = otra_cat
                user_data["servicios"] = serv

        # Publicaciones
        cur.execute("""
            SELECT p.id, p.user_id, p.contenido, p.imagen, p.video, 
                   p.etiquetas, p.fecha_creacion
            FROM publicaciones p
            WHERE p.user_id = %s
            ORDER BY p.fecha_creacion DESC
        """, (user_id,))

        publicaciones = cur.fetchall()
        
        publicaciones_list = [
            {
                "id": row[0],
                "user_id": int(row[1]),
                "contenido": row[2] if row[2] else "",
                "imagen_url": f"/media/{row[0]}" if row[3] else "",
                "video_url": f"/media/{row[0]}" if row[4] else "",
                "etiquetas": row[5] if row[5] else [],
                "fecha_creacion": row[6].strftime("%Y-%m-%d %H:%M:%S"),
            }
            for row in publicaciones
        ]

        return {
            "user": user_data,
            "posts": publicaciones_list,
            "is_owner": viewer_id == user_id
        }

    except Exception as e:
        logging.error(f"[API ERROR] Perfil {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Error interno")
    finally:
        if conn: conn.close()

# ==========================================
#  OTRAS RUTAS NECESARIAS (Feed, Login, etc)
# ==========================================

# Foto Perfil
@router.get("/foto_perfil/{user_id}")
async def get_foto_perfil(user_id: int):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT foto FROM datos_usuario WHERE user_id = %s", (user_id,))
        result = cur.fetchone()
        cur.close()
        if result and result[0]:
            return StreamingResponse(io.BytesIO(result[0]), media_type="image/jpeg")
        raise HTTPException(status_code=404)
    finally:
        if conn: conn.close()

# Publicar
@router.post("/publicar")
async def publicar(request: Request, contenido: str = Form(None), imagen: UploadFile = File(None), video: UploadFile = File(None), etiquetas: str = Form(None)):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id: 
            if request.headers.get("Authorization"): raise HTTPException(status_code=401)
            return RedirectResponse("/login", 302)

        img_data, vid_data = None, None
        if imagen and imagen.size > 0: img_data = await imagen.read()
        if video and video.size > 0: vid_data = await video.read()
        
        tags = [e.strip() for e in etiquetas.split(",")] if etiquetas else []

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO publicaciones (user_id, contenido, imagen, video, etiquetas, fecha_creacion) VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)", 
                    (user_id, contenido, psycopg2.Binary(img_data) if img_data else None, psycopg2.Binary(vid_data) if vid_data else None, tags))
        conn.commit()
        cur.close()
        conn.close()
        return RedirectResponse("/inicio", 302)
    except Exception as e:
        logging.error(f"Error publicar: {e}")
        raise HTTPException(status_code=500)

# Feed API (Para el Inicio)
@router.get("/feed")
async def feed(limit: int = 10, offset: int = 0, request: Request = None):
    conn = None
    try:
        current_user = get_user_id_hybrid(request) if request else -1
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.user_id, p.contenido, p.etiquetas, p.fecha_creacion, 
                   COALESCE(du.nombre_empresa, u.nombre),
                   CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END,
                   p.imagen IS NOT NULL, p.video IS NOT NULL,
                   COUNT(DISTINCT i.user_id),
                   EXISTS(SELECT 1 FROM intereses i WHERE i.publicacion_id=p.id AND i.user_id=%s),
                   (SELECT COUNT(*) FROM comentarios c WHERE c.publicacion_id=p.id)
            FROM publicaciones p
            JOIN usuarios u ON p.user_id = u.id
            LEFT JOIN datos_usuario du ON p.user_id = du.user_id
            LEFT JOIN intereses i ON p.id = i.publicacion_id
            GROUP BY p.id, u.nombre, du.nombre_empresa, du.categoria
            ORDER BY p.fecha_creacion DESC LIMIT %s OFFSET %s
        """, (current_user, limit, offset))
        rows = cur.fetchall()
        return [{
            "id": r[0], "user_id": r[1], "contenido": r[2] or "", "etiquetas": r[3] or [], 
            "fecha_creacion": r[4].strftime("%Y-%m-%d %H:%M:%S"),
            "display_name": r[5], "tipo_usuario": r[6],
            "imagen_url": f"/media/{r[0]}" if r[7] else "", "video_url": f"/media/{r[0]}" if r[8] else "",
            "interesados_count": r[9], "interesado": r[10], "comentarios_count": r[11],
            "foto_perfil_url": f"/foto_perfil/{r[1]}" if r[6]=='emprendedor' else ""
        } for r in rows]
    finally:
        if conn: conn.close()

# Borrar Publicación
@router.delete("/borrar_publicacion/{post_id}")
async def borrar_publicacion(post_id: int, request: Request):
    user_id = get_user_id_hybrid(request)
    if not user_id: raise HTTPException(status_code=401)
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM publicaciones WHERE id=%s AND user_id=%s", (post_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"message": "Borrado"}

# Actualizar Perfil
@router.post("/api/perfil/actualizar")
async def update_profile(request: Request):
    user_id = get_user_id_hybrid(request)
    if not user_id: raise HTTPException(status_code=401)
    
    form = await request.form()
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Upsert datos_usuario
    cur.execute("SELECT 1 FROM datos_usuario WHERE user_id=%s", (user_id,))
    if not cur.fetchone():
        cur.execute("INSERT INTO datos_usuario (user_id) VALUES (%s)", (user_id,))
    
    fields = ['nombre_empresa', 'direccion', 'ubicacion_google_maps', 'telefono', 'horario', 'categoria', 'otra_categoria', 'servicios', 'sitio_web']
    for f in fields:
        if f in form:
            cur.execute(f"UPDATE datos_usuario SET {f}=%s WHERE user_id=%s", (form[f], user_id))
            
    if 'foto' in form and form['foto'].size > 0:
        fdata = await form['foto'].read()
        cur.execute("UPDATE datos_usuario SET foto=%s WHERE user_id=%s", (psycopg2.Binary(fdata), user_id))
        
    conn.commit()
    cur.close()
    conn.close()
    return {"message": "Actualizado"}

# Reseñas
@router.get("/api/perfil/{perfil_id}/resenas")
async def get_reviews(perfil_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT texto, calificacion, u.nombre FROM resenas r JOIN usuarios u ON r.user_id=u.id WHERE perfil_id=%s ORDER BY r.fecha_creacion DESC", (perfil_id,))
    rows = cur.fetchall()
    conn.close()
    return [{"texto": r[0], "calificacion": r[1], "nombre_empresa": r[2]} for r in rows]

@router.post("/api/perfil/{perfil_id}/resenas")
async def post_review(perfil_id: int, req: ReviewRequest, r: Request):
    uid = get_user_id_hybrid(r)
    if not uid: raise HTTPException(401)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO resenas (user_id, perfil_id, texto, calificacion, fecha_creacion) VALUES (%s,%s,%s,%s,NOW())", (uid, perfil_id, req.texto, req.calificacion))
    conn.commit()
    conn.close()
    return {"message":"OK"}

# Login/Salir
@router.post("/salir")
async def salir(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 302)

@router.get("/current_user")
async def current_user(request: Request):
    uid = get_user_id_hybrid(request)
    if not uid: return {}
    return {"id": uid}

