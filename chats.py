from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request as StarletteRequest
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.cors import CORSMiddleware
import psycopg2
from datetime import datetime
import logging
import json
import re
from typing import Dict, List
import io

# Configurar el logger
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('app.log') 
    ]
)

# Inicializar FastAPI router
router = APIRouter(prefix="/chats", tags=["chats"])

# Configurar Jinja2 para buscar plantillas en el directorio raíz
templates = Jinja2Templates(directory=".")

# Inicializar FastAPI app
from fastapi import FastAPI
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500", "http://127.0.0.1", "http://localhost:8000", "http://127.0.0.1:8000", "https://prendiax.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configurar middleware de sesiones
app.add_middleware(SessionMiddleware, secret_key="my-secure-secret-key-12345")

# Conexión a la base de datos
def get_db_connection():
    """Establece una conexión a la base de datos PostgreSQL."""
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

# Diccionario para almacenar conexiones WebSocket activas
websocket_connections: Dict[int, WebSocket] = {}

# Función para limpiar nombres de archivo
def sanitize_filename(filename: str) -> str:
    """Limpia nombres de archivo reemplazando caracteres no deseados por guiones bajos."""
    clean_name = re.sub(r'[^a-zA-Z0-9\.\-_]', '_', filename)
    clean_name = re.sub(r'_+', '_', clean_name)
    return clean_name.strip('_')

# Dependencia para obtener la sesión
async def get_session(request: StarletteRequest):
    """Obtiene el ID del usuario desde la sesión."""
    if 'user' not in request.session or 'id' not in request.session['user']:
        raise HTTPException(status_code=401, detail="No autorizado")
    return request.session['user']['id']

# Ruta para obtener el usuario actual
@router.get("/current_user")
async def get_current_user(user_id: int = Depends(get_session)):
    """Devuelve el ID del usuario autenticado."""
    return {"user_id": user_id}

