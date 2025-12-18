from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException, Header
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import psycopg2
from datetime import datetime
import logging
import io
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
        logging.debug("Conexión a la base de datos establecida correctamente")
        return conn
    except Exception as e:
        logging.error(f"Error al conectar a la base de datos: {e}")
        raise HTTPException(status_code=500, detail="Error de conexión a la base de datos")

# Tamaño máximo de archivo (10 MB)
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB en bytes

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
            # El header llega así: "Bearer jwt_app_45"
            token_part = auth_header.split("jwt_app_")[1]
            if token_part.isdigit():
                return int(token_part)
        except Exception as e:
            logging.error(f"Error al leer token híbrido: {e}")
            pass
    
    # 2. Intentar Sesión Web (Cookie)
    if 'user' in request.session and 'id' in request.session['user']:
        return int(request.session['user']['id'])
        
    return None


# Ruta para renderizar perfil-especifico.html
@router.get("/perfil-especifico", response_class=HTMLResponse)
async def perfil_especifico(request: Request):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            logging.warning("Usuario no autenticado en /perfil-especifico, redirigiendo a /login")
            return RedirectResponse(url="/login", status_code=302)

        viewed_user_id = request.query_params.get('user_id')
        if not viewed_user_id or not viewed_user_id.isdigit():
            logging.error(f"viewed_user_id no proporcionado o inválido: {viewed_user_id}")
            raise HTTPException(status_code=400, detail="ID de usuario inválido en la URL")

        viewed_user_id = int(viewed_user_id)
        logging.debug(f"Rendering perfil-especifico.html para viewed_user_id: {viewed_user_id}")

        return templates.TemplateResponse("perfil-especifico.html", {
            "request": request,
            "current_user_id": user_id,
            "viewed_user_id": viewed_user_id
        })
    except Exception as e:
        logging.error(f"Error en /perfil-especifico: {e}")
        raise HTTPException(status_code=500, detail=f"Error al cargar perfil-especifico: {str(e)}")

