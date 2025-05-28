from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import psycopg2
from datetime import datetime
import os
import logging
import io

router = APIRouter()

# Configurar Jinja2
templates = Jinja2Templates(directory=".")

# Configurar logging
logging.basicConfig(level=logging.DEBUG)

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

# Directorio para uploads
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Ruta para servir la foto de perfil
@router.get("/foto_perfil/{user_id}")
async def get_foto_perfil(user_id: int):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT foto FROM datos_usuario WHERE user_id = %s", (user_id,))
        result = cur.fetchone()
        cur.close()
        if not result or not result[0]:
            # Devolver imagen por defecto si no hay foto
            default_path = "static/default_profile.png"
            if not os.path.exists(default_path):
                raise HTTPException(status_code=404, detail="Foto de perfil por defecto no encontrada")
            with open(default_path, "rb") as f:
                return StreamingResponse(io.BytesIO(f.read()), media_type="image/png")
        foto_data = result[0]
        return StreamingResponse(io.BytesIO(foto_data), media_type="image/jpeg")
    except Exception as e:
        logging.error(f"Error al obtener foto de perfil: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener foto de perfil")
    finally:
        if conn:
            conn.close()

# Ruta para renderizar inicio.html
@router.get("/inicio", response_class=HTMLResponse)
async def inicio(request: Request, limit: int = 10, offset: int = 0):
    try:
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning("Sesión no encontrada o sin user_id, redirigiendo a /login")
            return RedirectResponse(url="/login", status_code=302)

        try:
            user_id = int(request.session['user']['id'])  # Asegurar que sea entero
            logging.debug(f"User ID enviado a inicio.html: {user_id}, tipo: {type(user_id)}")
        except (ValueError, TypeError) as e:
            logging.error(f"Error: user_id no es un entero válido. Valor: {request.session['user']['id']}, Error: {e}")
            request.session.clear()  # Limpiar sesión inválida
            return RedirectResponse(url="/login", status_code=302)

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT p.id, p.user_id, p.contenido, p.imagen_url, p.video_url, 
                       p.etiquetas, p.fecha_creacion, du.nombre_empresa
                FROM publicaciones p
                JOIN datos_usuario du ON p.user_id = du.user_id
                ORDER BY p.fecha_creacion DESC
                LIMIT %s OFFSET %s
            """, (limit, offset))
            publicaciones = cur.fetchall()
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
                "user_id": int(row[1]),  # Asegurar que sea entero
                "contenido": row[2] if row[2] else "",
                "imagen_url": row[3] if row[3] else "",
                "video_url": row[4] if row[4] else "",
                "etiquetas": row[5] if row[5] else [],
                "fecha_creacion": row[6].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}",
                "nombre_empresa": row[7] if row[7] else "Sin empresa"
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

# Ruta para guardar publicación
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
            return RedirectResponse(url="/login", status_code=302)

        user_id = int(request.session['user']['id'])  # Asegurar que sea entero
        logging.debug(f"Publicando con user_id: {user_id}")

        if not contenido and not imagen and not video:
            raise HTTPException(status_code=400, detail="Debe incluir al menos contenido, una imagen o un video")

        etiquetas_lista = [e.strip() for e in etiquetas.split(",") if e.strip()] if etiquetas else []

        imagen_url = None
        video_url = None
        if imagen and imagen.size > 0:
            imagen_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{imagen.filename}"
            imagen_path = os.path.join(UPLOAD_DIR, imagen_filename)
            with open(imagen_path, "wb") as f:
                f.write(await imagen.read())
            imagen_url = f"/uploads/{imagen_filename}"
            logging.debug(f"Imagen guardada: {imagen_url}")

        if video and video.size > 0:
            video_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{video.filename}"
            video_path = os.path.join(UPLOAD_DIR, video_filename)
            with open(video_path, "wb") as f:
                f.write(await video.read())
            video_url = f"/uploads/{video_filename}"
            logging.debug(f"Video guardado: {video_url}")

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute("SELECT id FROM usuarios WHERE id = %s", (user_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=400, detail="Usuario no encontrado")

            query = """
                INSERT INTO publicaciones (user_id, contenido, imagen_url, video_url, etiquetas)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """
            cur.execute(query, (user_id, contenido, imagen_url, video_url, etiquetas_lista))
            post_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            logging.debug(f"Publicación guardada con ID: {post_id}")
        except Exception as e:
            logging.error(f"Error al guardar publicación: {e}")
            raise HTTPException(status_code=500, detail=f"Error al guardar publicación: {str(e)}")
        finally:
            if conn:
                conn.close()

        return RedirectResponse(url="/inicio", status_code=302)
    except Exception as e:
        logging.error(f"Error en /publicar: {e}")
        raise HTTPException(status_code=500, detail=f"Error al guardar publicación: {str(e)}")

# Ruta para feed
@router.get("/feed")
async def feed(limit: int = 10, offset: int = 0):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.user_id, p.contenido, p.imagen_url, p.video_url,
                   p.etiquetas, p.fecha_creacion, du.nombre_empresa
            FROM publicaciones p
            JOIN datos_usuario du ON p.user_id = du.user_id
            ORDER BY p.fecha_creacion DESC
            LIMIT %s OFFSET %s
        """, (limit, offset))
        publicaciones = cur.fetchall()
        cur.close()

        publicaciones_list = [
            {
                "id": row[0],
                "user_id": int(row[1]),  # Asegurar que sea entero
                "contenido": row[2] if row[2] else "",
                "imagen_url": row[3] if row[3] else "",
                "video_url": row[4] if row[4] else "",
                "etiquetas": row[5] if row[5] else [],
                "fecha_creacion": row[6].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}",
                "nombre_empresa": row[7] if row[7] else "Sin empresa"
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

# Ruta para buscar publicaciones
@router.get("/search")
async def search_publicaciones(query: str, limit: int = 10, offset: int = 0):
    query = query.strip().lower()
    if not query:
        logging.warning("Query vacío en /search")
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.user_id, p.contenido, p.imagen_url, p.video_url,
                   p.etiquetas, p.fecha_creacion, du.nombre_empresa
            FROM publicaciones p
            JOIN datos_usuario du ON p.user_id = du.user_id
            WHERE LOWER(du.nombre_empresa) LIKE %s
               OR EXISTS (
                   SELECT 1
                   FROM unnest(p.etiquetas) AS etiqueta
                   WHERE LOWER(etiqueta) LIKE %s
               )
            ORDER BY p.fecha_creacion DESC
            LIMIT %s OFFSET %s
        """, (f"%{query}%", f"%{query}%", limit, offset))
        publicaciones = cur.fetchall()
        cur.close()

        publicaciones_list = [
            {
                "id": row[0],
                "user_id": int(row[1]),  # Asegurar que sea entero
                "contenido": row[2] if row[2] else "",
                "imagen_url": row[3] if row[3] else "",
                "video_url": row[4] if row[4] else "",
                "etiquetas": row[5] if row[5] else [],
                "fecha_creacion": row[6].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}",
                "nombre_empresa": row[7] if row[7] else "Sin empresa"
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

# Ruta para feed del perfil
@router.get("/perfil/feed")
async def perfil_feed(request: Request, limit: int = 10, offset: int = 0):
    try:
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning("Sesión no encontrada en /perfil/feed, retornando error")
            raise HTTPException(status_code=401, detail="No autorizado")

        try:
            user_id = int(request.session['user']['id'])  # Asegurar que sea entero
            logging.debug(f"Obteniendo feed para user_id: {user_id}, tipo: {type(user_id)}")
        except (ValueError, TypeError) as e:
            logging.error(f"Error: user_id no es un entero válido. Valor: {request.session['user']['id']}, Error: {e}")
            raise HTTPException(status_code=400, detail="ID de usuario inválido")

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT p.id, p.user_id, p.contenido, p.imagen_url, p.video_url, 
                       p.etiquetas, p.fecha_creacion, du.nombre_empresa
                FROM publicaciones p
                JOIN datos_usuario du ON p.user_id = du.user_id
                WHERE p.user_id = %s
                ORDER BY p.fecha_creacion DESC
                LIMIT %s OFFSET %s
            """, (user_id, limit, offset))
            publicaciones = cur.fetchall()
            cur.close()
        except Exception as e:
            logging.error(f"Error al obtener publicaciones del perfil: {e}")
            raise HTTPException(status_code=500, detail="Error al obtener publicaciones")
        finally:
            if conn:
                conn.close()

        publicaciones_list = [
            {
                "id": row[0],
                "user_id": int(row[1]),  # Asegurar que sea entero
                "contenido": row[2] if row[2] else "",
                "imagen_url": row[3] if row[3] else "",
                "video_url": row[4] if row[4] else "",
                "etiquetas": row[5] if row[5] else [],
                "fecha_creacion": row[6].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": f"/foto_perfil/{row[1]}",
                "nombre_empresa": row[7] if row[7] else "Sin empresa"
            }
            for row in publicaciones
        ]
        logging.debug(f"Feed del perfil retornado para user_id {user_id}: {publicaciones_list}")
        return publicaciones_list
    except Exception as e:
        logging.error(f"Error en /perfil/feed: {e}")
        raise HTTPException(status_code=500, detail=f"Error al cargar feed del perfil: {str(e)}")