# Ruta para obtener datos de un usuario
@router.get("/user/{user_id}")
async def get_user(user_id: int, requesting_user_id: int = Depends(get_session)):
    """Obtiene los datos de un usuario específico."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT u.id, u.nombre, COALESCE(du.nombre_empresa, '') AS nombre_empresa,
                   du.categoria, du.foto IS NOT NULL AS has_foto
            FROM usuarios u
            LEFT JOIN datos_usuario du ON u.id = du.user_id
            WHERE u.id = %s
        """, (user_id,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if not user:
            logging.warning(f"Usuario no encontrado: {user_id}")
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

        return {
            "id": user[0],
            "nombre": user[1],
            "nombre_empresa": user[2],
            "categoria": user[3] if user[3] else "",
            "foto_perfil_url": f"/foto_perfil/{user_id}" if user[3] and user[4] else ""
        }
    except Exception as e:
        logging.error(f"Error al obtener datos del usuario {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al obtener datos del usuario: {str(e)}")

# Ruta para renderizar chats.html
@router.get("/", response_class=HTMLResponse)
async def get_chats_page(request: Request, user_id: int = Depends(get_session)):
    """Renderiza la página de chats para el usuario autenticado."""
    try:
        logging.debug(f"Renderizando chats.html para user_id: {user_id}")
        return templates.TemplateResponse("chats.html", {
            "request": request,
            "user_id": user_id
        })
    except Exception as e:
        logging.error(f"Error al renderizar chats.html: {e}")
        return RedirectResponse(url="/login", status_code=302)

# Ruta para servir archivos multimedia desde la base de datos
@router.get("/media/{mensaje_id}")
async def get_media(mensaje_id: int, user_id: int = Depends(get_session)):
    """Sirve el contenido multimedia desde la base de datos."""
    try:
        logging.debug(f"Obteniendo multimedia para mensaje_id: {mensaje_id}, user_id: {user_id}")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT m.media_content, m.tipo
            FROM mensajes_chat m
            JOIN chats c ON m.chat_id = c.id
            WHERE m.id = %s AND (c.usuario1_id = %s OR c.usuario2_id = %s)
        """, (mensaje_id, user_id, user_id))
        result = cur.fetchone()
        cur.close()
        conn.close()

        if not result or not result[0]:
            logging.warning(f"Archivo no encontrado para mensaje_id: {mensaje_id}")
            raise HTTPException(status_code=404, detail="Archivo no encontrado")

        media_content, tipo = result
        content_type = {
            'imagen': 'image/jpeg',
            'video': 'video/mp4',
            'voz': 'audio/webm',
            'document': 'application/pdf'
        }.get(tipo, 'application/octet-stream')

        return StreamingResponse(
            content=io.BytesIO(media_content),
            media_type=content_type,
            headers={"Content-Disposition": f"inline; filename=mensaje_{mensaje_id}"}
        )
    except Exception as e:
        logging.error(f"Error al servir multimedia para mensaje_id {mensaje_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al servir multimedia: {str(e)}")

# Ruta para listar chats del usuario
@router.get("/list")
async def list_chats(user_id: int = Depends(get_session), limit: int = 10, offset: int = 0):
    """Lista los chats del usuario con paginación."""
    try:
        logging.debug(f"Listando chats para user_id: {user_id}")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT c.id, 
                   CASE 
                       WHEN c.usuario1_id = %s THEN c.usuario2_id 
                       ELSE c.usuario1_id 
                   END AS otro_usuario_id,
                   COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                   CASE 
                       WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                       ELSE 'explorador'
                   END AS tipo_usuario,
                   m.contenido AS ultimo_mensaje,
                   m.fecha_envio,
                   m.tipo AS tipo_ultimo_mensaje,
                   SUM(CASE WHEN m.leido = FALSE AND m.receptor_id = %s THEN 1 ELSE 0 END) AS unread_count,
                   du.foto IS NOT NULL AS has_foto,
                   m.emisor_id = %s AS es_mio,
                   c.creado_en
            FROM chats c
            JOIN usuarios u ON (CASE 
                                   WHEN c.usuario1_id = %s THEN c.usuario2_id 
                                   ELSE c.usuario1_id 
                               END) = u.id
            LEFT JOIN datos_usuario du ON u.id = du.user_id
            LEFT JOIN mensajes_chat m ON c.ultimo_mensaje_id = m.id
            WHERE c.usuario1_id = %s OR c.usuario2_id = %s
            GROUP BY c.id, c.usuario1_id, c.usuario2_id, u.nombre, du.nombre_empresa, du.categoria, m.contenido, m.fecha_envio, m.tipo, du.foto, m.emisor_id, c.creado_en
            ORDER BY COALESCE(m.fecha_envio, c.creado_en) DESC
            LIMIT %s OFFSET %s
        """, (user_id, user_id, user_id, user_id, user_id, user_id, limit, offset))
        chats = cur.fetchall()
        cur.close()
        conn.close()

        chats_list = [
            {
                "chat_id": row[0],
                "otro_usuario_id": int(row[1]),
                "display_name": row[2],
                "tipo_usuario": row[3],
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[3] == 'emprendedor' and row[8] else "",
                "ultimo_mensaje": row[4] if row[4] else "",
                "fecha_envio": row[5].strftime("%Y-%m-%d %H:%M:%S") if row[5] else "",
                "tipo_ultimo_mensaje": row[6] if row[6] else "texto",
                "unread_count": int(row[7]),
                "es_mio": row[9]
            }
            for row in chats
        ]
        logging.debug(f"Se obtuvieron {len(chats_list)} chats para user_id: {user_id}")
        return chats_list
    except Exception as e:
        logging.error(f"Error al listar chats: {e}")
        raise HTTPException(status_code=500, detail=f"Error al listar chats: {str(e)}")