# Ruta para servir foto de perfil
@router.get("/foto_perfil/{user_id}")
async def get_foto_perfil(user_id: int):
    conn = None
    try:
        logging.debug(f"Obteniendo foto de perfil para user_id: {user_id}")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT CASE 
                        WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                        ELSE 'explorador'
                    END AS tipo_usuario
            FROM usuarios u
            LEFT JOIN datos_usuario du ON u.id = du.user_id
            WHERE u.id = %s
        """, (user_id,))
        result = cur.fetchone()
        if not result or result[0] != 'emprendedor':
            logging.debug(f"User_id {user_id} es explorador, no se devuelve foto de perfil")
            raise HTTPException(status_code=404, detail="Foto de perfil no disponible para exploradores")

        cur.execute("SELECT foto FROM datos_usuario WHERE user_id = %s", (user_id,))
        result = cur.fetchone()
        cur.close()

        if not result or not result[0]:
            logging.debug(f"No se encontró foto de perfil para user_id: {user_id}")
            raise HTTPException(status_code=404, detail="Foto de perfil no encontrada")

        foto_data = result[0]
        logging.debug(f"Foto de perfil encontrada para user_id: {user_id}, tamaño: {len(foto_data)} bytes")
        return StreamingResponse(io.BytesIO(foto_data), media_type="image/jpeg")
    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Error al obtener foto de perfil para user_id {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener foto de perfil")
    finally:
        if conn:
            conn.close()
            logging.debug("Conexión a la base de datos cerrada")

# Ruta para servir multimedia de publicaciones
# BUSCA ESTA RUTA EN publicaciones.py Y REEMPLÁZALA:

@router.get("/media/{post_id}")
async def get_media(post_id: int):
    # NOTA: Hemos quitado 'request: Request' y la validación de usuario.
    # Las imágenes deben ser accesibles para que la App las pueda cargar 
    # sin necesidad de enviar headers complejos en cada widget de imagen.
    try:
        logging.debug(f"Solicitando multimedia pública para post_id: {post_id}")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT imagen, video
            FROM publicaciones
            WHERE id = %s
        """, (post_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()

        if not result:
            logging.warning(f"Publicación no encontrada para post_id: {post_id}")
            raise HTTPException(status_code=404, detail="Publicación no encontrada")

        imagen_data, video_data = result
        
        if imagen_data:
            return StreamingResponse(
                content=io.BytesIO(imagen_data),
                media_type="image/jpeg",
                headers={"Content-Disposition": f"inline; filename=post_{post_id}_image.jpg"}
            )
        elif video_data:
            return StreamingResponse(
                content=io.BytesIO(video_data),
                media_type="video/mp4",
                headers={"Content-Disposition": f"inline; filename=post_{post_id}_video.mp4"}
            )
        else:
            # Si el post existe pero no tiene media (raro, pero posible)
            # Podemos devolver un 404 o una imagen vacía
            logging.warning(f"El post {post_id} existe pero no tiene archivo multimedia")
            raise HTTPException(status_code=404, detail="Archivo multimedia no encontrado")

    except Exception as e:
        logging.error(f"Error al servir multimedia para post_id {post_id}: {e}")
        # Si es un error de DB, lanzamos 500
        raise HTTPException(status_code=500, detail="Error interno al servir imagen")

# Ruta para renderizar inicio.html
@router.get("/inicio", response_class=HTMLResponse)
async def inicio(request: Request, limit: int = 10, offset: int = 0):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            logging.warning("Usuario no autenticado en /inicio, redirigiendo a /login")
            return RedirectResponse(url="/login", status_code=302)

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT 
                    p.id, 
                    p.user_id, 
                    p.contenido, 
                    p.etiquetas, 
                    p.fecha_creacion, 
                    COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                    CASE 
                        WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                        ELSE 'explorador'
                    END AS tipo_usuario,
                    p.imagen IS NOT NULL AS has_imagen,
                    p.video IS NOT NULL AS has_video,
                    COUNT(i.user_id) AS interesados_count,
                    EXISTS (
                        SELECT 1 FROM intereses i 
                        WHERE i.publicacion_id = p.id AND i.user_id = %s
                    ) AS interesado
                FROM publicaciones p
                JOIN usuarios u ON p.user_id = u.id
                LEFT JOIN datos_usuario du ON p.user_id = du.user_id
                LEFT JOIN intereses i ON p.id = i.publicacion_id
                GROUP BY p.id, p.user_id, p.contenido, p.etiquetas, 
                         p.fecha_creacion, du.nombre_empresa, u.nombre, du.categoria
                ORDER BY p.fecha_creacion DESC
                LIMIT %s OFFSET %s
            """, (user_id, limit, offset))
            publicaciones = cur.fetchall()
            logging.debug(f"Se obtuvieron {len(publicaciones)} publicaciones para renderizar en inicio.html")
            cur.close()
        except Exception as e:
            logging.error(f"Error al obtener publicaciones: {e}")
            raise HTTPException(status_code=500, detail="Error al obtener publicaciones")
        finally:
            if conn:
                conn.close()

        publicaciones_list = [
            {
                "id": row[0],
                "user_id": int(row[1]),
                "contenido": row[2] if row[2] else "",
                "imagen_url": f"/media/{row[0]}" if row[7] else "",
                "video_url": f"/media/{row[0]}" if row[8] else "",
                "etiquetas": row[3] if row[3] else [],
                "fecha_creacion": row[4].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[6] == 'emprendedor' else "",
                "nombre_empresa": row[5],
                "tipo_usuario": row[6],
                "interesados_count": int(row[9]),
                "interesado": row[10]
            }
            for row in publicaciones
        ]

        return templates.TemplateResponse("inicio.html", {
            "request": request,
            "publicaciones": publicaciones_list,
            "user_id": user_id
        })
    except Exception as e:
        logging.error(f"Error en /inicio: {e}")
        return RedirectResponse(url="/login", status_code=302)

# Ruta para publicar (Soporta App y Web)
@router.post("/publicar")
async def publicar(
    request: Request,
    contenido: str = Form(None),
    imagen: UploadFile = File(None),
    video: UploadFile = File(None),
    etiquetas: str = Form(None)
):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            # Si es la App (por el header), damos 401. Si es web, redirect.
            if request.headers.get("Authorization"):
                raise HTTPException(status_code=401, detail="No autorizado")
            return RedirectResponse(url="/login", status_code=302)

        logging.debug(f"Publicando con user_id: {user_id}")

        if not contenido and (not imagen or imagen.size == 0) and (not video or video.size == 0):
            logging.warning("Intento de publicación sin contenido, imagen ni video")
            raise HTTPException(status_code=400, detail="Debe incluir al menos contenido, una imagen o un video")

        if imagen and imagen.size > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"La imagen excede el tamaño máximo de {MAX_FILE_SIZE // (1024 * 1024)} MB")

        if video and video.size > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"El video excede el tamaño máximo de {MAX_FILE_SIZE // (1024 * 1024)} MB")

        if imagen and video:
            raise HTTPException(status_code=400, detail="No se puede cargar una imagen y un video al mismo tiempo")

        etiquetas_lista = [e.strip() for e in etiquetas.split(",") if e.strip()] if etiquetas else []

        imagen_data = None
        video_data = None
        if imagen and imagen.size > 0:
            if not imagen.content_type.startswith('image/'):
                raise HTTPException(status_code=400, detail="Solo se permiten imágenes")
            imagen_data = await imagen.read()

        if video and video.size > 0:
            if not video.content_type.startswith('video/'):
                raise HTTPException(status_code=400, detail="Solo se permiten videos")
            video_data = await video.read()

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute("SELECT id FROM usuarios WHERE id = %s", (user_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=400, detail="Usuario no encontrado")

            query = """
                INSERT INTO publicaciones (user_id, contenido, imagen, video, etiquetas, fecha_creacion)
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING id
            """
            cur.execute(query, (user_id, contenido, psycopg2.Binary(imagen_data) if imagen_data else None, psycopg2.Binary(video_data) if video_data else None, etiquetas_lista))
            post_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            logging.debug(f"Publicación guardada con ID: {post_id}")
        except Exception as e:
            if conn:
                conn.rollback()
            raise HTTPException(status_code=500, detail=f"Error al guardar publicación: {str(e)}")
        finally:
            if conn:
                conn.close()

        # Para web redirigimos. La App puede leer el 302 o podemos adaptar luego.
        return RedirectResponse(url="/inicio", status_code=302)
    except Exception as e:
        logging.error(f"Error en /publicar: {e}")
        raise HTTPException(status_code=500, detail=f"Error al guardar publicación: {str(e)}")

# Ruta para el feed (API JSON para App y Web AJAX)
@router.get("/feed")
async def feed(limit: int = 10, offset: int = 0, request: Request = None):
    conn = None
    try:
        current_user = get_user_id_hybrid(request) if request else None
        logging.debug(f"Generando feed. Usuario actual: {current_user}")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                p.id, 
                p.user_id, 
                p.contenido, 
                p.etiquetas, 
                p.fecha_creacion, 
                COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                CASE 
                    WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                    ELSE 'explorador'
                END AS tipo_usuario,
                p.imagen IS NOT NULL AS has_imagen,
                p.video IS NOT NULL AS has_video,
                COUNT(i.user_id) AS interesados_count,
                EXISTS (
                    SELECT 1 FROM intereses i 
                    WHERE i.publicacion_id = p.id AND i.user_id = %s
                ) AS interesado
            FROM publicaciones p
            JOIN usuarios u ON p.user_id = u.id
            LEFT JOIN datos_usuario du ON p.user_id = du.user_id
            LEFT JOIN intereses i ON p.id = i.publicacion_id
            GROUP BY p.id, p.user_id, p.contenido, p.etiquetas, 
                     p.fecha_creacion, du.nombre_empresa, u.nombre, du.categoria
            ORDER BY p.fecha_creacion DESC
            LIMIT %s OFFSET %s
        """, (current_user if current_user else -1, limit, offset))
        publicaciones = cur.fetchall()
        cur.close()

        publicaciones_list = [
            {
                "id": row[0],
                "user_id": int(row[1]),
                "contenido": row[2] if row[2] else "",
                "imagen_url": f"/media/{row[0]}" if row[7] else "",
                "video_url": f"/media/{row[0]}" if row[8] else "",
                "etiquetas": row[3] if row[3] else [],
                "fecha_creacion": row[4].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[6] == 'emprendedor' else "",
                "nombre_empresa": row[5],
                "tipo_usuario": row[6],
                "interesados_count": int(row[9]),
                "interesado": row[10]
            }
            for row in publicaciones
        ]
        return publicaciones_list
    except Exception as e:
        logging.error(f"Error al cargar feed: {e}")
        raise HTTPException(status_code=500, detail=f"Error al cargar feed: {str(e)}")
    finally:
        if conn:
            conn.close()