# Ruta para current_user
@router.get("/current_user")
async def get_current_user(request: Request):
    try:
        if 'user' not in request.session or 'id' not in request.session['user']:
            logging.warning("Sesión no encontrada en /current_user, retornando null")
            return {"user_id": None, "tipo": None}  # Añadido "tipo": None para compatibilidad con el frontend
        
        try:
            user_id = int(request.session['user']['id'])  # Asegurar que sea entero
            user_tipo = request.session['user'].get('tipo', 'empresa')  # Obtener el tipo de usuario
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
    logging.debug("Sesión cerrada")
    return RedirectResponse(url="/login", status_code=302)

# Ruta para borrar publicaciones
@router.delete("/borrar_publicacion/{post_id}")
async def borrar_publicacion(post_id: int, request: Request):
    try:
        if 'user' not in request.session or 'id' not in request.session['user']:
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
                raise HTTPException(status_code=404, detail="Publicación no encontrada")
            if result[0] != user_id:
                raise HTTPException(status_code=403, detail="No tienes permiso para borrar esta publicación")

            cur.execute("SELECT imagen_url, video_url FROM publicaciones WHERE id = %s", (post_id,))
            file_urls = cur.fetchone()
            imagen_url, video_url = file_urls

            cur.execute("DELETE FROM publicaciones WHERE id = %s", (post_id,))
            conn.commit()

            if imagen_url:
                file_path = os.path.join(UPLOAD_DIR, imagen_url.split('/')[-1])
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logging.debug(f"Archivo eliminado: {file_path}")

            if video_url:
                file_path = os.path.join(UPLOAD_DIR, video_url.split('/')[-1])
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logging.debug(f"Archivo eliminado: {file_path}")

            cur.close()
            logging.debug(f"Publicación {post_id} eliminada correctamente")
            return {"message": "Publicación eliminada correctamente"}
        except Exception as e:
            logging.error(f"Error al borrar publicación: {e}")
            raise HTTPException(status_code=500, detail=f"Error al borrar publicación: {str(e)}")
        finally:
            if conn:
                conn.close()
    except Exception as e:
        logging.error(f"Error en /borrar_publicacion: {e}")
        raise HTTPException(status_code=500, detail=f"Error al borrar publicación: {str(e)}")