# Ruta para obtener mensajes de un chat
@router.get("/{chat_id}/mensajes")
async def get_chat_messages(chat_id: int, user_id: int = Depends(get_session), limit: int = 20, offset: int = 0):
    """Obtiene los mensajes de un chat específico con paginación."""
    try:
        logging.debug(f"Obteniendo mensajes para chat_id: {chat_id}, user_id: {user_id}")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, usuario1_id, usuario2_id 
            FROM chats 
            WHERE id = %s AND (usuario1_id = %s OR usuario2_id = %s)
        """, (chat_id, user_id, user_id))
        chat = cur.fetchone()
        if not chat:
            logging.warning(f"Chat no encontrado o no autorizado: chat_id {chat_id}, user_id {user_id}")
            raise HTTPException(status_code=404, detail="Chat no encontrado o no autorizado")

        otro_usuario_id = chat[2] if chat[1] == user_id else chat[1]
        cur.execute("""
            SELECT COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                   CASE 
                       WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                       ELSE 'explorador'
                   END AS tipo_usuario,
                   du.foto IS NOT NULL AS has_foto
            FROM usuarios u
            LEFT JOIN datos_usuario du ON u.id = du.user_id
            WHERE u.id = %s
        """, (otro_usuario_id,))
        otro_usuario = cur.fetchone()

        cur.execute("""
            SELECT m.id, m.emisor_id, m.receptor_id, m.contenido, m.tipo, m.fecha_envio, m.leido
            FROM mensajes_chat m
            WHERE m.chat_id = %s
            ORDER BY m.fecha_envio ASC
            LIMIT %s OFFSET %s
        """, (chat_id, limit, offset))
        mensajes = cur.fetchall()

        cur.execute("""
            UPDATE mensajes_chat 
            SET leido = TRUE 
            WHERE chat_id = %s AND receptor_id = %s AND leido = FALSE
        """, (chat_id, user_id))
        conn.commit()

        cur.close()
        conn.close()

        mensajes_list = [
            {
                "id": row[0],
                "emisor_id": int(row[1]),
                "receptor_id": int(row[2]),
                "contenido": row[3] if row[3] else "",
                "tipo": row[4],
                "media_url": f"/chats/media/{row[0]}" if row[4] in ['imagen', 'video', 'voz', 'document'] else "",
                "fecha_envio": row[5].strftime("%Y-%m-%d %H:%M:%S"),
                "leido": row[6],
                "es_mio": row[1] == user_id
            }
            for row in mensajes
        ]

        return {
            "chat_id": chat_id,
            "otro_usuario": {
                "id": otro_usuario_id,
                "display_name": otro_usuario[0],
                "tipo_usuario": otro_usuario[1],
                "foto_perfil_url": f"/foto_perfil/{otro_usuario_id}" if otro_usuario[1] == 'emprendedor' and otro_usuario[2] else ""
            },
            "mensajes": mensajes_list
        }
    except Exception as e:
        logging.error(f"Error al obtener mensajes del chat {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al obtener mensajes: {str(e)}")

# Ruta para enviar mensaje de texto
@router.post("/{chat_id}/mensaje")
async def send_message(chat_id: int, contenido: str = Form(...), user_id: int = Depends(get_session)):
    """Envía un mensaje de texto a un chat."""
    try:
        contenido = contenido.strip()
        if not contenido:
            logging.warning(f"Mensaje vacío en chat_id: {chat_id}")
            raise HTTPException(status_code=400, detail="El mensaje no puede estar vacío")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, usuario1_id, usuario2_id 
                FROM chats 
                WHERE id = %s AND (usuario1_id = %s OR usuario2_id = %s)
            """, (chat_id, user_id, user_id))
            chat = cur.fetchone()
            if not chat:
                logging.warning(f"Chat no encontrado: chat_id {chat_id}, user_id {user_id}")
                raise HTTPException(status_code=404, detail="Chat no encontrado")

            receptor_id = chat[2] if chat[1] == user_id else chat[1]
            cur.execute("""
                INSERT INTO mensajes_chat (chat_id, emisor_id, receptor_id, contenido, tipo, fecha_envio)
                VALUES (%s, %s, %s, %s, 'texto', CURRENT_TIMESTAMP)
                RETURNING id, fecha_envio
            """, (chat_id, user_id, receptor_id, contenido))
            mensaje = cur.fetchone()

            cur.execute("""
                UPDATE chats 
                SET ultimo_mensaje_id = %s 
                WHERE id = %s
            """, (mensaje[0], chat_id))
            conn.commit()

            message_data = {
                "id": mensaje[0],
                "chat_id": chat_id,
                "emisor_id": user_id,
                "receptor_id": receptor_id,
                "contenido": contenido,
                "tipo": "texto",
                "media_url": "",
                "fecha_envio": mensaje[1].strftime("%Y-%m-%d %H:%M:%S"),
                "leido": False,
                "es_mio": True
            }
            if receptor_id in websocket_connections:
                try:
                    await websocket_connections[receptor_id].send_text(json.dumps(message_data))
                    logging.debug(f"Notificación WebSocket enviada a receptor_id: {receptor_id}")
                except Exception as e:
                    logging.error(f"Error al enviar WebSocket a receptor_id {receptor_id}: {e}")
                    del websocket_connections[receptor_id]
        except Exception as e:
            conn.rollback()
            logging.error(f"Error al enviar mensaje en chat {chat_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Error al enviar mensaje: {str(e)}")
        finally:
            cur.close()
            conn.close()

        logging.debug(f"Mensaje enviado en chat_id: {chat_id}, mensaje_id: {mensaje[0]}")
        return message_data
    except Exception as e:
        logging.error(f"Error al enviar mensaje en chat {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al enviar mensaje: {str(e)}")

