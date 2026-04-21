from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException, Header, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse, StreamingResponse, Response
from fastapi.templating import Jinja2Templates
from typing import List, Dict, Optional
import psycopg2
from datetime import datetime
import logging
import io
import re
import json 
from pydantic import BaseModel
import jwt
from firebase_admin import messaging 


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

# --- GESTOR DE WEBSOCKETS ---
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

notification_manager = NotificationManager()

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

MAX_FILE_SIZE = 100 * 1024 * 1024  

class InterestRequest(BaseModel):
    user_id: int

class CommentRequest(BaseModel):
    contenido: str
    parent_id: int | None = None       
    reply_to_user_id: int | None = None 

class ReporteUsuarioRequest(BaseModel):
    usuario_reportado_id: int
    motivo: str

class BloqueoRequest(BaseModel):
    bloqueado_id: int

class ReviewRequest(BaseModel):
    texto: str
    calificacion: int

class ReportePublicacionRequest(BaseModel):
    publicacion_id: int
    motivo: str

class FCMTokenRequest(BaseModel):
    fcm_token: str

@router.post("/api/update_fcm_token")
async def update_fcm_token(request: Request, data: FCMTokenRequest):
    conn = None
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            raise HTTPException(status_code=401, detail="No autorizado")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE usuarios SET fcm_token = %s WHERE id = %s", (data.fcm_token, user_id))
        conn.commit()
        return JSONResponse(content={"status": "ok", "message": "Token guardado"})
    except Exception as e:
        if conn: conn.rollback()
        logging.error(f"Error guardando FCM: {e}")
        raise HTTPException(status_code=500, detail="Error interno")
    finally:
        if conn: conn.close()

SECRET_KEY_JWT = "Elbicho7"

def get_user_id_hybrid(request: Request):
    auth_header = request.headers.get("Authorization")
    
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1] 
        try:
            payload = jwt.decode(token, SECRET_KEY_JWT, algorithms=["HS256"])
            user_id = payload.get("user_id") or payload.get("sub")
            if user_id:
                return int(user_id)
        except Exception as e:
            if "jwt_app_" in token:
                try:
                    return int(token.split("jwt_app_")[1])
                except:
                    pass
            logging.error(f"Error validando token: {e}")

    if 'user' in request.session and 'id' in request.session['user']:
        return int(request.session['user']['id'])
        
    return None

async def crear_notificacion(publicacion_id: int, tipo: str, actor_id: int, mensaje: str = None, target_user_id: int = None, comentario_id: int = None):
    try:
        if tipo not in ['interes', 'comentario', 'respuesta', 'mencion']:
            raise HTTPException(status_code=400, detail="Tipo inválido")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            receptor_id = target_user_id
            if not receptor_id:
                cur.execute("SELECT user_id FROM publicaciones WHERE id = %s", (publicacion_id,))
                publicacion = cur.fetchone()
                if not publicacion: return None
                receptor_id = publicacion[0]

            if receptor_id == actor_id: return None 

            cur.execute("""
                SELECT 
                    (SELECT COALESCE(du.nombre_empresa, u.nombre) FROM usuarios u LEFT JOIN datos_usuario du ON u.id = du.user_id WHERE u.id = %s) AS actor_name,
                    (SELECT fcm_token FROM usuarios WHERE id = %s) AS fcm_token
            """, (actor_id, receptor_id))
            row = cur.fetchone()
            actor_name = row[0] if row and row[0] else "Usuario"
            fcm_token = row[1] if row and row[1] else None

            cur.execute("""
                INSERT INTO notifications (user_id, publicacion_id, tipo, leida, fecha_creacion, actor_id, mensaje, comentario_id)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s)
                RETURNING id, fecha_creacion
            """, (receptor_id, publicacion_id, tipo, False, actor_id, mensaje, comentario_id))
            notificacion = cur.fetchone()
            conn.commit()

            payload = {
                "id": notificacion[0], "user_id": receptor_id, "publicacion_id": publicacion_id,
                "tipo": tipo, "leida": False, "fecha_creacion": notificacion[1].strftime("%Y-%m-%d %H:%M:%S"),
                "actor_id": actor_id, "nombre_usuario": actor_name, "mensaje": mensaje, "comentario_id": comentario_id
            }
            await notification_manager.send_personal_message(payload, receptor_id)

            if fcm_token:
                titulos = {'interes': "¡Nueva interacción!", 'comentario': "Nuevo comentario", 'respuesta': "Te han respondido", 'mencion': "Te mencionaron"}
                cuerpos = {'interes': f"A {actor_name} le interesó tu publicación.", 'comentario': f"{actor_name} comentó: {mensaje}", 'respuesta': f"{actor_name} respondió a tu comentario.", 'mencion': f"{actor_name} te mencionó: {mensaje}"}
                
                # 🔥 1. CALCULAMOS EL TOTAL DE NO LEÍDAS PARA EL GLOBO ROJO (BADGE) 🔥
                cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND leida = FALSE", (receptor_id,))
                total_notis = cur.fetchone()[0]
                badge_count = total_notis 

                try:
                    push_msg = messaging.Message(
                        notification=messaging.Notification(title=titulos.get(tipo, "Notificación"), body=cuerpos.get(tipo, "Tienes una nueva notificación")), 
                        apns=messaging.APNSConfig(
                            payload=messaging.APNSPayload(
                                # 🔥 2. LE INYECTAMOS EL BADGE A APPLE AQUÍ 🔥
                                aps=messaging.Aps(sound="default", badge=badge_count)
                            )
                        ),
                        data={"tipo": tipo, "publicacion_id": str(publicacion_id)},
                        token=fcm_token,
                    )
                    messaging.send(push_msg)
                except Exception as e:
                    logging.error(f"Error enviando Push: {e}")

            return payload
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logging.error(f"Error crear_notificacion: {e}")
        return None
    
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
        logging.error(f"Error foto de perfil: {e}")
        raise HTTPException(status_code=500, detail="Error foto")
    finally:
        if conn: conn.close()