# Ruta para buscar publicaciones
@router.get("/search")
async def search_publicaciones(query: str, limit: int = 10, offset: int = 0, request: Request = None):
    query = query.strip().lower()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    conn = None
    try:
        current_user = get_user_id_hybrid(request) if request else None

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                p.id, 
                p.user_id, 
                p.contenido, 
                p.etiquetas, 
                p.fecha_creacion, 
                COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                CASE 
                    WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                    ELSE 'explorador'
                END AS tipo_usuario,
                p.imagen IS NOT NULL AS has_imagen,
                p.video IS NOT NULL AS has_video,
                COUNT(i.user_id) AS interesados_count,
                EXISTS (
                    SELECT 1 FROM intereses i 
                    WHERE i.publicacion_id = p.id AND i.user_id = %s
                ) AS interesado
            FROM publicaciones p
            JOIN usuarios u ON p.user_id = u.id
            LEFT JOIN datos_usuario du ON p.user_id = du.user_id
            LEFT JOIN intereses i ON p.id = i.publicacion_id
            WHERE LOWER(COALESCE(du.nombre_empresa, u.nombre)) LIKE %s
               OR EXISTS (
                   SELECT 1
                   FROM unnest(p.etiquetas) AS etiqueta
                   WHERE LOWER(etiqueta) LIKE %s
               )
            GROUP BY p.id, p.user_id, p.contenido, p.etiquetas, 
                     p.fecha_creacion, du.nombre_empresa, u.nombre, du.categoria
            ORDER BY p.fecha_creacion DESC
            LIMIT %s OFFSET %s
        """, (current_user if current_user else -1, f"%{query}%", f"%{query}%", limit, offset))
        publicaciones = cur.fetchall()
        cur.close()

        publicaciones_list = [
            {
                "id": row[0],
                "user_id": int(row[1]),
                "contenido": row[2] if row[2] else "",
                "imagen_url": f"/media/{row[0]}" if row[7] else "",
                "video_url": f"/media/{row[0]}" if row[8] else "",
                "etiquetas": row[3] if row[3] else [],
                "fecha_creacion": row[4].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[6] == 'emprendedor' else "",
                "nombre_empresa": row[5],
                "tipo_usuario": row[6],
                "interesados_count": int(row[9]),
                "interesado": row[10]
            }
            for row in publicaciones
        ]
        return publicaciones_list
    except Exception as e:
        logging.error(f"Error al buscar publicaciones: {e}")
        raise HTTPException(status_code=500, detail=f"Error al buscar publicaciones: {str(e)}")
    finally:
        if conn:
            conn.close()

# Ruta para el feed del perfil
@router.get("/perfil/feed")
async def perfil_feed(request: Request, limit: int = 10, offset: int = 0):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            raise HTTPException(status_code=401, detail="No autorizado")

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT 
                    p.id, 
                    p.user_id, 
                    p.contenido, 
                    p.etiquetas, 
                    p.fecha_creacion, 
                    COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                    CASE 
                        WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                        ELSE 'explorador'
                    END AS tipo_usuario,
                    p.imagen IS NOT NULL AS has_imagen,
                    p.video IS NOT NULL AS has_video
                FROM publicaciones p
                JOIN usuarios u ON p.user_id = u.id
                LEFT JOIN datos_usuario du ON p.user_id = du.user_id
                WHERE p.user_id = %s
                ORDER BY p.fecha_creacion DESC
                LIMIT %s OFFSET %s
            """, (user_id, limit, offset))
            publicaciones = cur.fetchall()
            cur.close()
        except Exception as e:
            raise HTTPException(status_code=500, detail="Error al obtener publicaciones")
        finally:
            if conn:
                conn.close()

        publicaciones_list = [
            {
                "id": row[0],
                "user_id": int(row[1]),
                "contenido": row[2] if row[2] else "",
                "imagen_url": f"/media/{row[0]}" if row[7] else "",
                "video_url": f"/media/{row[0]}" if row[8] else "",
                "etiquetas": row[3] if row[3] else [],
                "fecha_creacion": row[4].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[6] == 'emprendedor' else "",
                "nombre_empresa": row[5],
                "tipo_usuario": row[6]
            }
            for row in publicaciones
        ]
        return publicaciones_list
    except Exception as e:
        logging.error(f"Error en /perfil/feed: {e}")
        raise HTTPException(status_code=500, detail=f"Error al cargar feed del perfil: {str(e)}")

