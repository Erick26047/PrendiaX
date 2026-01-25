from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException, Header, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse, StreamingResponse, Response
from fastapi.templating import Jinja2Templates
from typing import List, Dict
import psycopg2
from datetime import datetime
import logging
import io
import re
import json # <--- Agregado para enviar mensajes JSON
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

# --- GESTOR DE WEBSOCKETS (AGREGADO) ---
# Esta clase maneja las conexiones activas para enviar notificaciones en vivo
class NotificationManager:
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)
        logging.debug(f"Usuario {user_id} conectado a WS Notificaciones")

    def disconnect(self, websocket: WebSocket, user_id: int):
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
        logging.debug(f"Usuario {user_id} desconectado de WS Notificaciones")

    async def send_personal_message(self, message: dict, user_id: int):
        if user_id in self.active_connections:
            for connection in self.active_connections[user_id]:
                try:
                    await connection.send_text(json.dumps(message))
                except Exception as e:
                    logging.error(f"Error enviando WS a {user_id}: {e}")

# Instancia global
notification_manager = NotificationManager()

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

# --- MODELOS PYDANTIC ACTUALIZADOS ---
class InterestRequest(BaseModel):
    user_id: int

class CommentRequest(BaseModel):
    contenido: str
    parent_id: int | None = None       # ID del comentario padre (para hilos)
    reply_to_user_id: int | None = None # ID del usuario al que se responde (para menciones)

class ReviewRequest(BaseModel):
    texto: str
    calificacion: int
    # --- AGREGAR ESTO JUNTO A TUS OTROS MODELOS (Línea ~85 aprox) ---
class ReportePublicacionRequest(BaseModel):
    publicacion_id: int
    motivo: str

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

# --- NOTIFICACIONES INTELIGENTES (MODIFICADO PARA USAR WEBSOCKET) ---
async def crear_notificacion(publicacion_id: int, tipo: str, actor_id: int, mensaje: str = None, target_user_id: int = None, comentario_id: int = None):
    """
    Crea una notificación gestionando la lógica de destinatarios Y LA ENVÍA POR WEBSOCKET
    """
    try:
        logging.debug(f"Creando notificación: post={publicacion_id}, tipo={tipo}, actor={actor_id}, target={target_user_id}")
        
        if tipo not in ['interes', 'comentario', 'respuesta', 'mencion']:
            raise HTTPException(status_code=400, detail="Tipo de notificación no válido")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # 1. Definir quién es el receptor
            receptor_id = target_user_id

            # Si no es explícito, buscamos al dueño de la publicación
            if not receptor_id:
                cur.execute("SELECT user_id FROM publicaciones WHERE id = %s", (publicacion_id,))
                publicacion = cur.fetchone()
                if not publicacion:
                    raise HTTPException(status_code=404, detail="Publicación no encontrada")
                receptor_id = publicacion[0]

            # Evitar notificarse a uno mismo
            if receptor_id == actor_id:
                return None 

            # 2. Obtener nombre del actor (para logs o historial rápido)
            cur.execute("""
                SELECT COALESCE(du.nombre_empresa, u.nombre) AS display_name
                FROM usuarios u
                LEFT JOIN datos_usuario du ON u.id = du.user_id
                WHERE u.id = %s
            """, (actor_id,))
            actor_name = cur.fetchone()
            actor_name = actor_name[0] if actor_name else "Usuario desconocido"

            # 3. Insertar la notificación en BD
            cur.execute("""
                INSERT INTO notifications (user_id, publicacion_id, tipo, leida, fecha_creacion, actor_id, mensaje, comentario_id)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s)
                RETURNING id, fecha_creacion
            """, (receptor_id, publicacion_id, tipo, False, actor_id, mensaje, comentario_id))
            notificacion = cur.fetchone()
            conn.commit()

            # 4. ENVIAR POR WEBSOCKET (¡ESTO ES LO QUE FALTABA EN EL ORIGINAL!)
            payload = {
                "id": notificacion[0],
                "user_id": receptor_id,
                "publicacion_id": publicacion_id,
                "tipo": tipo,
                "leida": False,
                "fecha_creacion": notificacion[1].strftime("%Y-%m-%d %H:%M:%S"),
                "actor_id": actor_id,
                "nombre_usuario": actor_name,
                "mensaje": mensaje,
                "comentario_id": comentario_id
            }
            await notification_manager.send_personal_message(payload, receptor_id)

            return payload

        except Exception as e:
            conn.rollback()
            # Logueamos pero no rompemos el flujo principal
            logging.error(f"Error interno creando notificación: {str(e)}")
            return None
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logging.error(f"Error general en crear_notificacion: {e}")
        return None


