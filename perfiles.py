from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import psycopg2
import logging
from datetime import datetime

router = APIRouter()

# Configurar Jinja2
templates = Jinja2Templates(directory="templates")

# Configurar logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Imprimir logs en consola
        logging.FileHandler('app.log')  # Guardar logs en un archivo
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

# Ruta para mostrar el perfil de un usuario
@router.get("/perfil/{user_id}", response_class=HTMLResponse)
async def get_perfil(request: Request, user_id: int):
    conn = None
    try:
        # Obtener user_id de la sesión (si existe)
        current_user_id = None
        if 'user' in request.session and 'id' in request.session['user']:
            try:
                current_user_id = int(request.session['user']['id'])
                logging.debug(f"User_id obtenido de la sesión: {current_user_id}")
            except (ValueError, TypeError) as e:
                logging.error(f"Error: user_id no es un entero válido. Valor: {request.session['user']['id']}, Error: {e}")
                current_user_id = None

        # Conectar a la base de datos
        conn = get_db_connection()
        cur = conn.cursor()

        # Obtener datos del usuario
        cur.execute("""
            SELECT u.id, COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                   CASE 
                       WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                       ELSE 'explorador'
                   END AS tipo_usuario,
                   CASE 
                       WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN %s || u.id
                       ELSE ''
                   END AS foto_perfil_url,
                   du.descripcion
            FROM usuarios u
            LEFT JOIN datos_usuario du ON u.id = du.user_id
            WHERE u.id = %s
        """, ("/foto_perfil/", user_id))
        user = cur.fetchone()
        if not user:
            logging.warning(f"Usuario no encontrado: {user_id}")
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

        # Crear diccionario con datos del usuario
        user_data = {
            "id": user[0],
            "nombre_empresa": user[1],
            "tipo_usuario": user[2],
            "foto_perfil_url": user[3] if user[3] else "",
            "descripcion": user[4] if user[4] else ""
        }

        # Obtener publicaciones del usuario
        cur.execute("""
            SELECT p.id, p.user_id, p.contenido, p.imagen_url, p.video_url, 
                   p.etiquetas, p.fecha_creacion, 
                   COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                   CASE 
                       WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN %s || p.user_id
                       ELSE ''
                   END AS foto_perfil_url,
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
            GROUP BY p.id, p.user_id, p.contenido, p.imagen_url, p.video_url, p.etiquetas, 
                     p.fecha_creacion, du.nombre_empresa, u.nombre, du.categoria
            ORDER BY p.fecha_creacion DESC
        """, ("/foto_perfil/", current_user_id if current_user_id else -1, user_id))
        publicaciones = cur.fetchall()

        publicaciones_list = [
            {
                "id": row[0],
                "user_id": int(row[1]),
                "contenido": row[2] if row[2] else "",
                "imagen_url": row[3] if row[3] else "",
                "video_url": row[4] if row[4] else "",
                "etiquetas": row[5] if row[5] else [],
                "fecha_creacion": row[6].strftime("%Y-%m-%d %H:%M:%S"),
                "foto_perfil_url": row[8] if row[8] else "",
                "nombre_empresa": row[7],
                "interesados_count": int(row[9]),
                "interesado": row[10]
            }
            for row in publicaciones
        ]
        user_data["posts"] = publicaciones_list

        # Determinar si el usuario actual es el propietario
        is_owner = current_user_id and current_user_id == user_id
        logging.debug(f"Es propietario: {is_owner}, user_id: {user_id}, current_user_id: {current_user_id}")

        # Seleccionar la plantilla adecuada
        template = "perfil.html" if is_owner else "perfil-especifico.html"

        # Obtener datos del usuario actual (si está autenticado)
        current_user = None
        if current_user_id:
            cur.execute("""
                SELECT u.id, COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                       CASE 
                           WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                           ELSE 'explorador'
                       END AS tipo_usuario
                FROM usuarios u
                LEFT JOIN datos_usuario du ON u.id = du.user_id
                WHERE u.id = %s
            """, (current_user_id,))
            current_user_result = cur.fetchone()
            if current_user_result:
                current_user = {
                    "id": current_user_result[0],
                    "nombre_empresa": current_user_result[1],
                    "tipo_usuario": current_user_result[2]
                }

        cur.close()
        logging.debug(f"Renderizando {template} para user_id: {user_id}")

        # Renderizar la plantilla con los datos
        return templates.TemplateResponse(
            template,
            {
                "request": request,
                "user": user_data,
                "is_owner": is_owner,
                "current_user": current_user
            }
        )
    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Error al obtener perfil para user_id {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al obtener perfil: {str(e)}")
    finally:
        if conn:
            conn.close()
            logging.debug("Conexión a la base de datos cerrada")