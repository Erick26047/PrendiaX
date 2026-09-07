from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException, Header, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse, StreamingResponse, Response, FileResponse
from fastapi.templating import Jinja2Templates
from typing import List, Dict
import psycopg2
from datetime import datetime
import logging
import os
import re
import json 
import uuid
import shutil
from pydantic import BaseModel
import jwt 
from firebase_admin import messaging

router = APIRouter(prefix="/chats", tags=["chats"])

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

SECRET_KEY_JWT = "Elbicho7"

MEDIA_DIR = os.path.join("media", "chats")
os.makedirs(MEDIA_DIR, exist_ok=True)

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
        return psycopg2.connect(
            host="localhost",
            database="prendia_db",
            user="postgres",
            password="Elbicho7",
        )
    except Exception as e:
        logging.error(f"Error BD: {e}")
        raise HTTPException(status_code=500, detail="Error BD")

MAX_FILE_SIZE = 100 * 1024 * 1024 

class InterestRequest(BaseModel): user_id: int
class CommentRequest(BaseModel):
    contenido: str
    parent_id: int | None = None
    reply_to_user_id: int | None = None
class ReporteUsuarioRequest(BaseModel):
    usuario_reportado_id: int
    motivo: str
class BloqueoRequest(BaseModel): bloqueado_id: int
class ReviewRequest(BaseModel):
    texto: str
    calificacion: int
class ReportePublicacionRequest(BaseModel):
    publicacion_id: int
    motivo: str

websocket_connections: Dict[int, WebSocket] = {}

def sanitize_filename(filename: str) -> str:
    clean_name = re.sub(r'[^a-zA-Z0-9\.\-_]', '_', filename)
    clean_name = re.sub(r'_+', '_', clean_name)
    return clean_name.strip('_')

def verificar_bloqueo(cur, user_a: int, user_b: int):
    cur.execute("""
        SELECT 1 FROM bloqueos 
        WHERE (bloqueador_id = %s AND bloqueado_id = %s) 
           OR (bloqueador_id = %s AND bloqueado_id = %s)
    """, (user_a, user_b, user_b, user_a))
    if cur.fetchone():
        raise HTTPException(status_code=403, detail="Bloqueo activo")

def send_file_range_requests(request: Request, file_path: str, content_type: str, filename: str):
    file_size = os.path.getsize(file_path)
    range_header = request.headers.get("range")

    if not range_header:
        return FileResponse(
            path=file_path,
            media_type=content_type,
            headers={"Content-Disposition": f"inline; filename={filename}"}
        )

    try:
        start, end = 0, None
        match = range_header.strip().replace("bytes=", "").split("-")
        if match[0]: start = int(match[0])
        if len(match) > 1 and match[1]: end = int(match[1])
        
        if end is None: end = file_size - 1
        if start >= file_size: raise HTTPException(status_code=416, detail="Range Not Satisfiable")
        if end >= file_size: end = file_size - 1

        chunk_length = end - start + 1

        def file_iterator(path, offset, bytes_to_read):
            with open(path, "rb") as f:
                f.seek(offset)
                remaining = bytes_to_read
                while remaining > 0:
                    chunk_size = min(65536, remaining)
                    data = f.read(chunk_size)
                    if not data: break
                    remaining -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_length),
            "Content-Disposition": f"inline; filename={filename}",
        }

        return StreamingResponse(
            file_iterator(file_path, start, chunk_length),
            status_code=206,
            headers=headers,
            media_type=content_type
        )
    except Exception:
        return FileResponse(
            path=file_path,
            media_type=content_type,
            headers={"Content-Disposition": f"inline; filename={filename}"}
        )

def get_user_id_hybrid(request: Request):
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        try:
            payload = jwt.decode(token, SECRET_KEY_JWT, algorithms=["HS256"])
            user_id = payload.get("user_id") or payload.get("sub")
            if user_id: return int(user_id)
        except Exception:
            if "jwt_app_" in token:
                 try: return int(token.split("jwt_app_")[1])
                 except: pass
    if 'user' in request.session and 'id' in request.session['user']:
        return int(request.session['user']['id'])
    return None

