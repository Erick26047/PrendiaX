from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException
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

# Ruta para renderizar perfil-especifico.html
@router.get("/perfil-especifico", response_class=HTMLResponse)
async def perfil_especifico(request: Request):
    try:
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning("Sesión no encontrada en /perfil-especifico, redirigiendo a /login")
            return RedirectResponse(url="/login", status_code=302)

        try:
            user_id = int(request.session['user']['id'])
            logging.debug(f"User ID enviado a perfil-especifico.html: {user_id}, tipo: {type(user_id)}")
        except (ValueError, TypeError) as e:
            logging.error(f"Error: user_id no es un entero válido. Valor: {request.session['user']['id']}, Error: {e}")
            request.session.clear()
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
@router.get("/media/{post_id}")
async def get_media(post_id: int, request: Request):
    try:
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning("Sesión no encontrada en /media/{post_id}")
            raise HTTPException(status_code=401, detail="No autorizado")

        user_id = int(request.session['user']['id'])
        logging.debug(f"Obteniendo multimedia para post_id: {post_id}, user_id: {user_id}")
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
            logging.debug(f"Sirviendo imagen para post_id: {post_id}, tamaño: {len(imagen_data)} bytes")
            return StreamingResponse(
                content=io.BytesIO(imagen_data),
                media_type="image/jpeg",
                headers={"Content-Disposition": f"inline; filename=post_{post_id}_image.jpg"}
            )
        elif video_data:
            logging.debug(f"Sirviendo video para post_id: {post_id}, tamaño: {len(video_data)} bytes")
            return StreamingResponse(
                content=io.BytesIO(video_data),
                media_type="video/mp4",
                headers={"Content-Disposition": f"inline; filename=post_{post_id}_video.mp4"}
            )
        else:
            logging.warning(f"Archivo multimedia no encontrado para post_id: {post_id}")
            raise HTTPException(status_code=404, detail="Archivo multimedia no encontrado")
    except Exception as e:
        logging.error(f"Error al servir multimedia para post_id {post_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al servir multimedia: {str(e)}")

# Ruta para renderizar inicio.html
@router.get("/inicio", response_class=HTMLResponse)
async def inicio(request: Request, limit: int = 10, offset: int = 0):
    try:
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning("Sesión no encontrada o sin user_id, redirigiendo a /login")
            return RedirectResponse(url="/login", status_code=302)

        try:
            user_id = int(request.session['user']['id'])
            logging.debug(f"User ID enviado a inicio.html: {user_id}, tipo: {type(user_id)}")
        except (ValueError, TypeError) as e:
            logging.error(f"Error: user_id no es un entero válido. Valor: {request.session['user']['id']}, Error: {e}")
            request.session.clear()
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
                logging.debug("Conexión a la base de datos cerrada")

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

# Ruta para publicar
@router.post("/publicar")
async def publicar(
    request: Request,
    contenido: str = Form(None),
    imagen: UploadFile = File(None),
    video: UploadFile = File(None),
    etiquetas: str = Form(None)
):
    try:
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning("Sesión no encontrada en /publicar, redirigiendo a /login")
            return RedirectResponse(url="/login", status_code=302)

        user_id = int(request.session['user']['id'])
        logging.debug(f"Publicando con user_id: {user_id}")

        if not contenido and (not imagen or imagen.size == 0) and (not video or video.size == 0):
            logging.warning("Intento de publicación sin contenido, imagen ni video")
            raise HTTPException(status_code=400, detail="Debe incluir al menos contenido, una imagen o un video")

        if imagen and imagen.size > MAX_FILE_SIZE:
            logging.warning(f"Imagen demasiado grande, tamaño: {imagen.size}")
            raise HTTPException(status_code=400, detail=f"La imagen excede el tamaño máximo de {MAX_FILE_SIZE // (1024 * 1024)} MB")

        if video and video.size > MAX_FILE_SIZE:
            logging.warning(f"Video demasiado grande, tamaño: {video.size}")
            raise HTTPException(status_code=400, detail=f"El video excede el tamaño máximo de {MAX_FILE_SIZE // (1024 * 1024)} MB")

        if imagen and video:
            logging.warning("No se permite cargar imagen y video al mismo tiempo")
            raise HTTPException(status_code=400, detail="No se puede cargar una imagen y un video al mismo tiempo")

        etiquetas_lista = [e.strip() for e in etiquetas.split(",") if e.strip()] if etiquetas else []
        logging.debug(f"Etiquetas procesadas: {etiquetas_lista}")

        imagen_data = None
        video_data = None
        if imagen and imagen.size > 0:
            if not imagen.content_type.startswith('image/'):
                logging.warning(f"Tipo de archivo no soportado para imagen: {imagen.content_type}")
                raise HTTPException(status_code=400, detail="Solo se permiten imágenes")
            imagen_data = await imagen.read()
            logging.debug(f"Imagen cargada, tamaño: {len(imagen_data)} bytes")

        if video and video.size > 0:
            if not video.content_type.startswith('video/'):
                logging.warning(f"Tipo de archivo no soportado para video: {video.content_type}")
                raise HTTPException(status_code=400, detail="Solo se permiten videos")
            video_data = await video.read()
            logging.debug(f"Video cargado, tamaño: {len(video_data)} bytes")

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute("SELECT id FROM usuarios WHERE id = %s", (user_id,))
            if not cur.fetchone():
                logging.error(f"Usuario no encontrado: {user_id}")
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
            logging.error(f"Error al guardar publicación: {e}")
            raise HTTPException(status_code=500, detail=f"Error al guardar publicación: {str(e)}")
        finally:
            if conn:
                conn.close()
                logging.debug("Conexión a la base de datos cerrada")

        return RedirectResponse(url="/inicio", status_code=302)
    except Exception as e:
        logging.error(f"Error en /publicar: {e}")
        raise HTTPException(status_code=500, detail=f"Error al guardar publicación: {str(e)}")