# Ruta para obtener una publicación específica
@router.get("/publicacion/{post_id}")
async def get_publicacion(post_id: int, request: Request):
    try:
        current_user = get_user_id_hybrid(request)
        
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT 
                    p.id, 
                    p.user_id, 
                    p.contenido, 
                    p.etiquetas, 
                    p.fecha_creacion, 
                    COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                    CASE 
                        WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                        ELSE 'explorador'
                    END AS tipo_usuario,
                    p.imagen IS NOT NULL AS has_imagen,
                    p.video IS NOT NULL AS has_video,
                    COUNT(i.user_id) AS interesados_count,
                    EXISTS (
                        SELECT 1 FROM intereses i 
                        WHERE i.publicacion_id = p.id AND i.user_id = %s
                    ) AS interesado
                FROM publicaciones p
                JOIN usuarios u ON p.user_id = u.id
                LEFT JOIN datos_usuario du ON p.user_id = du.user_id
                LEFT JOIN intereses i ON p.id = i.publicacion_id
                WHERE p.id = %s
                GROUP BY p.id, p.user_id, p.contenido, p.etiquetas, 
                         p.fecha_creacion, du.nombre_empresa, u.nombre, du.categoria
            """, (current_user if current_user else -1, post_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Publicación no encontrada")
            cur.close()

            publicacion = {
                "id": row[0],
                "user_id": int(row[1]),
                "contenido": row[2] if row[2] else "",
                "imagen_url": f"/media/{row[0]}" if row[7] else "",
                "video_url": f"/media/{row[0]}" if row[8] else "",
                "etiquetas": row[3] if row[3] else [],
                "fecha_creacion": row[4].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[6] == 'emprendedor' else "",
                "nombre_empresa": row[5],
                "tipo_usuario": row[6],
                "interesados_count": int(row[9]),
                "interesado": row[10]
            }
            return publicacion
        except Exception as e:
            raise HTTPException(status_code=500, detail="Error al obtener publicación")
        finally:
            if conn:
                conn.close()
    except Exception as e:
        logging.error(f"Error en /publicacion/{post_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al obtener publicación: {str(e)}")

# Ruta para obtener datos del usuario
@router.get("/user/{user_id}")
async def get_user(user_id: int):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                u.id,
                CASE 
                    WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                    ELSE 'explorador'
                END AS tipo_usuario,
                COALESCE(du.nombre_empresa, u.nombre) AS nombre_empresa,
                u.email,
                du.foto AS foto_perfil,
                du.direccion,
                du.ubicacion_google_maps,
                du.telefono,
                du.horario,
                du.categoria,
                du.otra_categoria,
                du.servicios,
                du.sitio_web
            FROM usuarios u
            LEFT JOIN datos_usuario du ON u.id = du.user_id
            WHERE u.id = %s
        """, (user_id,))
        result = cur.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        cur.close()

        user_data = {
            "user_id": result[0],
            "tipo": result[1],
            "nombre_empresa": result[2] if result[2] else "",
            "email": result[3] if result[3] else "",
            "foto_perfil": f"/foto_perfil/{user_id}" if result[1] == 'emprendedor' and result[4] else "",
            "direccion": result[5] if result[5] else "",
            "ubicacion_google_maps": result[6] if result[6] else "",
            "telefono": result[7] if result[7] else "",
            "horario": result[8] if result[8] else "",
            "categoria": result[9] if result[9] else "",
            "otra_categoria": result[10] if result[10] else "",
            "servicios": result[11] if result[11] else "",
            "sitio_web": result[12] if result[12] else ""
        }
        return user_data
    except Exception as e:
        logging.error(f"Error al obtener datos del usuario para user_id {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener datos del usuario")
    finally:
        if conn:
            conn.close()