# Ruta para enviar multimedia (imagen o video)
@router.post("/{chat_id}/media")
async def send_media(chat_id: int, file: UploadFile = File(...), user_id: int = Depends(get_session)):
    try:
        if not file or file.size == 0:
            logging.warning(f"Archivo vacío en chat_id: {chat_id}")
            raise HTTPException(status_code=400, detail="El archivo no puede estar vacío")
        
        if file.size > MAX_FILE_SIZE:
            logging.warning(f"Archivo demasiado grande en chat_id: {chat_id}, tamaño: {file.size}")
            raise HTTPException(status_code=400, detail=f"El archivo excede el tamaño máximo de {MAX_FILE_SIZE // (1024 * 1024)} MB")

        content_type = file.content_type
        if content_type.startswith('image/'):
            tipo = 'imagen'
        elif content_type.startswith('video/'):
            tipo = 'video'
        else:
            logging.warning(f"Tipo de archivo no soportado: {content_type}")
            raise HTTPException(status_code=400, detail="Solo se permiten imágenes y videos")

        file_content = await file.read()

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, usuario1_id, usuario2_id 
                FROM chats 
                WHERE id = %s AND (usuario1_id = %s OR usuario2_id = %s)
            """, (chat_id, user_id, user_id))
            chat = cur.fetchone()
            if not chat:
                logging.warning(f"Chat no encontrado: chat_id {chat_id}, user_id {user_id}")
                raise HTTPException(status_code=404, detail="Chat no encontrado")

            receptor_id = chat[2] if chat[1] == user_id else chat[1]
            cur.execute("""
                INSERT INTO mensajes_chat (chat_id, emisor_id, receptor_id, tipo, media_content, fecha_envio)
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING id, fecha_envio
            """, (chat_id, user_id, receptor_id, tipo, psycopg2.Binary(file_content)))
            mensaje = cur.fetchone()

            cur.execute("""
                UPDATE chats 
                SET ultimo_mensaje_id = %s 
                WHERE id = %s
            """, (mensaje[0], chat_id))
            conn.commit()

            message_data = {
                "id": mensaje[0],
                "chat_id": chat_id,
                "emisor_id": user_id,
                "receptor_id": receptor_id,
                "contenido": "",
                "tipo": tipo,
                "media_url": f"/chats/media/{mensaje[0]}",
                "fecha_envio": mensaje[1].strftime("%Y-%m-%d %H:%M:%S"),
                "leido": False,
                "es_mio": True
            }
            if receptor_id in websocket_connections:
                try:
                    await websocket_connections[receptor_id].send_text(json.dumps(message_data))
                    logging.debug(f"Notificación WebSocket enviada a receptor_id: {receptor_id}")
                except Exception as e:
                    logging.error(f"Error al enviar WebSocket a receptor_id {receptor_id}: {e}")
                    del websocket_connections[receptor_id]
        except Exception as e:
            conn.rollback()
            logging.error(f"Error al enviar multimedia en chat {chat_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Error al enviar multimedia: {str(e)}")
        finally:
            cur.close()
            conn.close()

        logging.debug(f"Multimedia enviada en chat_id: {chat_id}, mensaje_id: {mensaje[0]}")
        return message_data
    except Exception as e:
        logging.error(f"Error al enviar multimedia en chat {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al enviar multimedia: {str(e)}")

# Ruta para enviar nota de voz
@router.post("/{chat_id}/voz")
async def send_voice_note(chat_id: int, file: UploadFile = File(...), user_id: int = Depends(get_session)):
    try:
        if not file or file.size == 0:
            logging.warning(f"Archivo vacío en chat_id: {chat_id}")
            raise HTTPException(status_code=400, detail="El archivo no puede estar vacío")
        
        if file.size > MAX_FILE_SIZE:
            logging.warning(f"Archivo demasiado grande en chat_id: {chat_id}, tamaño: {file.size}")
            raise HTTPException(status_code=400, detail=f"El archivo excede el tamaño máximo de {MAX_FILE_SIZE // (1024 * 1024)} MB")

        content_type = file.content_type
        if not content_type.startswith('audio/'):
            logging.warning(f"Tipo de archivo no soportado: {content_type}")
            raise HTTPException(status_code=400, detail="Solo se permiten archivos de audio")

        file_content = await file.read()

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, usuario1_id, usuario2_id 
                FROM chats 
                WHERE id = %s AND (usuario1_id = %s OR usuario2_id = %s)
            """, (chat_id, user_id, user_id))
            chat = cur.fetchone()
            if not chat:
                logging.warning(f"Chat no encontrado: chat_id {chat_id}, user_id {user_id}")
                raise HTTPException(status_code=404, detail="Chat no encontrado")

            receptor_id = chat[2] if chat[1] == user_id else chat[1]
            cur.execute("""
                INSERT INTO mensajes_chat (chat_id, emisor_id, receptor_id, tipo, media_content, fecha_envio)
                VALUES (%s, %s, %s, 'voz', %s, CURRENT_TIMESTAMP)
                RETURNING id, fecha_envio
            """, (chat_id, user_id, receptor_id, psycopg2.Binary(file_content)))
            mensaje = cur.fetchone()

            cur.execute("""
                UPDATE chats 
                SET ultimo_mensaje_id = %s 
                WHERE id = %s
            """, (mensaje[0], chat_id))
            conn.commit()

            message_data = {
                "id": mensaje[0],
                "chat_id": chat_id,
                "emisor_id": user_id,
                "receptor_id": receptor_id,
                "contenido": "",
                "tipo": "voz",
                "media_url": f"/chats/media/{mensaje[0]}",
                "fecha_envio": mensaje[1].strftime("%Y-%m-%d %H:%M:%S"),
                "leido": False,
                "es_mio": True
            }
            if receptor_id in websocket_connections:
                try:
                    await websocket_connections[receptor_id].send_text(json.dumps(message_data))
                    logging.debug(f"Notificación WebSocket enviada a receptor_id: {receptor_id}")
                except Exception as e:
                    logging.error(f"Error al enviar WebSocket a receptor_id {receptor_id}: {e}")
                    del websocket_connections[receptor_id]
        except Exception as e:
            conn.rollback()
            logging.error(f"Error al enviar nota de voz en chat {chat_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Error al enviar nota de voz: {str(e)}")
        finally:
            cur.close()
            conn.close()

        logging.debug(f"Nota de voz enviada en chat_id: {chat_id}, mensaje_id: {mensaje[0]}")
        return message_data
    except Exception as e:
        logging.error(f"Error al enviar nota de voz en chat {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al enviar nota de voz: {str(e)}")