# Ruta para el feed
@router.get("/feed")
async def feed(limit: int = 10, offset: int = 0, request: Request = None):
    conn = None
    try:
        current_user = None
        if request and 'user' in request.session and 'id' in request.session['user']:
            try:
                current_user = int(request.session['user']['id'])
                logging.debug(f"User_id obtenido de la sesión: {current_user}")
            except (ValueError, TypeError) as e:
                logging.error(f"Error al obtener user_id de la sesión: {e}")
                current_user = None

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
        logging.debug(f"Se obtuvieron {len(publicaciones)} publicaciones para el feed")
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
        logging.debug(f"Feed retornado: {publicaciones_list}")
        return publicaciones_list
    except Exception as e:
        logging.error(f"Error al cargar feed: {e}")
        raise HTTPException(status_code=500, detail=f"Error al cargar feed: {str(e)}")
    finally:
        if conn:
            conn.close()
            logging.debug("Conexión a la base de datos cerrada")

# Ruta para buscar publicaciones
@router.get("/search")
async def search_publicaciones(query: str, limit: int = 10, offset: int = 0, request: Request = None):
    query = query.strip().lower()
    if not query:
        logging.warning("Query vacío en /search")
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    conn = None
    try:
        current_user = None
        if request and 'user' in request.session and 'id' in request.session['user']:
            try:
                current_user = int(request.session['user']['id'])
                logging.debug(f"User_id obtenido de la sesión para búsqueda: {current_user}")
            except (ValueError, TypeError) as e:
                logging.error(f"Error al obtener user_id de la sesión: {e}")
                current_user = None

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
        logging.debug(f"Se obtuvieron {len(publicaciones)} resultados para query: {query}")
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
        logging.debug(f"Resultados de búsqueda para query '{query}': {publicaciones_list}")
        return publicaciones_list
    except Exception as e:
        logging.error(f"Error al buscar publicaciones: {e}")
        raise HTTPException(status_code=500, detail=f"Error al buscar publicaciones: {str(e)}")
    finally:
        if conn:
            conn.close()
            logging.debug("Conexión a la base de datos cerrada")

