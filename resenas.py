from fastapi import APIRouter, Request, HTTPException, Header
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

# Modelo para la solicitud de reseña
class ReviewRequest(BaseModel):
    texto: str
    calificacion: int

# --- FUNCIÓN NUEVA: Validar el Token de la App ---
def get_current_user_id(authorization: str):
    """
    Lee el header 'Authorization: Bearer jwt_app_123' y extrae el ID 123.
    """
    # Debug para confirmar que entra aquí
    print(f"\n[AUTH CHECK] Header recibido: {authorization}")

    if not authorization:
        raise HTTPException(status_code=401, detail="No autorizado: Token faltante")
    
    try:
        # Formato esperado: "Bearer jwt_app_45"
        parts = authorization.split(" ")
        if len(parts) != 2 or parts[0].lower() != "bearer":
             raise HTTPException(status_code=401, detail="Formato de token inválido")
        
        token = parts[1]
        
        # Validar el prefijo que definiste en tu login
        if token.startswith("jwt_app_"):
            user_id_str = token.replace("jwt_app_", "")
            if not user_id_str.isdigit():
                 raise HTTPException(status_code=401, detail="Token corrupto (ID no numérico)")
            
            print(f"[AUTH SUCCESS] Usuario ID identificado: {user_id_str}")
            return int(user_id_str)
        
        # Si usas el login web antiguo
        elif token == "fake_web_token":
             raise HTTPException(status_code=401, detail="Sesión web no soportada en móvil")
        else:
            print(f"[AUTH ERROR] Token desconocido: {token}")
            raise HTTPException(status_code=401, detail="Token desconocido")

    except Exception as e:
        logging.error(f"Error validando token: {e}")
        # Si ya es HTTPException, la dejamos pasar
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=401, detail="Error de autenticación")

# ==========================================
#  RUTAS
# ==========================================

# Ruta para listar reseñas de un perfil
@router.get("/api/perfil/{perfil_id}/resenas")
async def get_resenas(perfil_id: int, limit: int = 10, offset: int = 0, authorization: str = Header(None)):
    
    # 🛑 ESTE PRINT ES LA PRUEBA DE QUE EL CÓDIGO SE ACTUALIZÓ
    print("\n🔥🔥🔥 ¡CÓDIGO NUEVO DE RESEÑAS EJECUTÁNDOSE! 🔥🔥🔥")
    
    # 1. Validar Usuario usando el Token
    user_id = get_current_user_id(authorization)
    
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Verificar si el perfil existe
        cur.execute("SELECT id FROM usuarios WHERE id = %s", (perfil_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Perfil no encontrado")

        # Obtener reseñas con información del autor
        cur.execute("""
            SELECT r.id, r.user_id, r.perfil_id, r.texto, r.calificacion, r.fecha_creacion,
                   COALESCE(du.nombre_empresa, u.nombre) AS display_name,
                   CASE 
                       WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                       ELSE 'explorador'
                   END AS tipo_usuario,
                   du.foto_perfil
            FROM resenas r
            JOIN usuarios u ON r.user_id = u.id
            LEFT JOIN datos_usuario du ON r.user_id = du.user_id
            WHERE r.perfil_id = %s
            ORDER BY r.fecha_creacion DESC
            LIMIT %s OFFSET %s
        """, (perfil_id, limit, offset))
        
        resenas = cur.fetchall()
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
                "tipo_usuario": row[7],
                "foto_perfil": row[8] if row[8] else "" 
            }
            for row in resenas
        ]
        return resenas_list

    except psycopg2.Error as e:
        logging.error(f"Error DB: {e}")
        raise HTTPException(status_code=500, detail="Error de base de datos")
    except Exception as e:
        logging.error(f"Error general: {e}")
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()

# Ruta para crear una reseña
@router.post("/api/perfil/{perfil_id}/resenas")
async def create_review(perfil_id: int, request: ReviewRequest, authorization: str = Header(None)):
    
    # 1. Autenticación con Token
    user_id = get_current_user_id(authorization)
    
    texto = request.texto.strip()
    calificacion = request.calificacion

    # Validaciones
    if user_id == perfil_id:
        raise HTTPException(status_code=400, detail="No puedes dejar una reseña en tu propio perfil")

    if not texto:
        raise HTTPException(status_code=400, detail="El comentario no puede estar vacío")

    if not (1 <= calificacion <= 5):
        raise HTTPException(status_code=400, detail="La calificación debe estar entre 1 y 5")

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Verificar si el perfil existe
        cur.execute("SELECT id FROM usuarios WHERE id = %s", (perfil_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Perfil no encontrado")

        # Insertar la reseña
        cur.execute("""
            INSERT INTO resenas (user_id, perfil_id, texto, calificacion, fecha_creacion)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            RETURNING id, fecha_creacion
        """, (user_id, perfil_id, texto, calificacion))
        
        new_data = cur.fetchone()
        review_id = new_data[0]
        fecha_creacion = new_data[1]
        
        conn.commit()

        # Obtener datos del autor para devolver al frontend inmediatamente
        cur.execute("""
            SELECT COALESCE(du.nombre_empresa, u.nombre),
                   CASE 
                       WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor'
                       ELSE 'explorador'
                   END,
                   du.foto_perfil
            FROM usuarios u
            LEFT JOIN datos_usuario du ON u.id = du.user_id
            WHERE u.id = %s
        """, (user_id,))
        
        autor_data = cur.fetchone()
        cur.close()

        review_dict = {
            "id": review_id,
            "user_id": user_id,
            "perfil_id": perfil_id,
            "texto": texto,
            "calificacion": calificacion,
            "fecha_creacion": fecha_creacion.strftime("%Y-%m-%d %H:%M:%S"),
            "nombre_empresa": autor_data[0] or "Anónimo",
            "tipo_usuario": autor_data[1],
            "foto_perfil": autor_data[2] if autor_data[2] else ""
        }

        return review_dict

    except psycopg2.Error as e:
        if conn: conn.rollback()
        logging.error(f"Error DB: {e}")
        raise HTTPException(status_code=500, detail="Error de base de datos")
    except Exception as e:
        if conn: conn.rollback()
        if isinstance(e, HTTPException):
            raise e
        logging.error(f"Error general: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()