# Ruta para renderizar perfil-especifico.html
@router.get("/perfil-especifico", response_class=HTMLResponse)
async def perfil_especifico(request: Request):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            return RedirectResponse(url="/login", status_code=302)

        viewed_user_id = request.query_params.get('user_id')
        if not viewed_user_id or not viewed_user_id.isdigit():
            raise HTTPException(status_code=400, detail="ID de usuario inválido en la URL")

        viewed_user_id = int(viewed_user_id)
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
            raise HTTPException(status_code=404, detail="Foto de perfil no disponible para exploradores")

        cur.execute("SELECT foto FROM datos_usuario WHERE user_id = %s", (user_id,))
        result = cur.fetchone()
        cur.close()

        if not result or not result[0]:
            raise HTTPException(status_code=404, detail="Foto de perfil no encontrada")

        foto_data = result[0]
        return StreamingResponse(io.BytesIO(foto_data), media_type="image/jpeg")
    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Error al obtener foto de perfil para user_id {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener foto de perfil")
    finally:
        if conn: conn.close()

# Ruta GET MEDIA (Soporte Ranges para Video)
@router.get("/media/{post_id}")
def get_media(post_id: int, request: Request):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT imagen, video FROM publicaciones WHERE id = %s", (post_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()

        if not result:
            raise HTTPException(status_code=404, detail="Publicación no encontrada")

        imagen_data, video_data = result
        
        # --- IMAGEN ---
        if imagen_data:
            return StreamingResponse(
                content=io.BytesIO(imagen_data),
                media_type="image/jpeg",
                headers={"Content-Disposition": f"inline; filename=post_{post_id}_image.jpg"}
            )
        
        # --- VIDEO (Con Rangos) ---
        elif video_data:
            file_size = len(video_data)
            range_header = request.headers.get("range")
            headers = {
                "Accept-Ranges": "bytes",
                "Content-Disposition": f"inline; filename=post_{post_id}_video.mp4"
            }

            if not range_header:
                headers["Content-Length"] = str(file_size)
                return StreamingResponse(
                    content=io.BytesIO(video_data),
                    media_type="video/mp4",
                    headers=headers,
                    status_code=200
                )

            try:
                range_match = re.search(r'bytes=(\d+)-(\d*)', range_header)
                if not range_match: raise ValueError("Rango inválido")
                start = int(range_match.group(1))
                end = int(range_match.group(2)) if range_match.group(2) else file_size - 1
            except ValueError:
                start = 0
                end = file_size - 1

            if start >= file_size:
                headers["Content-Range"] = f"bytes */{file_size}"
                return Response(status_code=416, headers=headers)

            end = min(end, file_size - 1)
            chunk_length = end - start + 1
            chunk_data = video_data[start : end + 1]

            headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            headers["Content-Length"] = str(chunk_length)
            headers["Content-Type"] = "video/mp4"

            return StreamingResponse(
                io.BytesIO(chunk_data),
                status_code=206,
                headers=headers,
                media_type="video/mp4"
            )
        else:
            raise HTTPException(status_code=404, detail="Archivo multimedia no encontrado")
    except Exception as e:
        if conn and not conn.closed: conn.close()
        logging.error(f"Error media post {post_id}: {e}")
        raise HTTPException(status_code=500, detail="Error interno al servir media")

# Ruta INICIO
@router.get("/inicio", response_class=HTMLResponse)
async def inicio(request: Request, limit: int = 10, offset: int = 0):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            return RedirectResponse(url="/login", status_code=302)

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT p.id, p.user_id, p.contenido, p.etiquetas, p.fecha_creacion, 
                    COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                    CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END AS tipo_usuario,
                    p.imagen IS NOT NULL AS has_imagen,
                    p.video IS NOT NULL AS has_video,
                    COUNT(DISTINCT i.user_id) AS interesados_count,
                    EXISTS (SELECT 1 FROM intereses i WHERE i.publicacion_id = p.id AND i.user_id = %s) AS interesado,
                    (SELECT COUNT(*) FROM comentarios c WHERE c.publicacion_id = p.id) AS comentarios_count
                FROM publicaciones p
                JOIN usuarios u ON p.user_id = u.id
                LEFT JOIN datos_usuario du ON p.user_id = du.user_id
                LEFT JOIN intereses i ON p.id = i.publicacion_id
                GROUP BY p.id, p.user_id, p.contenido, p.etiquetas, p.fecha_creacion, du.nombre_empresa, u.nombre, du.categoria
                ORDER BY p.fecha_creacion DESC
                LIMIT %s OFFSET %s
            """, (user_id, limit, offset))
            publicaciones = cur.fetchall()
            cur.close()
        finally:
            if conn: conn.close()

        publicaciones_list = [
            {
                "id": row[0],
                "user_id": int(row[1]),
                "contenido": row[2] or "",
                "imagen_url": f"/media/{row[0]}" if row[7] else "",
                "video_url": f"/media/{row[0]}" if row[8] else "",
                "etiquetas": row[3] or [],
                "fecha_creacion": row[4].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[6] == 'emprendedor' else "",
                "nombre_empresa": row[5],
                "tipo_usuario": row[6],
                "interesados_count": int(row[9]),
                "interesado": row[10],
                "comentarios_count": int(row[11])
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

# Ruta PUBLICAR
@router.post("/publicar")
async def publicar(request: Request, contenido: str = Form(None), imagen: UploadFile = File(None), video: UploadFile = File(None), etiquetas: str = Form(None)):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            if request.headers.get("Authorization"): raise HTTPException(status_code=401, detail="No autorizado")
            return RedirectResponse(url="/login", status_code=302)

        if not contenido and (not imagen or imagen.size == 0) and (not video or video.size == 0):
            raise HTTPException(status_code=400, detail="Debe incluir contenido")

        if imagen and imagen.size > MAX_FILE_SIZE: raise HTTPException(status_code=400, detail="Imagen muy pesada")
        if video and video.size > MAX_FILE_SIZE: raise HTTPException(status_code=400, detail="Video muy pesado")
        if imagen and video: raise HTTPException(status_code=400, detail="Solo imagen o video, no ambos")

        etiquetas_lista = [e.strip() for e in etiquetas.split(",") if e.strip()] if etiquetas else []

        imagen_data = await imagen.read() if imagen and imagen.size > 0 else None
        video_data = await video.read() if video and video.size > 0 else None

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO publicaciones (user_id, contenido, imagen, video, etiquetas, fecha_creacion)
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING id
            """, (user_id, contenido, psycopg2.Binary(imagen_data) if imagen_data else None, psycopg2.Binary(video_data) if video_data else None, etiquetas_lista))
            conn.commit()
            cur.close()
        finally:
            if conn: conn.close()

        return RedirectResponse(url="/inicio", status_code=302)
    except Exception as e:
        logging.error(f"Error en /publicar: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
    # =================================================================
# 🚀 REPORTAR PUBLICACIÓN (NUEVO)
# =================================================================
@router.post("/api/reportar/publicacion")
async def reportar_publicacion(request: Request, reporte: ReportePublicacionRequest):
    conn = None
    try:
        # 1. Identificar al usuario (App o Web)
        user_id = get_user_id_hybrid(request)
        if not user_id:
            raise HTTPException(status_code=401, detail="Debes iniciar sesión para reportar.")

        conn = get_db_connection()
        cur = conn.cursor()

        # 2. Verificar que la publicación exista
        cur.execute("SELECT id FROM publicaciones WHERE id = %s", (reporte.publicacion_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="La publicación no existe.")

        # 3. Evitar reportes duplicados (Opcional pero recomendado)
        # Revisa si este usuario ya reportó este post hoy para no llenar la BD de spam
        cur.execute("""
            SELECT id FROM reportes_publicaciones 
            WHERE denunciante_id = %s AND publicacion_id = %s AND estatus = 'pendiente'
        """, (user_id, reporte.publicacion_id))
        
        if cur.fetchone():
            return JSONResponse(content={"status": "ok", "message": "Ya has reportado esta publicación anteriormente."})

        # 4. Insertar el reporte
        cur.execute("""
            INSERT INTO reportes_publicaciones (denunciante_id, publicacion_id, motivo, estatus, fecha_reporte)
            VALUES (%s, %s, %s, 'pendiente', CURRENT_TIMESTAMP)
        """, (user_id, reporte.publicacion_id, reporte.motivo))
        
        conn.commit()
        
        logging.info(f"Usuario {user_id} reportó publicación {reporte.publicacion_id} por: {reporte.motivo}")
        
        return JSONResponse(content={"status": "ok", "message": "Reporte enviado. Gracias por ayudarnos."})

    except HTTPException as he:
        raise he
    except Exception as e:
        if conn: conn.rollback()
        logging.error(f"Error al reportar publicación: {e}")
        raise HTTPException(status_code=500, detail="Error interno al procesar el reporte.")
    finally:
        if conn: conn.close()

# Ruta FEED JSON
@router.get("/feed")
async def feed(limit: int = 10, offset: int = 0, request: Request = None):
    conn = None
    try:
        current_user = get_user_id_hybrid(request) if request else -1
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.user_id, p.contenido, p.etiquetas, p.fecha_creacion, 
                COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END AS tipo_usuario,
                p.imagen IS NOT NULL AS has_imagen,
                p.video IS NOT NULL AS has_video,
                COUNT(DISTINCT i.user_id) AS interesados_count,
                EXISTS (SELECT 1 FROM intereses i WHERE i.publicacion_id = p.id AND i.user_id = %s) AS interesado,
                (SELECT COUNT(*) FROM comentarios c WHERE c.publicacion_id = p.id) AS comentarios_count
            FROM publicaciones p
            JOIN usuarios u ON p.user_id = u.id
            LEFT JOIN datos_usuario du ON p.user_id = du.user_id
            LEFT JOIN intereses i ON p.id = i.publicacion_id
            GROUP BY p.id, p.user_id, p.contenido, p.etiquetas, p.fecha_creacion, du.nombre_empresa, u.nombre, du.categoria
            ORDER BY p.fecha_creacion DESC LIMIT %s OFFSET %s
        """, (current_user, limit, offset))
        publicaciones = cur.fetchall()
        cur.close()

        return [
            {
                "id": row[0], "user_id": int(row[1]), "contenido": row[2] or "",
                "imagen_url": f"/media/{row[0]}" if row[7] else "",
                "video_url": f"/media/{row[0]}" if row[8] else "",
                "etiquetas": row[3] or [], "fecha_creacion": row[4].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[6] == 'emprendedor' else "",
                "nombre_empresa": row[5], "tipo_usuario": row[6],
                "interesados_count": int(row[9]), "interesado": row[10], "comentarios_count": int(row[11])
            } for row in publicaciones
        ]
    except Exception as e:
        logging.error(f"Error feed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

# Ruta SEARCH
@router.get("/search")
async def search_publicaciones(query: str, limit: int = 10, offset: int = 0, request: Request = None):
    query = query.strip().lower()
    if not query: raise HTTPException(status_code=400, detail="Query empty")
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
                EXISTS (SELECT 1 FROM intereses i WHERE i.publicacion_id = p.id AND i.user_id = %s),
                (SELECT COUNT(*) FROM comentarios c WHERE c.publicacion_id = p.id)
            FROM publicaciones p
            JOIN usuarios u ON p.user_id = u.id
            LEFT JOIN datos_usuario du ON p.user_id = du.user_id
            LEFT JOIN intereses i ON p.id = i.publicacion_id
            WHERE LOWER(COALESCE(du.nombre_empresa, u.nombre)) LIKE %s
               OR EXISTS (SELECT 1 FROM unnest(p.etiquetas) AS etiqueta WHERE LOWER(etiqueta) LIKE %s)
            GROUP BY p.id, p.user_id, p.contenido, p.etiquetas, p.fecha_creacion, du.nombre_empresa, u.nombre, du.categoria
            ORDER BY p.fecha_creacion DESC LIMIT %s OFFSET %s
        """, (current_user, f"%{query}%", f"%{query}%", limit, offset))
        publicaciones = cur.fetchall()
        cur.close()

        return [
            {
                "id": row[0], "user_id": int(row[1]), "contenido": row[2] or "",
                "imagen_url": f"/media/{row[0]}" if row[7] else "", "video_url": f"/media/{row[0]}" if row[8] else "",
                "etiquetas": row[3] or [], "fecha_creacion": row[4].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[6] == 'emprendedor' else "",
                "nombre_empresa": row[5], "tipo_usuario": row[6],
                "interesados_count": int(row[9]), "interesado": row[10], "comentarios_count": int(row[11])
            } for row in publicaciones
        ]
    except Exception as e:
        logging.error(f"Error search: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

# Ruta PERFIL FEED
@router.get("/perfil/feed")
async def perfil_feed(request: Request, limit: int = 10, offset: int = 0):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id: raise HTTPException(status_code=401, detail="No autorizado")

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT p.id, p.user_id, p.contenido, p.etiquetas, p.fecha_creacion, 
                    COALESCE(du.nombre_empresa, u.nombre),
                    CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END,
                    p.imagen IS NOT NULL, p.video IS NOT NULL,
                    (SELECT COUNT(*) FROM comentarios c WHERE c.publicacion_id = p.id)
                FROM publicaciones p
                JOIN usuarios u ON p.user_id = u.id
                LEFT JOIN datos_usuario du ON p.user_id = du.user_id
                WHERE p.user_id = %s
                ORDER BY p.fecha_creacion DESC LIMIT %s OFFSET %s
            """, (user_id, limit, offset))
            publicaciones = cur.fetchall()
            cur.close()
        finally:
            if conn: conn.close()

        return [
            {
                "id": row[0], "user_id": int(row[1]), "contenido": row[2] or "",
                "imagen_url": f"/media/{row[0]}" if row[7] else "", "video_url": f"/media/{row[0]}" if row[8] else "",
                "etiquetas": row[3] or [], "fecha_creacion": row[4].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[6] == 'emprendedor' else "",
                "nombre_empresa": row[5], "tipo_usuario": row[6], "comentarios_count": int(row[9])
            } for row in publicaciones
        ]
    except Exception as e:
        logging.error(f"Error perfil feed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Ruta SINGLE POST
@router.get("/publicacion/{post_id}")
async def get_publicacion(post_id: int, request: Request):
    try:
        current_user = get_user_id_hybrid(request)
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT p.id, p.user_id, p.contenido, p.etiquetas, p.fecha_creacion, 
                    COALESCE(du.nombre_empresa, u.nombre),
                    CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END,
                    p.imagen IS NOT NULL, p.video IS NOT NULL,
                    COUNT(DISTINCT i.user_id),
                    EXISTS (SELECT 1 FROM intereses i WHERE i.publicacion_id = p.id AND i.user_id = %s),
                    (SELECT COUNT(*) FROM comentarios c WHERE c.publicacion_id = p.id)
                FROM publicaciones p
                JOIN usuarios u ON p.user_id = u.id
                LEFT JOIN datos_usuario du ON p.user_id = du.user_id
                LEFT JOIN intereses i ON p.id = i.publicacion_id
                WHERE p.id = %s
                GROUP BY p.id, p.user_id, p.contenido, p.etiquetas, p.fecha_creacion, du.nombre_empresa, u.nombre, du.categoria
            """, (current_user if current_user else -1, post_id))
            row = cur.fetchone()
            cur.close()
            if not row: raise HTTPException(status_code=404, detail="No encontrado")

            return {
                "id": row[0], "user_id": int(row[1]), "contenido": row[2] or "",
                "imagen_url": f"/media/{row[0]}" if row[7] else "", "video_url": f"/media/{row[0]}" if row[8] else "",
                "etiquetas": row[3] or [], "fecha_creacion": row[4].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[6] == 'emprendedor' else "",
                "nombre_empresa": row[5], "tipo_usuario": row[6],
                "interesados_count": int(row[9]), "interesado": row[10], "comentarios_count": int(row[11])
            }
        finally:
            if conn: conn.close()
    except Exception as e:
        logging.error(f"Error single post: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Ruta USER DATA
@router.get("/user/{user_id}")
async def get_user(user_id: int):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT u.id, 
                CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END,
                COALESCE(du.nombre_empresa, u.nombre), u.email, du.foto, du.direccion, du.ubicacion_google_maps,
                du.telefono, du.horario, du.categoria, du.otra_categoria, du.servicios, du.sitio_web
            FROM usuarios u LEFT JOIN datos_usuario du ON u.id = du.user_id WHERE u.id = %s
        """, (user_id,))
        result = cur.fetchone()
        cur.close()
        if not result: raise HTTPException(status_code=404, detail="Usuario no encontrado")

        return {
            "user_id": result[0], "tipo": result[1], "nombre_empresa": result[2] or "", "email": result[3] or "",
            "foto_perfil": f"/foto_perfil/{user_id}" if result[1] == 'emprendedor' and result[4] else "",
            "direccion": result[5] or "", "ubicacion_google_maps": result[6] or "", "telefono": result[7] or "",
            "horario": result[8] or "", "categoria": result[9] or "", "otra_categoria": result[10] or "",
            "servicios": result[11] or "", "sitio_web": result[12] or ""
        }
    except Exception as e:
        logging.error(f"Error user data: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

# Ruta PUBLICACIONES DE USUARIO (Relative URL)
@router.get("/user/{user_id}/publicaciones")
async def get_user_publicaciones(user_id: int, limit: int = 10, offset: int = 0, request: Request = None):
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
                EXISTS (SELECT 1 FROM intereses i WHERE i.publicacion_id = p.id AND i.user_id = %s),
                (SELECT COUNT(*) FROM comentarios c WHERE c.publicacion_id = p.id)
            FROM publicaciones p
            JOIN usuarios u ON p.user_id = u.id
            LEFT JOIN datos_usuario du ON p.user_id = du.user_id
            LEFT JOIN intereses i ON p.id = i.publicacion_id
            WHERE p.user_id = %s
            GROUP BY p.id, p.user_id, p.contenido, p.etiquetas, p.fecha_creacion, du.nombre_empresa, u.nombre, du.categoria
            ORDER BY p.fecha_creacion DESC LIMIT %s OFFSET %s
        """, (current_user, user_id, limit, offset))
        publicaciones = cur.fetchall()
        cur.close()

        return [
            {
                "id": row[0], "user_id": int(row[1]), "contenido": row[2] or "",
                "imagen_url": f"/media/{row[0]}" if row[7] else "", "video_url": f"/media/{row[0]}" if row[8] else "",
                "etiquetas": row[3] or [], "fecha_creacion": row[4].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[6] == 'emprendedor' else "",
                "nombre_empresa": row[5], "tipo_usuario": row[6],
                "interesados_count": int(row[9]), "interesado": row[10], "comentarios_count": int(row[11])
            } for row in publicaciones
        ]
    except Exception as e:
        logging.error(f"Error user posts: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

# Ruta CURRENT USER
@router.get("/current_user")
async def get_current_user(request: Request):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id: return {"user_id": None, "tipo": None}
        
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END
                FROM usuarios u LEFT JOIN datos_usuario du ON u.id = du.user_id WHERE u.id = %s
            """, (user_id,))
            result = cur.fetchone()
            cur.close()
            return {"user_id": user_id, "tipo": result[0] if result else 'explorador', "id": user_id}
        finally:
            if conn: conn.close()
    except Exception as e:
        return {"user_id": None, "tipo": None}

# Ruta SALIR
@router.post("/salir")
async def salir(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)

# Ruta BORRAR PUBLICACIÓN
@router.delete("/borrar_publicacion/{post_id}")
async def borrar_publicacion(post_id: int, request: Request):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id: raise HTTPException(status_code=401, detail="No autorizado")

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT user_id FROM publicaciones WHERE id = %s", (post_id,))
            result = cur.fetchone()
            if not result: raise HTTPException(status_code=404, detail="No encontrado")
            if result[0] != user_id: raise HTTPException(status_code=403, detail="Sin permiso")

            cur.execute("DELETE FROM publicaciones WHERE id = %s", (post_id,))
            conn.commit()
            cur.close()
            return {"message": "Eliminado"}
        finally:
            if conn: conn.close()
    except Exception as e:
        logging.error(f"Error borrar post: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# =================================================================
# 🚀 LISTAR COMENTARIOS (CON JERARQUÍA Y MENCIONES)
# =================================================================
@router.get("/publicacion/{post_id}/comentarios")
async def list_comments(post_id: int, limit: int = 50, offset: int = 0):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT COUNT(*) FROM comentarios WHERE publicacion_id = %s", (post_id,))
        total = cur.fetchone()[0]

        # Query Mejorada: Trae info del padre y del usuario etiquetado
        cur.execute("""
            SELECT 
                c.id, c.publicacion_id, c.user_id, c.contenido, c.fecha_creacion,
                COALESCE(du.nombre_empresa, u.nombre) AS nombre_empresa,
                CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN %s || c.user_id ELSE '' END AS foto_perfil_url,
                CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END AS tipo_usuario,
                c.parent_id,
                c.reply_to_user_id,
                COALESCE(du_reply.nombre_empresa, u_reply.nombre) AS nombre_respondido
            FROM comentarios c
            JOIN usuarios u ON c.user_id = u.id
            LEFT JOIN datos_usuario du ON c.user_id = du.user_id
            -- JOIN EXTRA para saber a quién se etiqueta
            LEFT JOIN usuarios u_reply ON c.reply_to_user_id = u_reply.id
            LEFT JOIN datos_usuario du_reply ON c.reply_to_user_id = du_reply.user_id
            WHERE c.publicacion_id = %s
            ORDER BY c.fecha_creacion ASC
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
                "tipo_usuario": row[7],
                "parent_id": row[8],
                "reply_to_user_id": row[9],
                "nombre_respondido": row[10] # Nombre para el @Usuario
            }
            for row in comentarios
        ]

        cur.close()
        return {"comentarios": comentarios_list, "total": total}
    except Exception as e:
        logging.error(f"Error comentarios: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

# =================================================================
# 🚀 PUBLICAR COMENTARIO (CON NOTIFICACIONES INTELIGENTES)
# =================================================================
@router.post("/publicacion/{post_id}/comentar")
async def post_comment(post_id: int, request: CommentRequest, http_request: Request):
    conn = None
    try:
        user_id = get_user_id_hybrid(http_request)
        if not user_id: raise HTTPException(status_code=401, detail="Login requerido")

        contenido = request.contenido.strip()
        if not contenido: raise HTTPException(status_code=400, detail="Contenido vacío")

        conn = get_db_connection()
        cur = conn.cursor()

        # Insertamos el comentario con su jerarquía
        cur.execute("""
            INSERT INTO comentarios (publicacion_id, user_id, contenido, parent_id, reply_to_user_id, fecha_creacion)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            RETURNING id, publicacion_id, user_id, contenido, fecha_creacion
        """, (post_id, user_id, contenido, request.parent_id, request.reply_to_user_id))
        
        comment_data = cur.fetchone()
        new_comment_id = comment_data[0]
        conn.commit()

        # --- LÓGICA DE NOTIFICACIONES ---
        # 1. Prioridad: Es una Mención (@Usuario)
        if request.reply_to_user_id:
            await crear_notificacion(
                publicacion_id=post_id,
                tipo="mencion",
                actor_id=user_id,
                mensaje=contenido,
                target_user_id=request.reply_to_user_id,
                comentario_id=new_comment_id
            )
        
        # 2. Prioridad: Es una Respuesta a un hilo
        elif request.parent_id:
            cur.execute("SELECT user_id FROM comentarios WHERE id = %s", (request.parent_id,))
            parent_row = cur.fetchone()
            if parent_row:
                await crear_notificacion(
                    publicacion_id=post_id,
                    tipo="respuesta",
                    actor_id=user_id,
                    mensaje=contenido,
                    target_user_id=parent_row[0],
                    comentario_id=new_comment_id
                )

        # 3. Comentario normal (Notificar al dueño del post)
        else:
            await crear_notificacion(
                publicacion_id=post_id,
                tipo="comentario",
                actor_id=user_id,
                mensaje=contenido,
                target_user_id=None, # La función busca al dueño del post automáticamente
                comentario_id=new_comment_id
            )

        # Datos para devolver al frontend
        cur.execute("""
            SELECT COALESCE(du.nombre_empresa, u.nombre), 
                   CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END,
                   CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN %s || %s ELSE '' END
            FROM usuarios u LEFT JOIN datos_usuario du ON u.id = du.user_id WHERE u.id = %s
        """, ("/foto_perfil/", user_id, user_id))
        user_info = cur.fetchone()
        cur.close()

        return {
            "id": new_comment_id,
            "publicacion_id": post_id,
            "user_id": user_id,
            "contenido": contenido,
            "fecha_creacion": comment_data[4].strftime("%Y-%m-%d %H:%M:%S"),
            "parent_id": request.parent_id,
            "reply_to_user_id": request.reply_to_user_id,
            "nombre_empresa": user_info[0] or "Anónimo",
            "tipo_usuario": user_info[1],
            "foto_perfil_url": user_info[2]
        }

    except Exception as e:
        if conn: conn.rollback()
        logging.error(f"Error al comentar: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

# Ruta INTERES (LIKE)
@router.post("/publicacion/{post_id}/interesar")
async def toggle_interest(post_id: int, request: InterestRequest, http_request: Request):
    conn = None
    try:
        user_id = get_user_id_hybrid(http_request)
        if not user_id: raise HTTPException(status_code=401, detail="No autorizado")

        conn = get_db_connection()
        cur = conn.cursor()
        
        # Verificar existencia
        cur.execute("SELECT id FROM publicaciones WHERE id = %s", (post_id,))
        if not cur.fetchone(): raise HTTPException(status_code=404, detail="Post no encontrado")

        cur.execute("SELECT id FROM intereses WHERE publicacion_id = %s AND user_id = %s", (post_id, user_id))
        existing_interest = cur.fetchone()

        if existing_interest:
            cur.execute("DELETE FROM intereses WHERE publicacion_id = %s AND user_id = %s", (post_id, user_id))
        else:
            cur.execute("INSERT INTO intereses (publicacion_id, user_id, fecha_creacion) VALUES (%s, %s, CURRENT_TIMESTAMP)", (post_id, user_id))
            # Notificación Actualizada
            await crear_notificacion(
                publicacion_id=post_id,
                tipo="interes",
                actor_id=user_id,
                mensaje="Le interesa tu publicación"
            )

        conn.commit()
        
        # Obtener contadores actualizados
        cur.execute("SELECT COUNT(*) FROM intereses WHERE publicacion_id = %s", (post_id,))
        interesados_count = cur.fetchone()[0]
        cur.execute("SELECT EXISTS (SELECT 1 FROM intereses WHERE publicacion_id = %s AND user_id = %s)", (post_id, user_id))
        interesado = cur.fetchone()[0]
        cur.close()

        return {"interesados_count": interesados_count, "interesado": interesado}
    except Exception as e:
        if conn: conn.rollback()
        logging.error(f"Error like: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

# Ruta BORRAR COMENTARIO
@router.delete("/borrar_comentario/{comentario_id}")
async def borrar_comentario(comentario_id: int, request: Request):
    conn = None
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id: raise HTTPException(status_code=401, detail="No autorizado")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM comentarios WHERE id = %s", (comentario_id,))
        comment = cur.fetchone()
        if not comment: raise HTTPException(status_code=404, detail="No encontrado")
        if comment[0] != user_id: raise HTTPException(status_code=403, detail="Sin permiso")

        cur.execute("DELETE FROM comentarios WHERE id = %s", (comentario_id,))
        conn.commit()
        cur.close()
        return {"message": "Borrado"}
    except Exception as e:
        if conn: conn.rollback()
        logging.error(f"Error borrar comentario: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

# Rutas RESEÑAS (GET/POST/DELETE) - Sin cambios mayores, solo mantener
@router.get("/api/perfil/{perfil_id}/resenas")
async def get_user_resenas(perfil_id: int, request: Request, limit: int = 10, offset: int = 0):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM usuarios WHERE id = %s", (perfil_id,))
        if not cur.fetchone(): raise HTTPException(status_code=404, detail="Perfil no encontrado")

        cur.execute("""
            SELECT r.id, r.user_id, r.perfil_id, r.texto, r.calificacion, r.fecha_creacion,
                   COALESCE(du.nombre_empresa, u.nombre),
                   CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END,
                   du.foto
            FROM resenas r JOIN usuarios u ON r.user_id = u.id LEFT JOIN datos_usuario du ON r.user_id = du.user_id
            WHERE r.perfil_id = %s ORDER BY r.fecha_creacion DESC LIMIT %s OFFSET %s
        """, (perfil_id, limit, offset))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        data = []
        for r in rows:
            data.append({
                "id": r[0], "user_id": r[1], "perfil_id": r[2], "texto": r[3], "calificacion": r[4], 
                "fecha_creacion": r[5].strftime("%Y-%m-%d %H:%M:%S"), "nombre_empresa": r[6] or "Anónimo", 
                "tipo_usuario": r[7], "foto_perfil": f"/foto_perfil/{r[1]}" if r[7] == 'emprendedor' and r[8] else ""
            })
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/perfil/{perfil_id}/resenas")
async def create_review(perfil_id: int, request: ReviewRequest, http_request: Request):
    conn = None
    try:
        user_id = get_user_id_hybrid(http_request)
        if not user_id: raise HTTPException(status_code=401, detail="Login requerido")
        if user_id == perfil_id: raise HTTPException(status_code=400, detail="Auto-reseña no permitida")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO resenas (user_id, perfil_id, texto, calificacion, fecha_creacion)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP) RETURNING id, fecha_creacion
        """, (user_id, perfil_id, request.texto, request.calificacion))
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

        return {
            "id": new_data[0], "user_id": user_id, "perfil_id": perfil_id, "texto": request.texto, 
            "calificacion": request.calificacion, "fecha_creacion": new_data[1].strftime("%Y-%m-%d %H:%M:%S"),
            "nombre_empresa": autor[0] or "Anónimo", "tipo_usuario": autor[1], 
            "foto_perfil": f"/foto_perfil/{user_id}" if autor[1] == 'emprendedor' and autor[2] else ""
        }
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@router.delete("/api/perfil/{perfil_id}/resenas/{resena_id}")
async def delete_review(perfil_id: int, resena_id: int, request: Request):
    conn = None
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id: raise HTTPException(status_code=401, detail="No autorizado")
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM resenas WHERE id = %s", (resena_id,))
        resena = cur.fetchone()
        if not resena: raise HTTPException(status_code=404, detail="No encontrada")
        if resena[0] != user_id: raise HTTPException(status_code=403, detail="Sin permiso")

        cur.execute("DELETE FROM resenas WHERE id = %s", (resena_id,))
        conn.commit()
        cur.close()
        return {"message": "Eliminada"}
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

# Rutas NOTIFICACIONES (LECTURA Y CONTEO)
@router.get("/notificaciones")
async def obtener_notificaciones(request: Request, limit: int = 10, offset: int = 0):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id: raise HTTPException(status_code=401, detail="No autorizado")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT n.id, n.publicacion_id, n.tipo, n.leida, n.fecha_creacion, n.actor_id, 
                       COALESCE(du.nombre_empresa, u.nombre), n.mensaje,
                       CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END,
                       CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN %s || n.actor_id ELSE '' END,
                       n.comentario_id
                FROM notifications n
                JOIN usuarios u ON n.actor_id = u.id
                LEFT JOIN datos_usuario du ON u.id = du.user_id
                WHERE n.user_id = %s
                ORDER BY n.fecha_creacion DESC LIMIT %s OFFSET %s
            """, ("/foto_perfil/", user_id, limit, offset))
            notificaciones = cur.fetchall()

            cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s", (user_id,))
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND leida = FALSE", (user_id,))
            no_leidas = cur.fetchone()[0]

            return {
                "notificaciones": [
                    {
                        "id": n[0], "publicacion_id": n[1], "tipo": n[2], "leida": n[3], 
                        "fecha_creacion": n[4].strftime("%Y-%m-%d %H:%M:%S"), "actor_id": n[5], 
                        "nombre_usuario": n[6] or "Desconocido", "mensaje": n[7], 
                        "tipo_usuario": n[8], "foto_perfil_url": n[9], "comentario_id": n[10]
                    } for n in notificaciones
                ],
                "total": total, "no_leidas": no_leidas
            }
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logging.error(f"Error notificaciones: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/notificaciones/{notificacion_id}/leida")
async def marcar_notificacion_leida(notificacion_id: int, request: Request):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id: raise HTTPException(status_code=401, detail="No autorizado")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE notifications SET leida = TRUE WHERE id = %s AND user_id = %s", (notificacion_id, user_id))
        conn.commit()
        cur.close()
        conn.close()
        return {"message": "Leída"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/notificaciones/no_leidas")
async def contar_notificaciones_no_leidas(request: Request):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id: return {"no_leidas": 0}
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND leida = FALSE", (user_id,))
        no_leidas = cur.fetchone()[0]
        cur.close()
        conn.close()
        return {"no_leidas": no_leidas}
    except Exception:
        return {"no_leidas": 0}

# =================================================================
# 🚀 ENDPOINT WEBSOCKET PARA NOTIFICACIONES (¡AÑADIDO!)
# =================================================================
@router.websocket("/notificaciones/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    await notification_manager.connect(websocket, user_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        notification_manager.disconnect(websocket, user_id)
    except Exception as e:
        logging.error(f"Error en WS notificaciones: {e}")
        notification_manager.disconnect(websocket, user_id)