# Ruta para el feed del perfil
@router.get("/perfil/feed")
async def perfil_feed(request: Request, limit: int = 10, offset: int = 0):
    try:
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning("Sesión no encontrada en /perfil/feed, retornando error")
            raise HTTPException(status_code=401, detail="No autorizado")

        try:
            user_id = int(request.session['user']['id'])
            logging.debug(f"Obteniendo feed para user_id: {user_id}, tipo: {type(user_id)}")
        except (ValueError, TypeError) as e:
            logging.error(f"Error: user_id no es un entero válido. Valor: {request.session['user']['id']}, Error: {e}")
            raise HTTPException(status_code=400, detail="ID de usuario inválido")

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
            logging.debug(f"Se obtuvieron {len(publicaciones)} publicaciones para el perfil de user_id: {user_id}")
            cur.close()
        except Exception as e:
            logging.error(f"Error al obtener publicaciones del perfil: {e}")
            raise HTTPException(status_code=500, detail="Error al obtener publicaciones")
        finally:
            if conn:
                conn.close()
                logging.debug("Conexión a la base de datos cerrada")

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
        logging.debug(f"Feed del perfil retornado para user_id {user_id}: {publicaciones_list}")
        return publicaciones_list
    except Exception as e:
        logging.error(f"Error en /perfil/feed: {e}")
        raise HTTPException(status_code=500, detail=f"Error al cargar feed del perfil: {str(e)}")

# Ruta para obtener una publicación específica
@router.get("/publicacion/{post_id}")
async def get_publicacion(post_id: int, request: Request):
    try:
        current_user = None
        if request and 'user' in request.session and 'id' in request.session['user']:
            try:
                current_user = int(request.session['user']['id'])
                logging.debug(f"User_id obtenido de la sesión: {current_user}")
            except (ValueError, TypeError) as e:
                logging.error(f"Error al obtener user_id de la sesión: {e}")
                current_user = None

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
                logging.warning(f"Publicación no encontrada: {post_id}")
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
            logging.debug(f"Publicación retornada para post_id {post_id}: {publicacion}")
            return publicacion
        except Exception as e:
            logging.error(f"Error al obtener publicación {post_id}: {e}")
            raise HTTPException(status_code=500, detail="Error al obtener publicación")
        finally:
            if conn:
                conn.close()
                logging.debug("Conexión a la base de datos cerrada")
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
            logging.warning(f"Usuario no encontrado: {user_id}")
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
        logging.debug(f"Datos del usuario retornados para user_id {user_id}: {user_data}")
        return user_data
    except Exception as e:
        logging.error(f"Error al obtener datos del usuario para user_id {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener datos del usuario")
    finally:
        if conn:
            conn.close()
            logging.debug("Conexión a la base de datos cerrada")

# Ruta para obtener publicaciones de un usuario
@router.get("/user/{user_id}/publicaciones")
async def get_user_publicaciones(user_id: int, limit: int = 10, offset: int = 0, request: Request = None):
    conn = None
    try:
        current_user = None
        if request and 'user' in request.session and 'id' in request.session['user']:
            try:
                current_user = int(request.session['user']['id'])
                logging.debug(f"User_id obtenido de la sesión: {current_user}")
            except (ValueError, TypeError) as e:
                logging.error(f"Error al obtener user_id de la sesión: {e}")
                current_user = None

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
        logging.debug(f"Se obtuvieron {len(publicaciones)} publicaciones para user_id: {user_id}")
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
        logging.debug(f"Publicaciones retornadas para user_id {user_id}: {publicaciones_list}")
        return publicaciones_list
    except Exception as e:
        logging.error(f"Error al obtener publicaciones para user_id {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener publicaciones")
    finally:
        if conn:
            conn.close()
            logging.debug("Conexión a la base de datos cerrada")