@router.get("/media/imagen/{img_id}")
def get_media_imagen_carrusel(img_id: int):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT imagen FROM publicacion_imagenes WHERE id = %s", (img_id,))
        result = cur.fetchone()
        cur.close()
        
        if not result or not result[0]:
            raise HTTPException(status_code=404, detail="Imagen no encontrada")
            
        return StreamingResponse(
            content=io.BytesIO(result[0]),
            media_type="image/jpeg",
            headers={"Content-Disposition": f"inline; filename=img_car_{img_id}.jpg"}
        )
    except Exception as e:
        logging.error(f"Error sirviendo imagen carrusel {img_id}: {e}")
        raise HTTPException(status_code=500, detail="Error interno")
    finally:
        if conn: conn.close()


    # 🔥 RUTA SALVA-VIDAS PARA FOTOS VIEJAS 🔥
@router.get("/media/imagen_vieja/{post_id}")
def get_media_imagen_vieja(post_id: int):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT imagen FROM publicaciones WHERE id = %s", (post_id,))
        result = cur.fetchone()
        cur.close()
        if not result or not result[0]:
            raise HTTPException(status_code=404, detail="Imagen no encontrada")
        return StreamingResponse(
            content=io.BytesIO(result[0]), media_type="image/jpeg",
            headers={"Content-Disposition": f"inline; filename=old_img_{post_id}.jpg"}
        )
    finally:
        if conn: conn.close()