# Ruta para obtener publicaciones de un usuario
@router.get("/user/{user_id}/publicaciones")
async def get_user_publicaciones(user_id: int, limit: int = 10, offset: int = 0, request: Request = None):
    base_url = str(request.base_url).rstrip("/") if request else ""
    conn = None
    try:
        current_user = get_user_id_hybrid(request) if request else None

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                p.id, 
                p.user_id, 
                p.contenido, 
                p.etiquetas, 
                p.fecha_creacion, 
                COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                CASE 
                    WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                    ELSE 'explorador'
                END AS tipo_usuario,
                p.imagen IS NOT NULL AS has_imagen,
                p.video IS NOT NULL AS has_video,
                COUNT(i.user_id) AS interesados_count,
                EXISTS (
                    SELECT 1 FROM intereses i 
                    WHERE i.publicacion_id = p.id AND i.user_id = %s
                ) AS interesado
            FROM publicaciones p
            JOIN usuarios u ON p.user_id = u.id
            LEFT JOIN datos_usuario du ON p.user_id = du.user_id
            LEFT JOIN intereses i ON p.id = i.publicacion_id
            WHERE p.user_id = %s
            GROUP BY p.id, p.user_id, p.contenido, p.etiquetas, 
                     p.fecha_creacion, du.nombre_empresa, u.nombre, du.categoria
            ORDER BY p.fecha_creacion DESC
            LIMIT %s OFFSET %s
        """, (current_user if current_user else -1, user_id, limit, offset))
        publicaciones = cur.fetchall()
        cur.close()

        publicaciones_list = [
            {
                "id": row[0],
                "user_id": int(row[1]),
                "contenido": row[2] if row[2] else "",
                "imagen_url": f"{base_url}/media/{row[0]}" if row[7] else "",
                "video_url": f"{base_url}/media/{row[0]}" if row[8] else "",
                "etiquetas": row[3] if row[3] else [],
                "fecha_creacion": row[4].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"{base_url}/foto_perfil/{row[1]}" if row[6] == 'emprendedor' else "",
                "nombre_empresa": row[5],
                "tipo_usuario": row[6],
                "interesados_count": int(row[9]),
                "interesado": row[10]
            }
            for row in publicaciones
        ]
        return publicaciones_list
    except Exception as e:
        logging.error(f"Error al obtener publicaciones para user_id {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener publicaciones")
    finally:
        if conn:
            conn.close()

# ---------------------------------------------------------
# RESEÑAS - INTACTO (Como solicitaste)
# ---------------------------------------------------------
@router.get("/api/perfil/{perfil_id}/resenas")
async def get_user_resenas(perfil_id: int, request: Request, limit: int = 10, offset: int = 0):
    user_id = get_user_id_hybrid(request)
    logging.debug(f"Cargando reseñas perfil {perfil_id}. Usuario detectado: {user_id}")

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT id FROM usuarios WHERE id = %s", (perfil_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Perfil no encontrado")

        cur.execute("""
            SELECT r.id, r.user_id, r.perfil_id, r.texto, r.calificacion, r.fecha_creacion,
                   COALESCE(du.nombre_empresa, u.nombre),
                   CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END,
                   du.foto
            FROM resenas r
            JOIN usuarios u ON r.user_id = u.id
            LEFT JOIN datos_usuario du ON r.user_id = du.user_id
            WHERE r.perfil_id = %s
            ORDER BY r.fecha_creacion DESC
            LIMIT %s OFFSET %s
        """, (perfil_id, limit, offset))
        
        rows = cur.fetchall()
        cur.close()

        data = []
        for r in rows:
            foto_url = ""
            if r[7] == 'emprendedor' and r[8]:
                foto_url = f"/foto_perfil/{r[1]}"

            data.append({
                "id": r[0], "user_id": r[1], "perfil_id": r[2], "texto": r[3],
                "calificacion": r[4], "fecha_creacion": r[5].strftime("%Y-%m-%d %H:%M:%S"),
                "nombre_empresa": r[6] or "Anónimo", "tipo_usuario": r[7],
                "foto_perfil": foto_url
            })
        return data

    except Exception as e:
        logging.error(f"Error reseñas: {e}")
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@router.post("/api/perfil/{perfil_id}/resenas")
async def create_review(perfil_id: int, request: ReviewRequest, http_request: Request):
    user_id = get_user_id_hybrid(http_request)
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Inicia sesión para comentar")

    texto = request.texto.strip()
    if user_id == perfil_id:
        raise HTTPException(status_code=400, detail="No puedes reseñarte a ti mismo")
    if not texto:
        raise HTTPException(status_code=400, detail="Texto vacío")

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT id FROM usuarios WHERE id = %s", (perfil_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Perfil no encontrado")

        cur.execute("""
            INSERT INTO resenas (user_id, perfil_id, texto, calificacion, fecha_creacion)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            RETURNING id, fecha_creacion
        """, (user_id, perfil_id, texto, request.calificacion))
        new_data = cur.fetchone()
        conn.commit()

        cur.execute("""
            SELECT COALESCE(du.nombre_empresa, u.nombre), 
                   CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END,
                   du.foto
            FROM usuarios u LEFT JOIN datos_usuario du ON u.id = du.user_id WHERE u.id = %s
        """, (user_id,))
        autor = cur.fetchone()
        cur.close()

        foto_url = f"/foto_perfil/{user_id}" if (autor[1] == 'emprendedor' and autor[2]) else ""

        return {
            "id": new_data[0], "user_id": user_id, "perfil_id": perfil_id,
            "texto": texto, "calificacion": request.calificacion,
            "fecha_creacion": new_data[1].strftime("%Y-%m-%d %H:%M:%S"),
            "nombre_empresa": autor[0] or "Anónimo", "tipo_usuario": autor[1],
            "foto_perfil": foto_url
        }
    except Exception as e:
        if conn: conn.rollback()
        logging.error(f"Error crear reseña: {e}")
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

# Ruta para obtener usuario actual
@router.get("/current_user")
async def get_current_user(request: Request):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            return {"user_id": None, "tipo": None}
        
        try:
            conn = None
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("""
                    SELECT CASE 
                               WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                               ELSE 'explorador'
                           END AS tipo_usuario
                    FROM usuarios u
                    LEFT JOIN datos_usuario du ON u.id = du.user_id
                    WHERE u.id = %s
                """, (user_id,))
                result = cur.fetchone()
                user_tipo = result[0] if result else 'explorador'
                cur.close()
            except Exception as e:
                logging.error(f"Error al determinar el tipo de usuario en /current_user: {e}")
                user_tipo = 'explorador'
            finally:
                if conn:
                    conn.close()

            return {"user_id": user_id, "tipo": user_tipo}
        except Exception as e:
            return {"user_id": None, "tipo": None}
    except Exception as e:
        logging.error(f"Error en /current_user: {e}")
        return {"user_id": None, "tipo": None}

# Ruta para cerrar sesión
@router.post("/salir")
async def salir(request: Request):
    request.session.clear()
    logging.debug("Sesión cerrada correctamente")
    return RedirectResponse(url="/login", status_code=302)

# Ruta para borrar publicación
@router.delete("/borrar_publicacion/{post_id}")
async def borrar_publicacion(post_id: int, request: Request):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            raise HTTPException(status_code=401, detail="No autorizado")

        logging.debug(f"Intento de borrar publicación {post_id} por user_id: {user_id}")

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute("SELECT user_id FROM publicaciones WHERE id = %s", (post_id,))
            result = cur.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="Publicación no encontrada")
            if result[0] != user_id:
                raise HTTPException(status_code=403, detail="No tienes permiso para borrar esta publicación")

            cur.execute("DELETE FROM publicaciones WHERE id = %s", (post_id,))
            conn.commit()
            cur.close()
            logging.debug(f"Publicación {post_id} eliminada correctamente")
            return {"message": "Publicación eliminada correctamente"}
        except Exception as e:
            if conn:
                conn.rollback()
            logging.error(f"Error al borrar publicación: {e}")
            raise HTTPException(status_code=500, detail=f"Error al borrar publicación: {str(e)}")
        finally:
            if conn:
                conn.close()
    except Exception as e:
        logging.error(f"Error en /borrar_publicacion: {e}")
        raise HTTPException(status_code=500, detail=f"Error al borrar publicación: {str(e)}")

# Ruta para listar comentarios
@router.get("/publicacion/{post_id}/comentarios")
async def list_comments(post_id: int, limit: int = 5, offset: int = 0):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM publicaciones WHERE id = %s", (post_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Publicación no encontrada")

        cur.execute("SELECT COUNT(*) FROM comentarios WHERE publicacion_id = %s", (post_id,))
        total = cur.fetchone()[0]

        cur.execute("""
            SELECT c.id, c.publicacion_id, c.user_id, c.contenido, c.fecha_creacion,
                   COALESCE(du.nombre_empresa, u.nombre) AS nombre_empresa,
                   CASE 
                       WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN %s || c.user_id
                       ELSE ''
                   END AS foto_perfil_url,
                   CASE 
                       WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                       ELSE 'explorador'
                   END AS tipo_usuario
            FROM comentarios c
            JOIN usuarios u ON c.user_id = u.id
            LEFT JOIN datos_usuario du ON c.user_id = du.user_id
            WHERE c.publicacion_id = %s
            ORDER BY c.fecha_creacion DESC
            LIMIT %s OFFSET %s
        """, ("/foto_perfil/", post_id, limit, offset))
        comentarios = cur.fetchall()

        comentarios_list = [
            {
                "id": row[0],
                "publicacion_id": row[1],
                "user_id": int(row[2]),
                "contenido": row[3],
                "fecha_creacion": row[4].strftime("%Y-%m-%d %H:%M:%S"),
                "nombre_empresa": row[5] or "Anónimo",
                "foto_perfil_url": row[6],
                "tipo_usuario": row[7]
            }
            for row in comentarios
        ]

        cur.close()
        return {"comentarios": comentarios_list, "total": total}
    except Exception as e:
        logging.error(f"Error al listar comentarios para la publicación {post_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al listar comentarios: {str(e)}")
    finally:
        if conn:
            conn.close()

# Ruta para publicar comentario
@router.post("/publicacion/{post_id}/comentar")
async def post_comment(post_id: int, request: CommentRequest, http_request: Request):
    conn = None
    try:
        user_id = get_user_id_hybrid(http_request)
        if not user_id:
            raise HTTPException(status_code=401, detail="Debes iniciar sesión para comentar")

        contenido = request.contenido.strip()
        if not contenido:
            raise HTTPException(status_code=400, detail="El comentario no puede estar vacío")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM publicaciones WHERE id = %s", (post_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Publicación no encontrada")

        cur.execute("""
            INSERT INTO comentarios (publicacion_id, user_id, contenido, fecha_creacion)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            RETURNING id, publicacion_id, user_id, contenido, fecha_creacion
        """, (post_id, user_id, contenido))
        comment = cur.fetchone()
        conn.commit()

        # Crear notificación
        await crear_notificacion(
            user_id=user_id,
            publicacion_id=post_id,
            tipo="comentario",
            actor_id=user_id,
            mensaje=contenido
        )

        cur.execute("""
            SELECT COALESCE(du.nombre_empresa, u.nombre) AS nombre_empresa,
                   CASE 
                       WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN %s || %s
                       ELSE ''
                   END AS foto_perfil_url,
                   CASE 
                       WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                       ELSE 'explorador'
                   END AS tipo_usuario
            FROM usuarios u
            LEFT JOIN datos_usuario du ON u.id = du.user_id
            WHERE u.id = %s
        """, ("/foto_perfil/", user_id, user_id))
        user_data = cur.fetchone()
        cur.close()

        comment_dict = {
            "id": comment[0],
            "publicacion_id": comment[1],
            "user_id": int(comment[2]),
            "contenido": comment[3],
            "fecha_creacion": comment[4].strftime("%Y-%m-%d %H:%M:%S"),
            "nombre_empresa": user_data[0] or "Anónimo",
            "foto_perfil_url": user_data[1],
            "tipo_usuario": user_data[2]
        }
        return comment_dict
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Error al crear comentario para la publicación {post_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al crear comentario: {str(e)}")
    finally:
        if conn:
            conn.close()

# Ruta para gestionar interés
@router.post("/publicacion/{post_id}/interesar")
async def toggle_interest(post_id: int, request: InterestRequest, http_request: Request):
    conn = None
    try:
        user_id = get_user_id_hybrid(http_request)
        if not user_id:
            raise HTTPException(status_code=401, detail="No autorizado")

        if user_id != request.user_id:
            raise HTTPException(status_code=403, detail="ID de usuario no coincide con la sesión")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM publicaciones WHERE id = %s", (post_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Publicación no encontrada")

        cur.execute("SELECT id FROM usuarios WHERE id = %s", (user_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

        cur.execute("""
            SELECT id FROM intereses WHERE publicacion_id = %s AND user_id = %s
        """, (post_id, user_id))
        existing_interest = cur.fetchone()

        if existing_interest:
            cur.execute("""
                DELETE FROM intereses WHERE publicacion_id = %s AND user_id = %s
            """, (post_id, user_id))
        else:
            cur.execute("""
                INSERT INTO intereses (publicacion_id, user_id, fecha_creacion)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
            """, (post_id, user_id))
            # Crear notificación
            await crear_notificacion(
                user_id=user_id,
                publicacion_id=post_id,
                tipo="interes",
                actor_id=user_id
            )

        conn.commit()

        cur.execute("""
            SELECT COUNT(*) FROM intereses WHERE publicacion_id = %s
        """, (post_id,))
        interesados_count = cur.fetchone()[0]

        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM intereses WHERE publicacion_id = %s AND user_id = %s
            )
        """, (post_id, user_id))
        interesado = cur.fetchone()[0]

        cur.close()
        return {
            "interesados_count": interesados_count,
            "interesado": interesado
        }
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Error al gestionar interés para publicación {post_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al gestionar interés: {str(e)}")
    finally:
        if conn:
            conn.close()

# Ruta para borrar comentario
@router.delete("/borrar_comentario/{comentario_id}")
async def borrar_comentario(comentario_id: int, request: Request):
    conn = None
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            raise HTTPException(status_code=401, detail="No autorizado")

        logging.debug(f"Intentando borrar comentario_id: {comentario_id}, user_id: {user_id}")

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT user_id, publicacion_id FROM comentarios WHERE id = %s
        """, (comentario_id,))
        comment = cur.fetchone()
        if not comment:
            raise HTTPException(status_code=404, detail="Comentario no encontrado")

        if comment[0] != user_id:
            raise HTTPException(status_code=403, detail="No autorizado para borrar este comentario")

        cur.execute("""
            DELETE FROM comentarios WHERE id = %s
        """, (comentario_id,))
        conn.commit()
        logging.debug(f"Comentario borrado exitosamente: {comentario_id}")

        cur.close()
        return {"message": "Comentario borrado exitosamente"}
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Error al borrar comentario {comentario_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al borrar comentario: {str(e)}")
    finally:
        if conn:
            conn.close()

# Ruta para eliminar reseña
@router.delete("/api/perfil/{perfil_id}/resenas/{resena_id}")
async def delete_review(perfil_id: int, resena_id: int, request: Request):
    conn = None
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            raise HTTPException(status_code=401, detail="Debes iniciar sesión para eliminar una reseña")

        logging.debug(f"Intentando eliminar reseña {resena_id} para perfil_id: {perfil_id} por user_id: {user_id}")

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, user_id, perfil_id, texto, calificacion, fecha_creacion 
            FROM resenas 
            WHERE id = %s
        """, (resena_id,))
        resena = cur.fetchone()

        if not resena:
            raise HTTPException(status_code=404, detail="Reseña no encontrada")

        resena_id_db, resena_user_id, resena_perfil_id, texto, calificacion, fecha_creacion = resena

        if resena_user_id != user_id:
            raise HTTPException(status_code=403, detail="No tienes permiso para eliminar esta reseña")

        cur.execute("DELETE FROM resenas WHERE id = %s", (resena_id,))
        conn.commit()
        cur.close()
        return {"message": "Reseña eliminada correctamente"}

    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Error al eliminar reseña {resena_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al eliminar reseña: {str(e)}")
    finally:
        if conn:
            conn.close()

# Ruta para crear notificación (interno, no expuesto directamente)
async def crear_notificacion(user_id: int, publicacion_id: int, tipo: str, actor_id: int, mensaje: str = None):
    try:
        logging.debug(f"Creando notificación: user_id={user_id}, publicacion_id={publicacion_id}, tipo={tipo}, actor_id={actor_id}, mensaje={mensaje}")
        
        if tipo not in ['interes', 'comentario']:
            raise HTTPException(status_code=400, detail="Tipo de notificación no válido")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Verificar que la publicación existe y obtener su dueño
            cur.execute("SELECT user_id FROM publicaciones WHERE id = %s", (publicacion_id,))
            publicacion = cur.fetchone()
            if not publicacion:
                raise HTTPException(status_code=404, detail="Publicación no encontrada")

            receptor_id = publicacion[0]
            if receptor_id == actor_id:
                return None  # No creamos notificación si el actor es el dueño

            # Obtener el nombre del actor
            cur.execute("""
                SELECT COALESCE(du.nombre_empresa, u.nombre) AS display_name
                FROM usuarios u
                LEFT JOIN datos_usuario du ON u.id = du.user_id
                WHERE u.id = %s
            """, (actor_id,))
            actor_name = cur.fetchone()
            actor_name = actor_name[0] if actor_name else "Usuario desconocido"

            # Insertar la notificación
            cur.execute("""
                INSERT INTO notifications (user_id, publicacion_id, tipo, leida, fecha_creacion, actor_id, mensaje)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s)
                RETURNING id, fecha_creacion
            """, (receptor_id, publicacion_id, tipo, False, actor_id, mensaje))
            notificacion = cur.fetchone()
            conn.commit()

            return {
                "id": notificacion[0],
                "user_id": receptor_id,
                "publicacion_id": publicacion_id,
                "tipo": tipo,
                "leida": False,
                "fecha_creacion": notificacion[1].strftime("%Y-%m-%d %H:%M:%S"),
                "actor_id": actor_id,
                "nombre_usuario": actor_name,
                "mensaje": mensaje
            }
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Error al crear notificación: {str(e)}")
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logging.error(f"Error al procesar creación de notificación: {e}")
        raise HTTPException(status_code=500, detail=f"Error al crear notificación: {str(e)}")

# Ruta para obtener notificaciones
@router.get("/notificaciones")
async def obtener_notificaciones(request: Request, limit: int = 10, offset: int = 0):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            raise HTTPException(status_code=401, detail="No autorizado")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Obtener notificaciones
            cur.execute("""
                SELECT n.id, n.publicacion_id, n.tipo, n.leida, n.fecha_creacion, n.actor_id, 
                       COALESCE(du.nombre_empresa, u.nombre) AS nombre_usuario, 
                       n.mensaje,
                       CASE 
                           WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                           ELSE 'explorador'
                       END AS tipo_usuario,
                       CASE 
                           WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN %s || n.actor_id
                           ELSE ''
                       END AS foto_perfil_url
                FROM notifications n
                JOIN usuarios u ON n.actor_id = u.id
                LEFT JOIN datos_usuario du ON u.id = du.user_id
                WHERE n.user_id = %s
                ORDER BY n.fecha_creacion DESC
                LIMIT %s OFFSET %s
            """, ("/foto_perfil/", user_id, limit, offset))
            notificaciones = cur.fetchall()

            # Contar notificaciones totales y no leídas
            cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s", (user_id,))
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND leida = FALSE", (user_id,))
            no_leidas = cur.fetchone()[0]

            conn.commit()

            notificaciones_list = [
                {
                    "id": notif[0],
                    "publicacion_id": notif[1],
                    "tipo": notif[2],
                    "leida": notif[3],
                    "fecha_creacion": notif[4].strftime("%Y-%m-%d %H:%M:%S"),
                    "actor_id": notif[5],
                    "nombre_usuario": notif[6] or "Usuario desconocido",
                    "mensaje": notif[7],
                    "tipo_usuario": notif[8],
                    "foto_perfil_url": notif[9]
                }
                for notif in notificaciones
            ]

            return {
                "notificaciones": notificaciones_list,
                "total": total,
                "no_leidas": no_leidas
            }
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Error al obtener notificaciones: {str(e)}")
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logging.error(f"Error al procesar solicitud /notificaciones: {e}")
        raise HTTPException(status_code=500, detail=f"Error al obtener notificaciones: {str(e)}")

# Ruta para marcar notificación como leída
@router.post("/notificaciones/{notificacion_id}/leida")
async def marcar_notificacion_leida(notificacion_id: int, request: Request):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            raise HTTPException(status_code=401, detail="No autorizado")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Verificar que la notificación pertenece al usuario
            cur.execute("SELECT user_id FROM notifications WHERE id = %s", (notificacion_id,))
            notificacion = cur.fetchone()
            if not notificacion:
                raise HTTPException(status_code=404, detail="Notificación no encontrada")
            if notificacion[0] != user_id:
                raise HTTPException(status_code=403, detail="No autorizado para marcar esta notificación")

            # Marcar como leída
            cur.execute("""
                UPDATE notifications 
                SET leida = TRUE 
                WHERE id = %s
            """, (notificacion_id,))
            conn.commit()

            return {"message": "Notificación marcada como leída"}
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Error al marcar notificación: {str(e)}")
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logging.error(f"Error al procesar solicitud /notificaciones/{notificacion_id}/leida: {e}")
        raise HTTPException(status_code=500, detail=f"Error al marcar notificación: {str(e)}")

# Ruta para obtener conteo de notificaciones no leídas
@router.get("/notificaciones/no_leidas")
async def contar_notificaciones_no_leidas(request: Request):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            raise HTTPException(status_code=401, detail="No autorizado")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND leida = FALSE", (user_id,))
            no_leidas = cur.fetchone()[0]
            conn.commit()
            return {"no_leidas": no_leidas}
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Error al contar notificaciones no leídas: {str(e)}")
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logging.error(f"Error al procesar solicitud /notificaciones/no_leidas: {e}")
        raise HTTPException(status_code=500, detail=f"Error al contar notificaciones no leídas: {str(e)}")