# Ruta para enviar documentos
@router.post("/{chat_id}/document")
async def send_document(chat_id: int, file: UploadFile = File(...), contenido: str = Form(None), user_id: int = Depends(get_session)):
    try:
        if not file or file.size == 0:
            logging.warning(f"Archivo vacío en chat_id: {chat_id}")
            raise HTTPException(status_code=400, detail="El archivo no puede estar vacío")
        
        if file.size > MAX_FILE_SIZE:
            logging.warning(f"Archivo demasiado grande en chat_id: {chat_id}, tamaño: {file.size}")
            raise HTTPException(status_code=400, detail=f"El archivo excede el tamaño máximo de {MAX_FILE_SIZE // (1024 * 1024)} MB")

        content_type = file.content_type
        allowed_types = ['application/pdf', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'text/plain']
        if content_type not in allowed_types:
            logging.warning(f"Tipo de archivo no soportado: {content_type}")
            raise HTTPException(status_code=400, detail="Solo se permiten documentos PDF, DOC, DOCX o TXT")

        file_content = await file.read()
        clean_filename = sanitize_filename(file.filename)

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, usuario1_id, usuario2_id 
                FROM chats 
                WHERE id = %s AND (usuario1_id = %s OR usuario2_id = %s)
            """, (chat_id, user_id, user_id))
            chat = cur.fetchone()
            if not chat:
                logging.warning(f"Chat no encontrado: chat_id {chat_id}, user_id {user_id}")
                raise HTTPException(status_code=404, detail="Chat no encontrado")

            receptor_id = chat[2] if chat[1] == user_id else chat[1]
            cur.execute("""
                INSERT INTO mensajes_chat (chat_id, emisor_id, receptor_id, contenido, tipo, media_content, fecha_envio)
                VALUES (%s, %s, %s, %s, 'document', %s, CURRENT_TIMESTAMP)
                RETURNING id, fecha_envio
            """, (chat_id, user_id, receptor_id, contenido or clean_filename, psycopg2.Binary(file_content)))
            mensaje = cur.fetchone()

            cur.execute("""
                UPDATE chats 
                SET ultimo_mensaje_id = %s 
                WHERE id = %s
            """, (mensaje[0], chat_id))
            conn.commit()

            message_data = {
                "id": mensaje[0],
                "chat_id": chat_id,
                "emisor_id": user_id,
                "receptor_id": receptor_id,
                "contenido": contenido or clean_filename,
                "tipo": "document",
                "media_url": f"/chats/media/{mensaje[0]}",
                "fecha_envio": mensaje[1].strftime("%Y-%m-%d %H:%M:%S"),
                "leido": False,
                "es_mio": True
            }
            if receptor_id in websocket_connections:
                try:
                    await websocket_connections[receptor_id].send_text(json.dumps(message_data))
                    logging.debug(f"Notificación WebSocket enviada a receptor_id: {receptor_id}")
                except Exception as e:
                    logging.error(f"Error al enviar WebSocket a receptor_id {receptor_id}: {e}")
                    del websocket_connections[receptor_id]
        except Exception as e:
            conn.rollback()
            logging.error(f"Error al enviar documento en chat {chat_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Error al enviar documento: {str(e)}")
        finally:
            cur.close()
            conn.close()

        logging.debug(f"Documento enviado en chat_id: {chat_id}, mensaje_id: {mensaje[0]}")
        return message_data
    except Exception as e:
        logging.error(f"Error al enviar documento en chat {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al enviar documento: {str(e)}")

# Ruta para buscar chats
@router.get("/buscar")
async def search_chats(query: str, user_id: int = Depends(get_session), limit: int = 10, offset: int = 0):
    """Busca chats por nombre del usuario o empresa."""
    try:
        query = query.strip().lower()
        if not query:
            logging.warning("Query vacío en /chats/buscar")
            raise HTTPException(status_code=400, detail="La búsqueda no puede estar vacía")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT c.id, 
                   CASE 
                       WHEN c.usuario1_id = %s THEN c.usuario2_id 
                       ELSE c.usuario1_id 
                   END AS otro_usuario_id,
                   COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                   CASE 
                       WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                       ELSE 'explorador'
                   END AS tipo_usuario,
                   m.contenido AS ultimo_mensaje,
                   m.fecha_envio,
                   m.tipo AS tipo_ultimo_mensaje,
                   SUM(CASE WHEN m.leido = FALSE AND m.receptor_id = %s THEN 1 ELSE 0 END) AS unread_count,
                   du.foto IS NOT NULL AS has_foto
            FROM chats c
            JOIN usuarios u ON (CASE 
                                   WHEN c.usuario1_id = %s THEN c.usuario2_id 
                                   ELSE c.usuario1_id 
                               END) = u.id
            LEFT JOIN datos_usuario du ON u.id = du.user_id
            LEFT JOIN mensajes_chat m ON c.ultimo_mensaje_id = m.id
            WHERE (c.usuario1_id = %s OR c.usuario2_id = %s)
              AND (LOWER(COALESCE(du.nombre_empresa, u.nombre)) LIKE %s)
            GROUP BY c.id, c.usuario1_id, c.usuario2_id, u.nombre, du.nombre_empresa, du.categoria, m.contenido, m.fecha_envio, m.tipo, du.foto
            ORDER BY m.fecha_envio DESC NULLS LAST
            LIMIT %s OFFSET %s
        """, (user_id, user_id, user_id, user_id, user_id, f"%{query}%", limit, offset))
        chats = cur.fetchall()
        cur.close()
        conn.close()

        chats_list = [
            {
                "chat_id": row[0],
                "otro_usuario_id": int(row[1]),
                "display_name": row[2],
                "tipo_usuario": row[3],
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[3] == 'emprendedor' and row[8] else "",
                "ultimo_mensaje": row[4] if row[4] else "",
                "fecha_envio": row[5].strftime("%Y-%m-%d %H:%M:%S") if row[5] else "",
                "tipo_ultimo_mensaje": row[6] if row[6] else "texto",
                "unread_count": int(row[7])
            }
            for row in chats
        ]
        logging.debug(f"Se encontraron {len(chats_list)} chats para query: {query}, user_id: {user_id}")
        return chats_list
    except Exception as e:
        logging.error(f"Error al buscar chats: {e}")
        raise HTTPException(status_code=500, detail=f"Error al buscar chats: {str(e)}")