@router.get("/media/{post_id}")
def get_media(post_id: int, request: Request):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT video FROM publicaciones WHERE id = %s", (post_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()

        if not result or not result[0]:
            raise HTTPException(status_code=404, detail="Video no encontrado")

        video_data = result[0]
        
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
    except Exception as e:
        if conn and not conn.closed: conn.close()
        logging.error(f"Error media post {post_id}: {e}")
        raise HTTPException(status_code=500, detail="Error interno al servir media")


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
                    (SELECT array_agg(id) FROM publicacion_imagenes WHERE publicacion_id = p.id) AS imagenes_ids,
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

        # 🔥 AQUÍ INYECTAMOS LA RETROCOMPATIBILIDAD (imagen_url)
        publicaciones_list = [
            {
                "id": row[0],
                "user_id": int(row[1]),
                "contenido": row[2] or "",
                "imagenes": [f"/media/imagen/{img_id}" for img_id in row[7]] if row[7] else [],
                "imagen_url": f"/media/imagen/{row[7][0]}" if row[7] else "", # <--- PARCHE SALVA-VIDAS
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

# =================================================================
# 🔥 1. RUTA PARA PUBLICAR (BLINDADA) 🔥
# =================================================================
@router.post("/publicar")
async def publicar(request: Request):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            if request.headers.get("Authorization"): raise HTTPException(status_code=401, detail="No autorizado")
            return RedirectResponse(url="/login", status_code=302)

        # 🔥 ABRIMOS EL PAQUETE MANUALMENTE (A PRUEBA DE FLUTTER) 🔥
        form = await request.form()
        
        contenido = form.get("contenido", "")
        etiquetas = form.get("etiquetas", "")
        video = form.get("video")
        imagenes = form.getlist("imagenes") # Atrapamos TODA la lista de fotos

        # Filtramos para quedarnos solo con archivos reales
        imagenes_validas = [img for img in imagenes if getattr(img, "filename", None)]
        video_valido = video if getattr(video, "filename", None) else None
        texto = contenido.strip() if contenido else ""

        if not texto and not imagenes_validas and not video_valido:
            raise HTTPException(status_code=400, detail="Debe incluir contenido o multimedia")

        if imagenes_validas and video_valido: 
            raise HTTPException(status_code=400, detail="Solo imágenes o video, no ambos")
        if len(imagenes_validas) > 10: 
            raise HTTPException(status_code=400, detail="Máximo 10 imágenes permitidas")

        etiquetas_lista = [e.strip() for e in etiquetas.split(",") if e.strip()] if isinstance(etiquetas, str) and etiquetas else []
        
        video_data = None
        if video_valido:
            video_data = await video_valido.read()
            if len(video_data) > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail="Video muy pesado")

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            
            cur.execute("""
                INSERT INTO publicaciones (user_id, contenido, video, etiquetas, fecha_creacion)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING id
            """, (user_id, texto, psycopg2.Binary(video_data) if video_data else None, etiquetas_lista))
            
            post_id = cur.fetchone()[0]

            # 🔥 GUARDAMOS LAS FOTOS EN LA NUEVA TABLA 🔥
            for img in imagenes_validas:
                img_data = await img.read()
                if img_data:
                    cur.execute("""
                        INSERT INTO publicacion_imagenes (publicacion_id, imagen)
                        VALUES (%s, %s)
                    """, (post_id, psycopg2.Binary(img_data)))

            conn.commit()

            # --- ALGORITMO DESPERTADOR ---
            try:
                cur.execute("""
                    SELECT id, fcm_token FROM usuarios 
                    WHERE id != %s AND fcm_token IS NOT NULL
                      AND ultima_conexion < CURRENT_TIMESTAMP - INTERVAL '2 days'
                      AND (ultima_noti_despertador IS NULL OR ultima_noti_despertador < CURRENT_TIMESTAMP - INTERVAL '7 days')
                    LIMIT 50
                """, (user_id,))
                
                usuarios_dormidos = cur.fetchall()

                if usuarios_dormidos:
                    cur.execute("SELECT COALESCE(du.nombre_empresa, u.nombre) FROM usuarios u LEFT JOIN datos_usuario du ON u.id = du.user_id WHERE u.id = %s", (user_id,))
                    autor = cur.fetchone()
                    nombre_autor = autor[0] if autor and autor[0] else "Alguien"
                    ids_despertados = []

                    for user_dormido in usuarios_dormidos:
                        ids_despertados.append(user_dormido[0])
                        push_msg = messaging.Message(
                            notification=messaging.Notification(title="¡Nuevos servicios en PrendiaX! 👀", body=f"{nombre_autor} acaba de publicar algo que podría interesarte."), 
                            apns=messaging.APNSConfig(payload=messaging.APNSPayload(aps=messaging.Aps(sound="default"))),
                            data={"tipo": "general", "publicacion_id": str(post_id)}, token=user_dormido[1]
                        )
                        messaging.send(push_msg)

                    if ids_despertados:
                        cur.execute("UPDATE usuarios SET ultima_noti_despertador = CURRENT_TIMESTAMP WHERE id = ANY(%s)", (ids_despertados,))
                        conn.commit()

            except Exception as push_err:
                logging.error(f"Error en Algoritmo Despertador: {push_err}")
            # ------------------------------

            cur.close()
        finally:
            if conn: conn.close()

        return RedirectResponse(url="/inicio", status_code=302)
    except Exception as e:
        logging.error(f"Error en /publicar: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# =================================================================
# 🔥 2. RUTA PARA EDITAR (BLINDADA) 🔥
# =================================================================
@router.post("/api/publicacion/{post_id}/editar")
async def editar_publicacion(post_id: int, request: Request):
    conn = None
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id: raise HTTPException(status_code=401, detail="No autorizado")

        form = await request.form()
        contenido = form.get("contenido", "")
        etiquetas = form.get("etiquetas", "")
        reemplazar_media = form.get("reemplazar_media", "false")
        video = form.get("video")
        imagenes = form.getlist("imagenes")

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT user_id FROM publicaciones WHERE id = %s", (post_id,))
        row = cur.fetchone()
        if not row: raise HTTPException(status_code=404, detail="Publicación no encontrada")
        if row[0] != user_id: raise HTTPException(status_code=403, detail="No tienes permiso")

        etiquetas_lista = [e.strip() for e in etiquetas.split(",") if e.strip()] if isinstance(etiquetas, str) and etiquetas else []

        if reemplazar_media == "true":
            cur.execute("DELETE FROM publicacion_imagenes WHERE publicacion_id = %s", (post_id,))
            
            imagenes_validas = [img for img in imagenes if getattr(img, "filename", None)]
            video_valido = video if getattr(video, "filename", None) else None
            video_data = await video_valido.read() if video_valido else None
            
            if len(imagenes_validas) > 10: raise HTTPException(status_code=400, detail="Máximo 10 imágenes")
            if imagenes_validas and video_data: raise HTTPException(status_code=400, detail="Imágenes o video, no ambos")

            cur.execute("""
                UPDATE publicaciones SET contenido = %s, etiquetas = %s, video = %s
                WHERE id = %s
            """, (contenido, etiquetas_lista, psycopg2.Binary(video_data) if video_data else None, post_id))

            for img in imagenes_validas:
                img_data = await img.read()
                if img_data:
                    cur.execute("INSERT INTO publicacion_imagenes (publicacion_id, imagen) VALUES (%s, %s)", 
                                (post_id, psycopg2.Binary(img_data)))
        else:
            cur.execute("""
                UPDATE publicaciones SET contenido = %s, etiquetas = %s
                WHERE id = %s
            """, (contenido, etiquetas_lista, post_id))

        conn.commit()
        return JSONResponse(content={"status": "ok", "message": "Publicación actualizada"})

    except Exception as e:
        if conn: conn.rollback()
        logging.error(f"Error al editar post {post_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()
    
@router.post("/api/reportar/publicacion")
async def reportar_publicacion(request: Request, reporte: ReportePublicacionRequest):
    conn = None
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id:
            raise HTTPException(status_code=401, detail="Debes iniciar sesión para reportar.")

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT id FROM publicaciones WHERE id = %s", (reporte.publicacion_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="La publicación no existe.")

        cur.execute("""
            SELECT id FROM reportes_publicaciones 
            WHERE denunciante_id = %s AND publicacion_id = %s AND estatus = 'pendiente'
        """, (user_id, reporte.publicacion_id))
        
        if cur.fetchone():
            return JSONResponse(content={"status": "ok", "message": "Ya has reportado esta publicación anteriormente."})

        cur.execute("""
            INSERT INTO reportes_publicaciones (denunciante_id, publicacion_id, motivo, estatus, fecha_reporte)
            VALUES (%s, %s, %s, 'pendiente', CURRENT_TIMESTAMP)
        """, (user_id, reporte.publicacion_id, reporte.motivo))
        
        conn.commit()
        return JSONResponse(content={"status": "ok", "message": "Reporte enviado. Gracias por ayudarnos."})

    except HTTPException as he:
        raise he
    except Exception as e:
        if conn: conn.rollback()
        logging.error(f"Error al reportar: {e}")
        raise HTTPException(status_code=500, detail="Error interno.")
    finally:
        if conn: conn.close()

@router.get("/feed")
async def feed(limit: int = 10, offset: int = 0, request: Request = None):
    conn = None
    try:
        current_user = get_user_id_hybrid(request) if request else -1
        conn = get_db_connection()
        cur = conn.cursor()
        
        query = """
            SELECT p.id, p.user_id, p.contenido, p.etiquetas, p.fecha_creacion, 
                COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END AS tipo_usuario,
                (SELECT array_agg(id) FROM publicacion_imagenes WHERE publicacion_id = p.id) AS imagenes_ids,
                p.video IS NOT NULL AS has_video,
                COUNT(DISTINCT i.user_id) AS interesados_count,
                EXISTS (SELECT 1 FROM intereses i WHERE i.publicacion_id = p.id AND i.user_id = %s) AS interesado,
                (SELECT COUNT(*) FROM comentarios c WHERE c.publicacion_id = p.id) AS comentarios_count,
                p.imagen IS NOT NULL AS has_old_image -- 🔥 DETECTA SI TIENE FOTO VIEJA
            FROM publicaciones p
            JOIN usuarios u ON p.user_id = u.id
            LEFT JOIN datos_usuario du ON p.user_id = du.user_id
            LEFT JOIN intereses i ON p.id = i.publicacion_id
            WHERE 
                p.user_id NOT IN (SELECT bloqueado_id FROM bloqueos WHERE bloqueador_id = %s)
                AND 
                p.user_id NOT IN (SELECT bloqueador_id FROM bloqueos WHERE bloqueado_id = %s)
            GROUP BY p.id, p.user_id, p.contenido, p.etiquetas, p.fecha_creacion, p.imagen, du.nombre_empresa, u.nombre, du.categoria
            ORDER BY p.fecha_creacion DESC LIMIT %s OFFSET %s
        """
        
        cur.execute(query, (current_user, current_user, current_user, limit, offset))
        publicaciones = cur.fetchall()
        cur.close()

        return [
            {
                "id": row[0], "user_id": int(row[1]), "contenido": row[2] or "",
                # 🔥 EL PARCHE MAGISTRAL ANTI-[NONE] 🔥
                "imagenes": [f"/media/imagen/{img_id}" for img_id in row[7] if img_id is not None] if row[7] else [], 
                "imagen_url": f"/media/imagen_vieja/{row[0]}" if row[12] else (f"/media/imagen/{row[7][0]}" if row[7] and row[7][0] is not None else ""),
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
                (SELECT array_agg(id) FROM publicacion_imagenes WHERE publicacion_id = p.id),
                p.video IS NOT NULL,
                COUNT(DISTINCT i.user_id),
                EXISTS (SELECT 1 FROM intereses i WHERE i.publicacion_id = p.id AND i.user_id = %s),
                (SELECT COUNT(*) FROM comentarios c WHERE c.publicacion_id = p.id)
            FROM publicaciones p
            JOIN usuarios u ON p.user_id = u.id
            LEFT JOIN datos_usuario du ON p.user_id = du.user_id
            LEFT JOIN intereses i ON p.id = i.publicacion_id
            WHERE 
                (
                    LOWER(COALESCE(du.nombre_empresa, u.nombre)) LIKE %s
                    OR EXISTS (SELECT 1 FROM unnest(p.etiquetas) AS etiqueta WHERE LOWER(etiqueta) LIKE %s)
                    OR LOWER(p.contenido) LIKE %s -- 🔥 BÚSQUEDA INTELIGENTE EN TEXTO 🔥
                )
                AND p.user_id NOT IN (SELECT bloqueado_id FROM bloqueos WHERE bloqueador_id = %s)
                AND p.user_id NOT IN (SELECT bloqueador_id FROM bloqueos WHERE bloqueado_id = %s)
            GROUP BY p.id, p.user_id, p.contenido, p.etiquetas, p.fecha_creacion, du.nombre_empresa, u.nombre, du.categoria
            ORDER BY p.fecha_creacion DESC LIMIT %s OFFSET %s
        """, (current_user, f"%{query}%", f"%{query}%", f"%{query}%", current_user, current_user, limit, offset))
        
        publicaciones = cur.fetchall()
        cur.close()

        return [
            {
                "id": row[0], "user_id": int(row[1]), "contenido": row[2] or "",
                "imagenes": [f"/media/imagen/{img_id}" for img_id in row[7]] if row[7] else [], 
                "imagen_url": f"/media/imagen/{row[7][0]}" if row[7] else "", # <--- PARCHE SALVA-VIDAS
                "video_url": f"/media/{row[0]}" if row[8] else "",
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
                    (SELECT array_agg(id) FROM publicacion_imagenes WHERE publicacion_id = p.id), 
                    p.video IS NOT NULL,
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
                # 🔥 EL PARCHE MAGISTRAL ANTI-[NONE] 🔥
                "imagenes": [f"/media/imagen/{img_id}" for img_id in row[7] if img_id is not None] if row[7] else [], 
                "imagen_url": f"/media/imagen_vieja/{row[0]}" if row[12] else (f"/media/imagen/{row[7][0]}" if row[7] and row[7][0] is not None else ""),
                "video_url": f"/media/{row[0]}" if row[8] else "",
                "etiquetas": row[3] or [], "fecha_creacion": row[4].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[6] == 'emprendedor' else "",
                "nombre_empresa": row[5], "tipo_usuario": row[6],
                "interesados_count": int(row[9]), "interesado": row[10], "comentarios_count": int(row[11])
            } for row in publicaciones
        ]
    except Exception as e:
        logging.error(f"Error perfil feed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
                    (SELECT array_agg(id) FROM publicacion_imagenes WHERE publicacion_id = p.id), 
                    p.video IS NOT NULL,
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

            return [
            {
                "id": row[0], "user_id": int(row[1]), "contenido": row[2] or "",
                # 🔥 EL PARCHE MAGISTRAL ANTI-[NONE] 🔥
                "imagenes": [f"/media/imagen/{img_id}" for img_id in row[7] if img_id is not None] if row[7] else [], 
                "imagen_url": f"/media/imagen_vieja/{row[0]}" if row[12] else (f"/media/imagen/{row[7][0]}" if row[7] and row[7][0] is not None else ""),
                "video_url": f"/media/{row[0]}" if row[8] else "",
                "etiquetas": row[3] or [], "fecha_creacion": row[4].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[6] == 'emprendedor' else "",
                "nombre_empresa": row[5], "tipo_usuario": row[6],
                "interesados_count": int(row[9]), "interesado": row[10], "comentarios_count": int(row[11])
            } for row in [row]
        ]
        finally:
            if conn: conn.close()
    except Exception as e:
        logging.error(f"Error single post: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
                (SELECT array_agg(id) FROM publicacion_imagenes WHERE publicacion_id = p.id), 
                p.video IS NOT NULL,
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
                "imagenes": [f"/media/imagen/{img_id}" for img_id in row[7]] if row[7] else [], 
                "imagen_url": f"/media/imagen/{row[7][0]}" if row[7] else "", # <--- PARCHE SALVA-VIDAS
                "video_url": f"/media/{row[0]}" if row[8] else "",
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

@router.get("/current_user")
async def get_current_user(request: Request):
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id: return {"user_id": None, "tipo": None}
        
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()

            # 🔥 NUEVO: ACTUALIZAMOS SU ÚLTIMA CONEXIÓN 🔥
            cur.execute("""
                UPDATE usuarios 
                SET ultima_conexion = CURRENT_TIMESTAMP 
                WHERE id = %s
            """, (user_id,))
            conn.commit()

            cur.execute("""
                SELECT CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END
                FROM usuarios u LEFT JOIN datos_usuario du ON u.id = du.user_id WHERE u.id = %s
            """, (user_id,))
            result = cur.fetchone()
            return {"user_id": user_id, "tipo": result[0] if result else 'explorador', "id": user_id}
        finally:
            if cur: cur.close()
            if conn: conn.close()
    except Exception as e:
        logging.error(f"Error en get_current_user: {e}")
        return {"user_id": None, "tipo": None}

@router.post("/salir")
async def salir(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
        
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

@router.get("/publicacion/{post_id}/comentarios")
async def list_comments(post_id: int, limit: int = 50, offset: int = 0):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT COUNT(*) FROM comentarios WHERE publicacion_id = %s", (post_id,))
        total = cur.fetchone()[0]

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
                "nombre_respondido": row[10] 
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

        cur.execute("""
            INSERT INTO comentarios (publicacion_id, user_id, contenido, parent_id, reply_to_user_id, fecha_creacion)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            RETURNING id, publicacion_id, user_id, contenido, fecha_creacion
        """, (post_id, user_id, contenido, request.parent_id, request.reply_to_user_id))
        
        comment_data = cur.fetchone()
        new_comment_id = comment_data[0]
        conn.commit()

        if request.reply_to_user_id:
            await crear_notificacion(
                publicacion_id=post_id,
                tipo="mencion",
                actor_id=user_id,
                mensaje=contenido,
                target_user_id=request.reply_to_user_id,
                comentario_id=new_comment_id
            )
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
        else:
            await crear_notificacion(
                publicacion_id=post_id,
                tipo="comentario",
                actor_id=user_id,
                mensaje=contenido,
                target_user_id=None,
                comentario_id=new_comment_id
            )

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

@router.post("/publicacion/{post_id}/interesar")
async def toggle_interest(post_id: int, request: InterestRequest, http_request: Request):
    conn = None
    try:
        user_id = get_user_id_hybrid(http_request)
        if not user_id: raise HTTPException(status_code=401, detail="No autorizado")

        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT id FROM publicaciones WHERE id = %s", (post_id,))
        if not cur.fetchone(): raise HTTPException(status_code=404, detail="Post no encontrado")

        cur.execute("SELECT id FROM intereses WHERE publicacion_id = %s AND user_id = %s", (post_id, user_id))
        existing_interest = cur.fetchone()

        if existing_interest:
            cur.execute("DELETE FROM intereses WHERE publicacion_id = %s AND user_id = %s", (post_id, user_id))
        else:
            cur.execute("INSERT INTO intereses (publicacion_id, user_id, fecha_creacion) VALUES (%s, %s, CURRENT_TIMESTAMP)", (post_id, user_id))
            await crear_notificacion(
                publicacion_id=post_id,
                tipo="interes",
                actor_id=user_id,
                mensaje="Le interesa tu publicación"
            )

        conn.commit()
        
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


@router.post("/api/reportar/usuario")
async def reportar_usuario(request: Request, reporte: ReporteUsuarioRequest):
    conn = None
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id: raise HTTPException(status_code=401, detail="Login requerido")

        conn = get_db_connection()
        cur = conn.cursor()

        if user_id == reporte.usuario_reportado_id:
            return JSONResponse({"status": "error", "message": "No te puedes reportar a ti mismo"})

        cur.execute("""
            INSERT INTO reportes_usuarios (denunciante_id, usuario_reportado_id, motivo, estatus)
            VALUES (%s, %s, %s, 'pendiente')
        """, (user_id, reporte.usuario_reportado_id, reporte.motivo))
        conn.commit()
        
        return JSONResponse({"status": "ok", "message": "Usuario reportado. Revisaremos su perfil."})
    except Exception as e:
        if conn: conn.rollback()
        logging.error(f"Error reportando usuario: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
    finally:
        if conn: conn.close()

@router.post("/api/bloquear/usuario")
async def bloquear_usuario(request: Request, bloqueo: BloqueoRequest):
    conn = None
    try:
        user_id = get_user_id_hybrid(request)
        if not user_id: raise HTTPException(status_code=401, detail="Login requerido")

        conn = get_db_connection()
        cur = conn.cursor()

        if user_id == bloqueo.bloqueado_id:
            return JSONResponse({"status": "error", "message": "No te puedes bloquear a ti mismo"})

        cur.execute("""
            INSERT INTO bloqueos (bloqueador_id, bloqueado_id)
            VALUES (%s, %s)
            ON CONFLICT (bloqueador_id, bloqueado_id) DO NOTHING
        """, (user_id, bloqueo.bloqueado_id))
        conn.commit()

        return JSONResponse({"status": "ok", "message": "Usuario bloqueado. No verás su contenido."})
    except Exception as e:
        if conn: conn.rollback()
        logging.error(f"Error bloqueando usuario: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
    finally:
        if conn: conn.close()

@router.delete("/api/usuario/eliminar")
async def eliminar_cuenta(request: Request):
    conn = None
    try:
        user_id = get_user_id_hybrid(request) 
        if not user_id:
            raise HTTPException(status_code=401, detail="No autorizado")

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("DELETE FROM usuarios WHERE id = %s", (user_id,))
        conn.commit()
        
        request.session.clear() 
        response = JSONResponse({"status": "ok", "message": "Cuenta eliminada"})
        response.delete_cookie("session_token") 
        return response

    except Exception as e:
        if conn: conn.rollback()
        logging.error(f"Error eliminando cuenta {user_id}: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
    finally:
        if conn: conn.close()