# Ruta para obtener reseñas
@router.get("/api/perfil/{perfil_id}/resenas")
async def get_user_resenas(perfil_id: int, request: Request, limit: int = 10, offset: int = 0):
    try:
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning("Sesión no encontrada en /api/perfil/{perfil_id}/resenas")
            raise HTTPException(status_code=401, detail="No autorizado")

        try:
            user_id = int(request.session['user']['id'])
            logging.debug(f"Obteniendo reseñas para perfil_id: {perfil_id}, solicitado por user_id: {user_id}")
        except (ValueError, TypeError) as e:
            logging.error(f"Error al convertir user_id a entero: {request.session['user']['id']}, Error: {e}")
            raise HTTPException(status_code=400, detail=f"ID de usuario inválido: {str(e)}")

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute("SELECT id FROM usuarios WHERE id = %s", (perfil_id,))
            if not cur.fetchone():
                logging.warning(f"Perfil no encontrado: {perfil_id}")
                raise HTTPException(status_code=404, detail="Perfil no encontrado")

            cur.execute("""
                SELECT r.id, r.user_id, r.perfil_id, r.texto, r.calificacion, r.fecha_creacion,
                       COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                       CASE 
                           WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                           ELSE 'explorador'
                       END AS tipo_usuario
                FROM resenas r
                JOIN usuarios u ON r.user_id = u.id
                LEFT JOIN datos_usuario du ON r.user_id = du.user_id
                WHERE r.perfil_id = %s
                ORDER BY r.fecha_creacion DESC
                LIMIT %s OFFSET %s
            """, (perfil_id, limit, offset))
            resenas = cur.fetchall()
            logging.debug(f"Se obtuvieron {len(resenas)} reseñas para perfil_id: {perfil_id}")

            cur.close()

            resenas_list = [
                {
                    "id": row[0],
                    "user_id": int(row[1]),
                    "perfil_id": int(row[2]),
                    "texto": row[3],
                    "calificacion": row[4],
                    "fecha_creacion": row[5].strftime("%Y-%m-%d %H:%M:%S"),
                    "nombre_empresa": row[6] or "Anónimo",
                    "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[7] == 'emprendedor' else "",
                    "tipo_usuario": row[7]
                }
                for row in resenas
            ]
            logging.debug(f"Reseñas retornadas para perfil_id {perfil_id}: {resenas_list}")
            return resenas_list
        except psycopg2.Error as e:
            logging.error(f"Error en la base de datos para perfil_id {perfil_id}: {e.pgcode} - {e.pgerror}")
            raise HTTPException(status_code=500, detail=f"Error en la base de datos: {str(e)}")
        finally:
            if conn:
                conn.close()
                logging.debug("Conexión a la base de datos cerrada")
    except Exception as e:
        logging.error(f"Error general en /api/perfil/{perfil_id}/resenas: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al cargar reseñas: {str(e)}")