# Ruta para iniciar un chat con otro usuario
@router.post("/iniciar/{otro_usuario_id}")
async def start_chat(otro_usuario_id: int, user_id: int = Depends(get_session)):
    """Inicia un nuevo chat con otro usuario."""
    try:
        if user_id == otro_usuario_id:
            logging.warning(f"Intento de iniciar chat consigo mismo: user_id {user_id}")
            raise HTTPException(status_code=400, detail="No puedes iniciar un chat contigo mismo")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM usuarios WHERE id = %s", (otro_usuario_id,))
            if not cur.fetchone():
                logging.warning(f"Usuario no encontrado: {otro_usuario_id}")
                raise HTTPException(status_code=404, detail="Usuario no encontrado")

            cur.execute("""
                SELECT id 
                FROM chats 
                WHERE (usuario1_id = %s AND usuario2_id = %s) OR (usuario1_id = %s AND usuario2_id = %s)
            """, (user_id, otro_usuario_id, otro_usuario_id, user_id))
            chat = cur.fetchone()
            if chat:
                logging.debug(f"Chat ya existe: chat_id {chat[0]}")
                return {"chat_id": chat[0]}

            cur.execute("""
                INSERT INTO chats (usuario1_id, usuario2_id, creado_en)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                RETURNING id
            """, (user_id, otro_usuario_id))
            chat_id = cur.fetchone()[0]
            conn.commit()

            message_data = {
                "chat_id": chat_id,
                "otro_usuario_id": user_id,
                "tipo": "nuevo_chat",
                "fecha_creacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            if otro_usuario_id in websocket_connections:
                try:
                    await websocket_connections[otro_usuario_id].send_text(json.dumps(message_data))
                    logging.debug(f"Notificación WebSocket de nuevo chat enviada a receptor_id: {otro_usuario_id}")
                except Exception as e:
                    logging.error(f"Error al enviar WebSocket a receptor_id {otro_usuario_id}: {e}")
                    del websocket_connections[otro_usuario_id]
        except Exception as e:
            conn.rollback()
            logging.error(f"Error al iniciar chat con usuario {otro_usuario_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Error al iniciar chat: {str(e)}")
        finally:
            cur.close()
            conn.close()

        logging.debug(f"Nuevo chat creado: chat_id {chat_id}, user_id {user_id}, otro_usuario_id {otro_usuario_id}")
        return {"chat_id": chat_id}
    except Exception as e:
        logging.error(f"Error al iniciar chat con usuario {otro_usuario_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al iniciar chat: {str(e)}")

# Ruta para eliminar un chat
@router.delete("/{chat_id}")
async def delete_chat(chat_id: int, user_id: int = Depends(get_session)):
    """Elimina un chat y sus mensajes asociados."""
    try:
        logging.debug(f"Intentando eliminar chat_id: {chat_id}, user_id: {user_id}")
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, usuario1_id, usuario2_id 
                FROM chats 
                WHERE id = %s AND (usuario1_id = %s OR usuario2_id = %s)
            """, (chat_id, user_id, user_id))
            chat = cur.fetchone()
            if not chat:
                logging.warning(f"Chat no encontrado o no autorizado: chat_id {chat_id}, user_id {user_id}")
                raise HTTPException(status_code=404, detail="Chat no encontrado o no autorizado")

            receptor_id = chat[2] if chat[1] == user_id else chat[1]
            cur.execute("DELETE FROM mensajes_chat WHERE chat_id = %s", (chat_id,))
            cur.execute("DELETE FROM chats WHERE id = %s", (chat_id,))
            conn.commit()

            message_data = {
                "chat_id": chat_id,
                "otro_usuario_id": user_id,
                "tipo": "chat_deleted",
                "fecha_eliminacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            if receptor_id in websocket_connections:
                try:
                    await websocket_connections[receptor_id].send_text(json.dumps(message_data))
                    logging.debug(f"Notificación WebSocket de eliminación enviada a receptor_id: {receptor_id}")
                except Exception as e:
                    logging.error(f"Error al enviar WebSocket a receptor_id {receptor_id}: {e}")
                    del websocket_connections[receptor_id]
        except Exception as e:
            conn.rollback()
            logging.error(f"Error al eliminar chat {chat_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Error al eliminar chat: {str(e)}")
        finally:
            cur.close()
            conn.close()

        logging.debug(f"Chat eliminado exitosamente: chat_id {chat_id}")
        return {"message": "Chat eliminado exitosamente"}
    except Exception as e:
        logging.error(f"Error al eliminar chat {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al eliminar chat: {str(e)}")

# Ruta para WebSocket con autenticación
@router.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int, token: str = None):
    """Maneja conexiones WebSocket para notificaciones en tiempo real."""
    await websocket.accept()
    try:
        logging.debug(f"WebSocket conexión intentada para user_id: {user_id}")
        session_cookie = websocket.cookies.get('session')
        logging.debug(f"Cookie de sesión recibida: {session_cookie}, Token: {token}")

        # Verificar cookie o token
        if not session_cookie and not token:
            logging.warning(f"WebSocket rechazado: No se proporcionó cookie de sesión ni token para user_id {user_id}")
            await websocket.close(code=1008, reason="No autorizado: Ni cookie ni token proporcionados")
            return

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM usuarios WHERE id = %s", (user_id,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if not user:
            logging.warning(f"WebSocket rechazado: Usuario no encontrado para user_id {user_id}")
            await websocket.close(code=1008, reason="No autorizado: Usuario no encontrado")
            return

        # Opcional: Verificar token si se proporciona
        if token:
            # Implementa lógica para validar el token (por ejemplo, contra una tabla de sesiones)
            logging.debug(f"Validando token para user_id {user_id}: {token}")
            # Ejemplo: cur.execute("SELECT user_id FROM sessions WHERE token = %s", (token,))
            # Asegúrate de tener una tabla de sesiones si usas tokens

        websocket_connections[user_id] = websocket
        logging.debug(f"WebSocket conectado para user_id: {user_id}")

        try:
            while True:
                data = await websocket.receive_text()
                logging.debug(f"Mensaje recibido por WebSocket de user_id {user_id}: {data}")
                await websocket.send_text(json.dumps({"type": "ping"}))
        except WebSocketDisconnect:
            logging.debug(f"WebSocket desconectado para user_id {user_id}")
            del websocket_connections[user_id]
        except Exception as e:
            logging.error(f"Error en WebSocket para user_id {user_id}: {e}")
            del websocket_connections[user_id]
    except Exception as e:
        logging.error(f"Error al autenticar WebSocket para user_id {user_id}: {e}")
        await websocket.close(code=1008, reason=f"No autorizado: {str(e)}")

# Ruta para servir la foto de perfil de un usuario
@router.get("/user/{user_id}/foto_perfil")
async def get_user_profile_picture(user_id: int, requesting_user_id: int = Depends(get_session)):
    """Sirve la foto de perfil de un usuario desde la base de datos."""
    try:
        logging.debug(f"Obteniendo foto de perfil para user_id: {user_id}, solicitado por requesting_user_id: {requesting_user_id}")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT CASE 
                       WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                       ELSE 'explorador'
                   END AS tipo_usuario,
                   du.foto
            FROM usuarios u
            LEFT JOIN datos_usuario du ON u.id = du.user_id
            WHERE u.id = %s
        """, (user_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()

        if not result or result[0] != 'emprendedor' or not result[1]:
            logging.debug(f"No se encontró foto de perfil para user_id {user_id} o no es emprendedor, sirviendo foto predeterminada")
            with open("default_profile.jpg", "rb") as f:
                default_foto = f.read()
            return StreamingResponse(
                content=io.BytesIO(default_foto),
                media_type="image/jpeg",
                headers={"Content-Disposition": f"inline; filename=foto_perfil_default.jpg"}
            )

        foto = result[1]
        logging.debug(f"Foto de perfil encontrada para user_id: {user_id}, tamaño: {len(foto)} bytes")
        return StreamingResponse(
            content=io.BytesIO(foto),
            media_type="image/jpeg",
            headers={"Content-Disposition": f"inline; filename=foto_perfil_{user_id}.jpg"}
        )
    except Exception as e:
        logging.error(f"Error al servir foto de perfil para user_id {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al servir foto de perfil: {str(e)}")

@router.get("/unread_count")
async def get_unread_count(user_id: int = Depends(get_session)):
    """Devuelve el número total de mensajes no leídos para el usuario."""
    try:
        logging.debug(f"Obteniendo conteo de mensajes no leídos para user_id: {user_id}")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) 
            FROM mensajes_chat m
            JOIN chats c ON m.chat_id = c.id
            WHERE m.receptor_id = %s AND m.leido = FALSE
            AND (c.usuario1_id = %s OR c.usuario2_id = %s)
        """, (user_id, user_id, user_id))
        unread_count = cur.fetchone()[0]
        cur.close()
        conn.close()
        logging.debug(f"Se encontraron {unread_count} mensajes no leídos para user_id: {user_id}")
        return {"unread_count": unread_count}
    except Exception as e:
        logging.error(f"Error al obtener conteo de mensajes no leídos: {e}")
        raise HTTPException(status_code=500, detail=f"Error al obtener conteo de mensajes no leídos: {str(e)}")

# Añadir el router al app principal
app.include_router(router)