async def get_session(request: Request):
    if 'user' in request.session and 'id' in request.session['user']:
        return request.session['user']['id']
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        try:
            payload = jwt.decode(token, SECRET_KEY_JWT, algorithms=["HS256"])
            user_id = payload.get("user_id") or payload.get("sub")
            if user_id: return int(user_id)
        except Exception:
            if "jwt_app_" in token:
                try: return int(token.split("jwt_app_")[1])
                except: pass
    raise HTTPException(status_code=401, detail="No autorizado")

@router.get("/current_user")
async def get_current_user_endpoint(user_id: int = Depends(get_session)):
    return {"user_id": user_id}

@router.get("/user/{user_id}")
async def get_user_info(user_id: int, requesting_user_id: int = Depends(get_session)):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT u.id, u.nombre, COALESCE(du.nombre_empresa, '') AS nombre_empresa,
                   du.categoria, (du.foto IS NOT NULL OR du.ruta_foto IS NOT NULL) AS has_foto
            FROM usuarios u LEFT JOIN datos_usuario du ON u.id = du.user_id WHERE u.id = %s
        """, (user_id,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if not user: raise HTTPException(status_code=404, detail="Usuario no encontrado")

        return {
            "id": user[0],
            "nombre": user[1],
            "nombre_empresa": user[2],
            "categoria": user[3] if user[3] else "",
            "foto_perfil_url": f"/foto_perfil/{user_id}" if user[4] else ""
        }
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.get("/", response_class=HTMLResponse)
async def get_chats_page(request: Request, user_id: int = Depends(get_session)):
    try: return templates.TemplateResponse("chats.html", {"request": request, "user_id": user_id})
    except Exception:
        if "application/json" in request.headers.get("accept", ""): raise HTTPException(status_code=401)
        return RedirectResponse(url="/login", status_code=302)

@router.get("/media/{mensaje_id}")
async def get_media_chat(request: Request, mensaje_id: int, user_id: int = Depends(get_session)):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT m.media_content, m.contenido, m.tipo
            FROM mensajes_chat m JOIN chats c ON m.chat_id = c.id
            WHERE m.id = %s AND (c.usuario1_id = %s OR c.usuario2_id = %s)
        """, (mensaje_id, user_id, user_id))
        result = cur.fetchone()
        cur.close()
        conn.close()

        if not result or not result[0]: raise HTTPException(status_code=404, detail="Archivo no encontrado")

        file_path, contenido, tipo = result
        
        if not os.path.exists(file_path): raise HTTPException(status_code=404, detail="Archivo eliminado")

        content_type = {
            'imagen': 'image/jpeg', 'video': 'video/mp4',
            'voz': 'audio/mp4', 'document': 'application/pdf'
        }.get(tipo, 'application/octet-stream')

        filename = os.path.basename(file_path)
        if tipo == 'document' and contenido: filename = contenido 
        
        return send_file_range_requests(request, file_path, content_type, filename)
    except HTTPException as he: raise he
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.get("/list")
async def list_chats(user_id: int = Depends(get_session), limit: int = 10, offset: int = 0):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT c.id, 
                   CASE WHEN c.usuario1_id = %s THEN c.usuario2_id ELSE c.usuario1_id END AS otro_usuario_id,
                   COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                   CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END AS tipo_usuario,
                   m.contenido AS ultimo_mensaje, m.fecha_envio, m.tipo AS tipo_ultimo_mensaje,
                   SUM(CASE WHEN m.leido = FALSE AND m.receptor_id = %s THEN 1 ELSE 0 END) AS unread_count,
                   (du.foto IS NOT NULL OR du.ruta_foto IS NOT NULL) AS has_foto,
                   m.emisor_id = %s AS es_mio, c.creado_en
            FROM chats c
            JOIN usuarios u ON (CASE WHEN c.usuario1_id = %s THEN c.usuario2_id ELSE c.usuario1_id END) = u.id
            LEFT JOIN datos_usuario du ON u.id = du.user_id
            LEFT JOIN mensajes_chat m ON c.ultimo_mensaje_id = m.id
            WHERE c.usuario1_id = %s OR c.usuario2_id = %s
            GROUP BY c.id, c.usuario1_id, c.usuario2_id, u.nombre, du.nombre_empresa, du.categoria, m.contenido, m.fecha_envio, m.tipo, du.foto, du.ruta_foto, m.emisor_id, c.creado_en
            ORDER BY COALESCE(m.fecha_envio, c.creado_en) DESC
            LIMIT %s OFFSET %s
        """, (user_id, user_id, user_id, user_id, user_id, user_id, limit, offset))
        chats = cur.fetchall()
        cur.close()
        conn.close()

        chats_list = [
            {
                "chat_id": row[0], "otro_usuario_id": int(row[1]), "display_name": row[2], "tipo_usuario": row[3],
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[8] else "",
                "ultimo_mensaje": row[4] if row[6] in ['texto', 'document'] else (f"[{row[6].upper()}]" if row[6] else ""),
                "fecha_envio": row[5].strftime("%Y-%m-%d %H:%M:%S") if row[5] else "",
                "tipo_ultimo_mensaje": row[6] if row[6] else "texto",
                "unread_count": int(row[7]), "es_mio": row[9]
            }
            for row in chats
        ]
        return chats_list
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.get("/{chat_id}/mensajes")
async def get_chat_messages(chat_id: int, user_id: int = Depends(get_session), limit: int = 20, offset: int = 0):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, usuario1_id, usuario2_id FROM chats WHERE id = %s AND (usuario1_id = %s OR usuario2_id = %s)", (chat_id, user_id, user_id))
        chat = cur.fetchone()
        if not chat: raise HTTPException(status_code=404, detail="Chat no encontrado")

        otro_usuario_id = chat[2] if chat[1] == user_id else chat[1]
        cur.execute("""
            SELECT COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                   CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END AS tipo_usuario,
                   (du.foto IS NOT NULL OR du.ruta_foto IS NOT NULL) AS has_foto
            FROM usuarios u LEFT JOIN datos_usuario du ON u.id = du.user_id WHERE u.id = %s
        """, (otro_usuario_id,))
        otro_usuario = cur.fetchone()

        cur.execute("""
            SELECT m.id, m.emisor_id, m.receptor_id, m.contenido, m.tipo, m.fecha_envio, m.leido
            FROM mensajes_chat m WHERE m.chat_id = %s ORDER BY m.fecha_envio DESC LIMIT %s OFFSET %s
        """, (chat_id, limit, offset))
        mensajes = cur.fetchall()
        mensajes.reverse()

        cur.execute("UPDATE mensajes_chat SET leido = TRUE WHERE chat_id = %s AND receptor_id = %s AND leido = FALSE", (chat_id, user_id))
        conn.commit()
        cur.close()
        conn.close()

        mensajes_list = [
            {
                "id": row[0], "emisor_id": int(row[1]), "receptor_id": int(row[2]),
                "contenido": row[3] if row[4] in ['texto', 'document'] else "", "tipo": row[4],
                "media_url": f"/chats/media/{row[0]}" if row[4] in ['imagen', 'video', 'voz', 'document'] else "",
                "fecha_envio": row[5].strftime("%Y-%m-%d %H:%M:%S"), "leido": row[6], "es_mio": row[1] == user_id
            }
            for row in mensajes
        ]
        return {
            "chat_id": chat_id,
            "otro_usuario": {
                "id": otro_usuario_id, "display_name": otro_usuario[0], "tipo_usuario": otro_usuario[1],
                "foto_perfil_url": f"/foto_perfil/{otro_usuario_id}" if otro_usuario[2] else ""
            },
            "mensajes": mensajes_list
        }
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.post("/{chat_id}/mensaje")
async def send_message(chat_id: int, contenido: str = Form(...), user_id: int = Depends(get_session)):
    try:
        contenido = contenido.strip()
        if not contenido: raise HTTPException(status_code=400, detail="Mensaje vacío")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, usuario1_id, usuario2_id FROM chats WHERE id = %s AND (usuario1_id = %s OR usuario2_id = %s)", (chat_id, user_id, user_id))
        chat = cur.fetchone()
        if not chat: raise HTTPException(status_code=404, detail="Chat no encontrado")

        receptor_id = chat[2] if chat[1] == user_id else chat[1]
        verificar_bloqueo(cur, user_id, receptor_id)

        cur.execute("SELECT (SELECT COALESCE(du.nombre_empresa, u.nombre) FROM usuarios u LEFT JOIN datos_usuario du ON u.id = du.user_id WHERE u.id = %s), (SELECT fcm_token FROM usuarios WHERE id = %s)", (user_id, receptor_id))
        row = cur.fetchone()
        emisor_nombre = row[0] if row and row[0] else "Usuario"
        fcm_token = row[1] if row and row[1] else None

        cur.execute("""
            INSERT INTO mensajes_chat (chat_id, emisor_id, receptor_id, contenido, tipo, fecha_envio)
            VALUES (%s, %s, %s, %s, 'texto', CURRENT_TIMESTAMP)
            RETURNING id, fecha_envio
        """, (chat_id, user_id, receptor_id, contenido))
        mensaje = cur.fetchone()
        cur.execute("UPDATE chats SET ultimo_mensaje_id = %s WHERE id = %s", (mensaje[0], chat_id))
        conn.commit()

        message_data = {
            "id": mensaje[0], "chat_id": chat_id, "emisor_id": user_id, "receptor_id": receptor_id,
            "contenido": contenido, "tipo": "texto", "media_url": "",
            "fecha_envio": mensaje[1].strftime("%Y-%m-%d %H:%M:%S"), "leido": False, "es_mio": True
        }
        if receptor_id in websocket_connections:
            try: await websocket_connections[receptor_id].send_text(json.dumps(message_data))
            except: del websocket_connections[receptor_id]

        if fcm_token:
            try:
                push_msg = messaging.Message(notification=messaging.Notification(title=f"Nuevo mensaje de {emisor_nombre}", body=contenido), apns=messaging.APNSConfig(payload=messaging.APNSPayload(aps=messaging.Aps(sound="default"))), data={"tipo": "chat", "chat_id": str(chat_id)}, token=fcm_token)
                messaging.send(push_msg)
            except Exception as e: logging.error(f"Error Push: {e}")

        cur.close()
        conn.close()
        return message_data
    except Exception as e:
        if 'conn' in locals() and conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{chat_id}/media")
async def send_media(chat_id: int, file: UploadFile = File(...), user_id: int = Depends(get_session)):
    try:
        if not file: raise HTTPException(status_code=400, detail="Archivo vacío")
        content_type = file.content_type or ""
        filename = file.filename.lower() if file.filename else ""
        ext = filename.split('.')[-1] if '.' in filename else ''
        tipo = None

        if content_type.startswith('image/'): tipo = 'imagen'
        elif content_type.startswith('video/'): tipo = 'video'
        if not tipo:
            if ext in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'bmp']: tipo = 'imagen'
            elif ext in ['mp4', 'mov', 'avi', 'mkv', 'webm']: tipo = 'video'
        if not tipo: raise HTTPException(status_code=400, detail=f"Formato no soportado")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, usuario1_id, usuario2_id FROM chats WHERE id = %s AND (usuario1_id = %s OR usuario2_id = %s)", (chat_id, user_id, user_id))
            chat = cur.fetchone()
            if not chat: raise HTTPException(status_code=404, detail="Chat no encontrado")

            receptor_id = chat[2] if chat[1] == user_id else chat[1]
            verificar_bloqueo(cur, user_id, receptor_id)
            cur.execute("SELECT (SELECT COALESCE(du.nombre_empresa, u.nombre) FROM usuarios u LEFT JOIN datos_usuario du ON u.id = du.user_id WHERE u.id = %s), (SELECT fcm_token FROM usuarios WHERE id = %s)", (user_id, receptor_id))
            row = cur.fetchone()
            emisor_nombre = row[0] if row and row[0] else "Usuario"
            fcm_token = row[1] if row and row[1] else None

            chat_folder = os.path.join(MEDIA_DIR, str(chat_id))
            os.makedirs(chat_folder, exist_ok=True)
            unique_filename = f"{uuid.uuid4().hex}_{sanitize_filename(filename)}"
            file_path = os.path.join(chat_folder, unique_filename)

            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            cur.execute("""
                INSERT INTO mensajes_chat (chat_id, emisor_id, receptor_id, tipo, media_content, fecha_envio)
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING id, fecha_envio
            """, (chat_id, user_id, receptor_id, tipo, file_path))
            mensaje = cur.fetchone()
            cur.execute("UPDATE chats SET ultimo_mensaje_id = %s WHERE id = %s", (mensaje[0], chat_id))
            conn.commit()

            message_data = {
                "id": mensaje[0], "chat_id": chat_id, "emisor_id": user_id, "receptor_id": receptor_id,
                "contenido": "", "tipo": tipo, "media_url": f"/chats/media/{mensaje[0]}",
                "fecha_envio": mensaje[1].strftime("%Y-%m-%d %H:%M:%S"), "leido": False, "es_mio": True
            }
            if receptor_id in websocket_connections:
                try: await websocket_connections[receptor_id].send_text(json.dumps(message_data))
                except: del websocket_connections[receptor_id]

            if fcm_token:
                cuerpo = "📷 Te ha enviado una foto." if tipo == 'imagen' else "🎥 Te ha enviado un video."
                try:
                    push_msg = messaging.Message(notification=messaging.Notification(title=f"Nuevo mensaje de {emisor_nombre}", body=cuerpo), apns=messaging.APNSConfig(payload=messaging.APNSPayload(aps=messaging.Aps(sound="default"))), data={"tipo": "chat", "chat_id": str(chat_id)}, token=fcm_token)
                    messaging.send(push_msg)
                except Exception as e: logging.error(f"Error Push: {e}")
            return message_data
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()
    except HTTPException as he: raise he

@router.post("/{chat_id}/voz")
async def send_voice_note(chat_id: int, file: UploadFile = File(...), user_id: int = Depends(get_session)):
    try:
        if not file: raise HTTPException(status_code=400, detail="Archivo vacío")
        content_type = file.content_type or ""
        filename = file.filename.lower() if file.filename else "audio.m4a"
        ext = filename.split('.')[-1] if '.' in filename else ''
        
        es_audio = False
        if content_type.startswith('audio/'): es_audio = True
        elif ext in ['m4a', 'mp3', 'wav', 'aac', 'webm', 'ogg', 'opus']: es_audio = True
        elif content_type == 'video/mp4' and ext == 'm4a': es_audio = True 

        if not es_audio: raise HTTPException(status_code=400, detail=f"No es un audio válido")
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, usuario1_id, usuario2_id FROM chats WHERE id = %s AND (usuario1_id = %s OR usuario2_id = %s)", (chat_id, user_id, user_id))
            chat = cur.fetchone()
            if not chat: raise HTTPException(status_code=404, detail="Chat no encontrado")

            receptor_id = chat[2] if chat[1] == user_id else chat[1]
            verificar_bloqueo(cur, user_id, receptor_id)
            cur.execute("SELECT (SELECT COALESCE(du.nombre_empresa, u.nombre) FROM usuarios u LEFT JOIN datos_usuario du ON u.id = du.user_id WHERE u.id = %s), (SELECT fcm_token FROM usuarios WHERE id = %s)", (user_id, receptor_id))
            row = cur.fetchone()
            emisor_nombre = row[0] if row and row[0] else "Usuario"
            fcm_token = row[1] if row and row[1] else None

            chat_folder = os.path.join(MEDIA_DIR, str(chat_id))
            os.makedirs(chat_folder, exist_ok=True)
            unique_filename = f"{uuid.uuid4().hex}_{sanitize_filename(filename)}"
            file_path = os.path.join(chat_folder, unique_filename)

            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            cur.execute("""
                INSERT INTO mensajes_chat (chat_id, emisor_id, receptor_id, tipo, media_content, fecha_envio)
                VALUES (%s, %s, %s, 'voz', %s, CURRENT_TIMESTAMP)
                RETURNING id, fecha_envio
            """, (chat_id, user_id, receptor_id, file_path))
            mensaje = cur.fetchone()
            cur.execute("UPDATE chats SET ultimo_mensaje_id = %s WHERE id = %s", (mensaje[0], chat_id))
            conn.commit()

            message_data = {
                "id": mensaje[0], "chat_id": chat_id, "emisor_id": user_id, "receptor_id": receptor_id,
                "contenido": "", "tipo": "voz", "media_url": f"/chats/media/{mensaje[0]}",
                "fecha_envio": mensaje[1].strftime("%Y-%m-%d %H:%M:%S"), "leido": False, "es_mio": True
            }
            if receptor_id in websocket_connections:
                try: await websocket_connections[receptor_id].send_text(json.dumps(message_data))
                except: del websocket_connections[receptor_id]

            if fcm_token:
                try:
                    push_msg = messaging.Message(notification=messaging.Notification(title=f"Nuevo mensaje de {emisor_nombre}", body="🎙️ Te ha enviado una nota de voz."), apns=messaging.APNSConfig(payload=messaging.APNSPayload(aps=messaging.Aps(sound="default"))), data={"tipo": "chat", "chat_id": str(chat_id)}, token=fcm_token)
                    messaging.send(push_msg)
                except Exception as e: logging.error(f"Error Push: {e}")

            return message_data
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()
    except HTTPException as he: raise he

@router.post("/{chat_id}/document")
async def send_document(chat_id: int, file: UploadFile = File(...), contenido: str = Form(None), user_id: int = Depends(get_session)):
    try:
        if not file: raise HTTPException(status_code=400, detail="Archivo vacío")
        content_type = file.content_type or ""
        filename = file.filename.lower() if file.filename else "documento.pdf"
        ext = filename.split('.')[-1] if '.' in filename else ''
        allowed_extensions = ['pdf', 'doc', 'docx', 'txt', 'xls', 'xlsx', 'ppt', 'pptx']
        es_doc = False
        if content_type in ['application/pdf', 'application/msword', 'text/plain']: es_doc = True
        elif ext in allowed_extensions: es_doc = True
        elif 'application/' in content_type: es_doc = True
        if not es_doc: raise HTTPException(status_code=400, detail="Documento no permitido")

        doc_name = contenido if contenido else file.filename
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, usuario1_id, usuario2_id FROM chats WHERE id = %s AND (usuario1_id = %s OR usuario2_id = %s)", (chat_id, user_id, user_id))
            chat = cur.fetchone()
            if not chat: raise HTTPException(status_code=404, detail="Chat no encontrado")

            receptor_id = chat[2] if chat[1] == user_id else chat[1]
            verificar_bloqueo(cur, user_id, receptor_id)
            cur.execute("SELECT (SELECT COALESCE(du.nombre_empresa, u.nombre) FROM usuarios u LEFT JOIN datos_usuario du ON u.id = du.user_id WHERE u.id = %s), (SELECT fcm_token FROM usuarios WHERE id = %s)", (user_id, receptor_id))
            row = cur.fetchone()
            emisor_nombre = row[0] if row and row[0] else "Usuario"
            fcm_token = row[1] if row and row[1] else None

            chat_folder = os.path.join(MEDIA_DIR, str(chat_id))
            os.makedirs(chat_folder, exist_ok=True)
            unique_filename = f"{uuid.uuid4().hex}_{sanitize_filename(filename)}"
            file_path = os.path.join(chat_folder, unique_filename)

            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            cur.execute("""
                INSERT INTO mensajes_chat (chat_id, emisor_id, receptor_id, contenido, tipo, media_content, fecha_envio)
                VALUES (%s, %s, %s, %s, 'document', %s, CURRENT_TIMESTAMP)
                RETURNING id, fecha_envio
            """, (chat_id, user_id, receptor_id, doc_name, file_path))
            mensaje = cur.fetchone()
            cur.execute("UPDATE chats SET ultimo_mensaje_id = %s WHERE id = %s", (mensaje[0], chat_id))
            conn.commit()

            message_data = {
                "id": mensaje[0], "chat_id": chat_id, "emisor_id": user_id, "receptor_id": receptor_id,
                "contenido": doc_name, "tipo": "document", "media_url": f"/chats/media/{mensaje[0]}",
                "fecha_envio": mensaje[1].strftime("%Y-%m-%d %H:%M:%S"), "leido": False, "es_mio": True
            }
            if receptor_id in websocket_connections:
                try: await websocket_connections[receptor_id].send_text(json.dumps(message_data))
                except: del websocket_connections[receptor_id]

            if fcm_token:
                try:
                    push_msg = messaging.Message(notification=messaging.Notification(title=f"Nuevo mensaje de {emisor_nombre}", body=f"📄 Te ha enviado un documento: {doc_name}"), apns=messaging.APNSConfig(payload=messaging.APNSPayload(aps=messaging.Aps(sound="default"))), data={"tipo": "chat", "chat_id": str(chat_id)}, token=fcm_token)
                    messaging.send(push_msg)
                except Exception as e: logging.error(f"Error Push: {e}")

            return message_data
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()
    except HTTPException as he: raise he

@router.get("/buscar")
async def search_chats(query: str, user_id: int = Depends(get_session), limit: int = 10, offset: int = 0):
    try:
        query = query.strip().lower()
        if not query: raise HTTPException(status_code=400, detail="Búsqueda vacía")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT c.id, 
                   CASE WHEN c.usuario1_id = %s THEN c.usuario2_id ELSE c.usuario1_id END AS otro_usuario_id,
                   COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                   CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END AS tipo_usuario,
                   m.contenido AS ultimo_mensaje, m.fecha_envio, m.tipo AS tipo_ultimo_mensaje,
                   SUM(CASE WHEN m.leido = FALSE AND m.receptor_id = %s THEN 1 ELSE 0 END) AS unread_count,
                   (du.foto IS NOT NULL OR du.ruta_foto IS NOT NULL) AS has_foto
            FROM chats c
            JOIN usuarios u ON (CASE WHEN c.usuario1_id = %s THEN c.usuario2_id ELSE c.usuario1_id END) = u.id
            LEFT JOIN datos_usuario du ON u.id = du.user_id
            LEFT JOIN mensajes_chat m ON c.ultimo_mensaje_id = m.id
            WHERE (c.usuario1_id = %s OR c.usuario2_id = %s)
              AND (LOWER(COALESCE(du.nombre_empresa, u.nombre)) LIKE %s)
            GROUP BY c.id, c.usuario1_id, c.usuario2_id, u.nombre, du.nombre_empresa, du.categoria, m.contenido, m.fecha_envio, m.tipo, du.foto, du.ruta_foto
            ORDER BY m.fecha_envio DESC NULLS LAST
            LIMIT %s OFFSET %s
        """, (user_id, user_id, user_id, user_id, user_id, f"%{query}%", limit, offset))
        chats = cur.fetchall()
        cur.close()
        conn.close()

        chats_list = [
            {
                "chat_id": row[0], "otro_usuario_id": int(row[1]), "display_name": row[2], "tipo_usuario": row[3],
                "foto_perfil_url": f"/foto_perfil/{row[1]}" if row[8] else "",
                "ultimo_mensaje": row[4] if row[6] in ['texto', 'document'] else (f"[{row[6].upper()}]" if row[6] else ""), 
                "fecha_envio": row[5].strftime("%Y-%m-%d %H:%M:%S") if row[5] else "",
                "tipo_ultimo_mensaje": row[6] if row[6] else "texto", "unread_count": int(row[7])
            } for row in chats
        ]
        return chats_list
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/iniciar/{otro_usuario_id}")
async def start_chat(otro_usuario_id: int, user_id: int = Depends(get_session)):
    try:
        if user_id == otro_usuario_id: raise HTTPException(status_code=400, detail="No auto-chat")

        conn = get_db_connection()
        cur = conn.cursor()
        
        verificar_bloqueo(cur, user_id, otro_usuario_id)

        cur.execute("SELECT id FROM usuarios WHERE id = %s", (otro_usuario_id,))
        if not cur.fetchone(): raise HTTPException(status_code=404, detail="Usuario no encontrado")

        cur.execute("""
            SELECT id FROM chats 
            WHERE (usuario1_id = %s AND usuario2_id = %s) OR (usuario1_id = %s AND usuario2_id = %s)
        """, (user_id, otro_usuario_id, otro_usuario_id, user_id))
        chat = cur.fetchone()
        if chat: 
            cur.close()
            conn.close()
            return {"chat_id": chat[0]}

        cur.execute("""
            INSERT INTO chats (usuario1_id, usuario2_id, creado_en)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            RETURNING id
        """, (user_id, otro_usuario_id))
        chat_id = cur.fetchone()[0]
        conn.commit()

        message_data = {"chat_id": chat_id, "otro_usuario_id": user_id, "tipo": "nuevo_chat", "fecha_creacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        if otro_usuario_id in websocket_connections:
            try: await websocket_connections[otro_usuario_id].send_text(json.dumps(message_data))
            except: del websocket_connections[otro_usuario_id]

        cur.close()
        conn.close()
        return {"chat_id": chat_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{chat_id}")
async def delete_chat(chat_id: int, user_id: int = Depends(get_session)):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, usuario1_id, usuario2_id FROM chats WHERE id = %s AND (usuario1_id = %s OR usuario2_id = %s)", (chat_id, user_id, user_id))
        chat = cur.fetchone()
        if not chat: raise HTTPException(status_code=404, detail="Chat no encontrado")

        receptor_id = chat[2] if chat[1] == user_id else chat[1]
        
        chat_folder = os.path.join(MEDIA_DIR, str(chat_id))
        if os.path.exists(chat_folder):
            try: shutil.rmtree(chat_folder)
            except Exception as e: logging.error(f"Error borrando carpeta: {e}")

        cur.execute("DELETE FROM mensajes_chat WHERE chat_id = %s", (chat_id,))
        cur.execute("DELETE FROM chats WHERE id = %s", (chat_id,))
        conn.commit()

        message_data = {"chat_id": chat_id, "otro_usuario_id": user_id, "tipo": "chat_deleted", "fecha_eliminacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        if receptor_id in websocket_connections:
            try: await websocket_connections[receptor_id].send_text(json.dumps(message_data))
            except: del websocket_connections[receptor_id]

        cur.close()
        conn.close()
        return {"message": "Chat eliminado"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    await websocket.accept()
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM usuarios WHERE id = %s", (user_id,))
        if not cur.fetchone():
            await websocket.close(code=1008, reason="Usuario no encontrado")
            return
        cur.close()
        conn.close()

        websocket_connections[user_id] = websocket
        try:
            while True:
                await websocket.receive_text()
                await websocket.send_text(json.dumps({"type": "ping"}))
        except WebSocketDisconnect:
            if user_id in websocket_connections: del websocket_connections[user_id]
        except Exception:
            if user_id in websocket_connections: del websocket_connections[user_id]
    except Exception as e:
        logging.error(f"Error WS: {e}")
        await websocket.close(code=1008)

@router.get("/user/{user_id}/foto_perfil")
async def get_user_profile_picture(user_id: int):
    return RedirectResponse(url=f"/foto_perfil/{user_id}")

@router.get("/unread_count")
async def get_unread_count(user_id: int = Depends(get_session)):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM mensajes_chat m JOIN chats c ON m.chat_id = c.id
            WHERE m.receptor_id = %s AND m.leido = FALSE AND (c.usuario1_id = %s OR c.usuario2_id = %s)
        """, (user_id, user_id, user_id))
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return {"unread_count": count}
    except Exception: raise HTTPException(status_code=500)