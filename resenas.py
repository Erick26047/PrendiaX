from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
import psycopg2
from datetime import datetime
import logging

router = APIRouter()

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

# Modelo para la solicitud de reseña
class ReviewRequest(BaseModel):
    texto: str
    calificacion: int

# Ruta para listar reseñas de un perfil
@router.get("/api/perfil/{perfil_id}/resenas")
async def get_resenas(perfil_id: int, request: Request, limit: int = 10, offset: int = 0):
    try:
        # Verificar autenticación
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

            # Verificar si el perfil existe
            cur.execute("SELECT id FROM usuarios WHERE id = %s", (perfil_id,))
            if not cur.fetchone():
                logging.warning(f"Perfil no encontrado: {perfil_id}")
                raise HTTPException(status_code=404, detail="Perfil no encontrado")

            # Obtener reseñas con información del autor
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
        except Exception as e:
            logging.error(f"Error inesperado al obtener reseñas para perfil_id {perfil_id}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error al obtener reseñas: {str(e)}")
        finally:
            if conn:
                conn.close()
                logging.debug("Conexión a la base de datos cerrada")

    except Exception as e:
        logging.error(f"Error general en /api/perfil/{perfil_id}/resenas: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al cargar reseñas: {str(e)}")

# Ruta para crear una reseña
@router.post("/api/perfil/{perfil_id}/resenas")
async def create_review(perfil_id: int, request: ReviewRequest, http_request: Request):
    conn = None
    try:
        # Verificar autenticación
        if 'user' not in http_request.session or 'id' not in http_request.session['user']:
            logging.warning("Sesión no encontrada en /api/perfil/{perfil_id}/resenas")
            raise HTTPException(status_code=401, detail="Debes iniciar sesión para dejar una reseña")

        user_id = int(http_request.session['user']['id'])
        texto = request.texto.strip()
        calificacion = request.calificacion

        # Validaciones
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

        # Verificar si el perfil existe
        cur.execute("SELECT id FROM usuarios WHERE id = %s", (perfil_id,))
        if not cur.fetchone():
            logging.warning(f"Perfil no encontrado: {perfil_id}")
            raise HTTPException(status_code=404, detail="Perfil no encontrado")

        # Insertar la reseña
        cur.execute("""
            INSERT INTO resenas (user_id, perfil_id, texto, calificacion, fecha_creacion)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            RETURNING id, user_id, perfil_id, texto, calificacion, fecha_creacion
        """, (user_id, perfil_id, texto, calificacion))
        review = cur.fetchone()
        conn.commit()

        # Obtener el nombre_empresa del autor
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