# Ruta para crear reseña
@router.post("/api/perfil/{perfil_id}/resenas")
async def create_review(perfil_id: int, request: ReviewRequest, http_request: Request):
    conn = None
    try:
        if 'user' not in http_request.session or 'id' not in http_request.session['user']:
            logging.warning("Sesión no encontrada en /api/perfil/{perfil_id}/resenas")
            raise HTTPException(status_code=401, detail="Debes iniciar sesión para dejar una reseña")

        user_id = int(http_request.session['user']['id'])
        texto = request.texto.strip()
        calificacion = request.calificacion

        if user_id == perfil_id:
            logging.warning(f"Usuario {user_id} intentó dejar una reseña en su propio perfil")
            raise HTTPException(status_code=400, detail="No puedes dejar una reseña en tu propio perfil")

        if not texto:
            logging.warning("Reseña vacía en /api/perfil/{perfil_id}/resenas")
            raise HTTPException(status_code=400, detail="El comentario no puede estar vacío")

        if not (1 <= calificacion <= 5):
            logging.warning(f"Calificación inválida: {calificacion}")
            raise HTTPException(status_code=400, detail="La calificación debe estar entre 1 y 5")

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT id FROM usuarios WHERE id = %s", (perfil_id,))
        if not cur.fetchone():
            logging.warning(f"Perfil no encontrado: {perfil_id}")
            raise HTTPException(status_code=404, detail="Perfil no encontrado")

        cur.execute("""
            INSERT INTO resenas (user_id, perfil_id, texto, calificacion, fecha_creacion)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            RETURNING id, user_id, perfil_id, texto, calificacion, fecha_creacion
        """, (user_id, perfil_id, texto, calificacion))
        review = cur.fetchone()
        conn.commit()

        cur.execute("""
            SELECT COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                   CASE 
                       WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                       ELSE 'explorador'
                   END AS tipo_usuario
            FROM usuarios u
            LEFT JOIN datos_usuario du ON u.id = du.user_id
            WHERE u.id = %s
        """, (user_id,))
        autor_data = cur.fetchone()
        display_name = autor_data[0]
        tipo_usuario = autor_data[1]

        cur.close()

        review_dict = {
            "id": review[0],
            "user_id": review[1],
            "perfil_id": review[2],
            "texto": review[3],
            "calificacion": review[4],
            "fecha_creacion": review[5].strftime("%Y-%m-%d %H:%M:%S"),
            "nombre_empresa": display_name or "Anónimo",
            "foto_perfil_url": f"/foto_perfil/{user_id}" if tipo_usuario == 'emprendedor' else "",
            "tipo_usuario": tipo_usuario
        }

        logging.debug(f"Reseña creada: {review_dict}")
        return review_dict
    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        logging.error(f"Error en la base de datos al crear reseña para perfil_id {perfil_id}: {e.pgcode} - {e.pgerror}")
        raise HTTPException(status_code=500, detail=f"Error en la base de datos: {str(e)}")
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Error al crear reseña para perfil_id {perfil_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al crear reseña: {str(e)}")
    finally:
        if conn:
            conn.close()
            logging.debug("Conexión a la base de datos cerrada")

# Ruta para obtener usuario actual
@router.get("/current_user")
async def get_current_user(request: Request):
    try:
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning("Sesión no encontrada en /current_user, retornando null")
            return {"user_id": None, "tipo": None}
        
        try:
            user_id = int(request.session['user']['id'])
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
                logging.debug(f"Tipo de usuario determinado: {user_tipo} para user_id: {user_id}")
            except Exception as e:
                logging.error(f"Error al determinar el tipo de usuario en /current_user: {e}")
                user_tipo = 'explorador'
            finally:
                if conn:
                    conn.close()
                    logging.debug("Conexión a la base de datos cerrada")

            logging.debug(f"User ID retornado por /current_user: {user_id}, tipo: {user_tipo}")
            return {"user_id": user_id, "tipo": user_tipo}
        except (ValueError, TypeError) as e:
            logging.error(f"Error: user_id no es un entero válido en /current_user. Valor: {request.session['user']['id']}, Error: {e}")
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
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning("Sesión no encontrada en /borrar_publicacion, retornando error")
            raise HTTPException(status_code=401, detail="No autorizado")

        user_id = int(request.session['user']['id'])
        logging.debug(f"Intento de borrar publicación {post_id} por user_id: {user_id}")

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute("SELECT user_id FROM publicaciones WHERE id = %s", (post_id,))
            result = cur.fetchone()
            if not result:
                logging.warning(f"Publicación no encontrada: {post_id}")
                raise HTTPException(status_code=404, detail="Publicación no encontrada")
            if result[0] != user_id:
                logging.warning(f"Usuario {user_id} no tiene permiso para borrar la publicación {post_id}")
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
                logging.debug("Conexión a la base de datos cerrada")
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
            logging.warning(f"Publicación no encontrada: {post_id}")
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
        logging.debug(f"Se obtuvieron {len(comentarios)} comentarios para la publicación {post_id}")

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
            logging.debug("Conexión a la base de datos cerrada")

# Ruta para publicar comentario
@router.post("/publicacion/{post_id}/comentar")
async def post_comment(post_id: int, request: CommentRequest, http_request: Request):
    conn = None
    try:
        if 'user' not in http_request.session or 'id' not in http_request.session['user']:
            logging.warning("Sesión no encontrada en /publicacion/{post_id}/comentar")
            raise HTTPException(status_code=401, detail="Debes iniciar sesión para comentar")

        user_id = int(http_request.session['user']['id'])
        logging.debug(f"User_id intentando comentar: {user_id}")

        contenido = request.contenido.strip()
        if not contenido:
            logging.warning(f"Comentario vacío en /publicacion/{post_id}/comentar")
            raise HTTPException(status_code=400, detail="El comentario no puede estar vacío")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM publicaciones WHERE id = %s", (post_id,))
        if not cur.fetchone():
            logging.warning(f"Publicación no encontrada: {post_id}")
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
            user_id=user_id,  # No usado directamente, se obtiene el dueño de la publicación internamente
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

        logging.debug(f"Comentario creado: {comment_dict}")
        return comment_dict
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Error al crear comentario para la publicación {post_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al crear comentario: {str(e)}")
    finally:
        if conn:
            conn.close()
            logging.debug("Conexión a la base de datos cerrada")

# Ruta para gestionar interés
@router.post("/publicacion/{post_id}/interesar")
async def toggle_interest(post_id: int, request: InterestRequest, http_request: Request):
    conn = None
    try:
        if 'user' not in http_request.session or 'id' not in http_request.session['user']:
            logging.warning("Sesión no encontrada en /publicacion/{post_id}/interesar")
            raise HTTPException(status_code=401, detail="No autorizado")

        user_id = int(http_request.session['user']['id'])
        if user_id != request.user_id:
            logging.warning(f"Discrepancia de user_id: session={user_id}, request={request.user_id}")
            raise HTTPException(status_code=403, detail="ID de usuario no coincide con la sesión")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM publicaciones WHERE id = %s", (post_id,))
        if not cur.fetchone():
            logging.warning(f"Publicación no encontrada: {post_id}")
            raise HTTPException(status_code=404, detail="Publicación no encontrada")

        cur.execute("SELECT id FROM usuarios WHERE id = %s", (user_id,))
        if not cur.fetchone():
            logging.warning(f"Usuario no encontrado: {user_id}")
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

        cur.execute("""
            SELECT id FROM intereses WHERE publicacion_id = %s AND user_id = %s
        """, (post_id, user_id))
        existing_interest = cur.fetchone()

        if existing_interest:
            cur.execute("""
                DELETE FROM intereses WHERE publicacion_id = %s AND user_id = %s
            """, (post_id, user_id))
            logging.debug(f"Interés eliminado para publicación {post_id} por user_id: {user_id}")
        else:
            cur.execute("""
                INSERT INTO intereses (publicacion_id, user_id, fecha_creacion)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
            """, (post_id, user_id))
            logging.debug(f"Interés añadido para publicación {post_id} por user_id: {user_id}")
            # Crear notificación
            await crear_notificacion(
                user_id=user_id,  # No usado directamente, se obtiene el dueño de la publicación internamente
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
    except HTTPException as he:
        raise he
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Error al gestionar interés para publicación {post_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al gestionar interés: {str(e)}")
    finally:
        if conn:
            conn.close()
            logging.debug("Conexión a la base de datos cerrada")

# Ruta para borrar comentario
@router.delete("/borrar_comentario/{comentario_id}")
async def borrar_comentario(comentario_id: int, request: Request):
    conn = None
    try:
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning("Sesión no encontrada en /borrar_comentario")
            raise HTTPException(status_code=401, detail="No autorizado")

        user_id = int(request.session['user']['id'])
        logging.debug(f"Intentando borrar comentario_id: {comentario_id}, user_id: {user_id}")

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT user_id, publicacion_id FROM comentarios WHERE id = %s
        """, (comentario_id,))
        comment = cur.fetchone()
        if not comment:
            logging.warning(f"Comentario no encontrado: {comentario_id}")
            raise HTTPException(status_code=404, detail="Comentario no encontrado")

        if comment[0] != user_id:
            logging.warning(f"No autorizado para borrar comentario {comentario_id}, user_id {user_id}")
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
            logging.debug("Conexión a la base de datos cerrada")

# Ruta para eliminar reseña
@router.delete("/api/perfil/{perfil_id}/resenas/{resena_id}")
async def delete_review(perfil_id: int, resena_id: int, request: Request):
    conn = None
    try:
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning(f"Sesión no encontrada en /api/perfil/{perfil_id}/resenas/{resena_id}")
            raise HTTPException(status_code=401, detail="Debes iniciar sesión para eliminar una reseña")

        user_id = int(request.session['user']['id'])
        logging.debug(f"Intentando eliminar reseña {resena_id} para perfil_id: {perfil_id} por user_id: {user_id}")

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, user_id, perfil_id, texto, calificacion, fecha_creacion 
            FROM resenas 
            WHERE id = %s
        """, (resena_id,))
        resena = cur.fetchone()
        logging.debug(f"Resultado de la consulta para reseña id={resena_id}: {resena}")

        if not resena:
            logging.warning(f"Reseña {resena_id} no encontrada")
            raise HTTPException(status_code=404, detail="Reseña no encontrada")

        resena_id_db, resena_user_id, resena_perfil_id, texto, calificacion, fecha_creacion = resena
        logging.debug(f"Reseña {resena_id} encontrada: id={resena_id_db}, user_id={resena_user_id}, perfil_id={resena_perfil_id}, texto={texto}, calificacion={calificacion}")

        if resena_user_id != user_id:
            logging.warning(f"Usuario {user_id} intentó eliminar reseña {resena_id} que no le pertenece")
            raise HTTPException(status_code=403, detail="No tienes permiso para eliminar esta reseña")

        if resena_perfil_id != perfil_id:
            logging.warning(f"Advertencia: Reseña {resena_id} no está asociada a perfil_id {perfil_id}, encontrado perfil_id {resena_perfil_id}. Procediendo con eliminación.")

        cur.execute("DELETE FROM resenas WHERE id = %s", (resena_id,))
        conn.commit()

        cur.close()
        logging.debug(f"Reseña {resena_id} eliminada correctamente")
        return {"message": "Reseña eliminada correctamente"}

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        logging.error(f"Error en la base de datos al eliminar reseña {resena_id}: {e.pgcode} - {e.pgerror}")
        raise HTTPException(status_code=500, detail=f"Error en la base de datos: {str(e)}")
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Error al eliminar reseña {resena_id} para perfil_id {perfil_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al eliminar reseña: {str(e)}")
    finally:
        if conn:
            conn.close()
            logging.debug("Conexión a la base de datos cerrada")

# Ruta para crear notificación (interno, no expuesto directamente)
async def crear_notificacion(user_id: int, publicacion_id: int, tipo: str, actor_id: int, mensaje: str = None):
    try:
        logging.debug(f"Creando notificación: user_id={user_id}, publicacion_id={publicacion_id}, tipo={tipo}, actor_id={actor_id}, mensaje={mensaje}")
        
        if tipo not in ['interes', 'comentario']:
            logging.warning(f"Tipo de notificación no válido: {tipo}")
            raise HTTPException(status_code=400, detail="Tipo de notificación no válido")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Verificar que la publicación existe y obtener su dueño
            cur.execute("SELECT user_id FROM publicaciones WHERE id = %s", (publicacion_id,))
            publicacion = cur.fetchone()
            if not publicacion:
                logging.warning(f"Publicación no encontrada: publicacion_id={publicacion_id}")
                raise HTTPException(status_code=404, detail="Publicación no encontrada")

            receptor_id = publicacion[0]
            if receptor_id == actor_id:
                logging.warning(f"Intento de notificación propia: receptor_id={receptor_id}, actor_id={actor_id}")
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

            logging.debug(f"Notificación creada: id={notificacion[0]}, receptor_id={receptor_id}, publicacion_id={publicacion_id}, tipo={tipo}, actor_id={actor_id}")
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
            logging.error(f"Error al crear notificación: {e}")
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
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning("Sesión no encontrada en /notificaciones")
            raise HTTPException(status_code=401, detail="No autorizado")

        user_id = int(request.session['user']['id'])
        logging.debug(f"Obteniendo notificaciones para user_id={user_id}, limit={limit}, offset={offset}")

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
            logging.error(f"Error al obtener notificaciones: {e}")
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
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning("Sesión no encontrada en /notificaciones/{notificacion_id}/leida")
            raise HTTPException(status_code=401, detail="No autorizado")

        user_id = int(request.session['user']['id'])
        logging.debug(f"Marcando notificación {notificacion_id} como leída para user_id={user_id}")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Verificar que la notificación pertenece al usuario
            cur.execute("SELECT user_id FROM notifications WHERE id = %s", (notificacion_id,))
            notificacion = cur.fetchone()
            if not notificacion:
                logging.warning(f"Notificación no encontrada: {notificacion_id}")
                raise HTTPException(status_code=404, detail="Notificación no encontrada")
            if notificacion[0] != user_id:
                logging.warning(f"Usuario {user_id} no autorizado para marcar notificación {notificacion_id}")
                raise HTTPException(status_code=403, detail="No autorizado para marcar esta notificación")

            # Marcar como leída
            cur.execute("""
                UPDATE notifications 
                SET leida = TRUE 
                WHERE id = %s
            """, (notificacion_id,))
            conn.commit()

            logging.debug(f"Notificación {notificacion_id} marcada como leída")
            return {"message": "Notificación marcada como leída"}
        except Exception as e:
            conn.rollback()
            logging.error(f"Error al marcar notificación como leída: {e}")
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
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning("Sesión no encontrada en /notificaciones/no_leidas")
            raise HTTPException(status_code=401, detail="No autorizado")

        user_id = int(request.session['user']['id'])
        logging.debug(f"Contando notificaciones no leídas para user_id={user_id}")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND leida = FALSE", (user_id,))
            no_leidas = cur.fetchone()[0]
            conn.commit()
            logging.debug(f"Notificaciones no leídas para user_id={user_id}: {no_leidas}")
            return {"no_leidas": no_leidas}
        except Exception as e:
            conn.rollback()
            logging.error(f"Error al contar notificaciones no leídas: {e}")
            raise HTTPException(status_code=500, detail=f"Error al contar notificaciones no leídas: {str(e)}")
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logging.error(f"Error al procesar solicitud /notificaciones/no_leidas: {e}")
        raise HTTPException(status_code=500, detail=f"Error al contar notificaciones no leídas: {str(e)}")