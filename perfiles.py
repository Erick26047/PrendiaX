from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import psycopg2
import logging

router = APIRouter()
templates = Jinja2Templates(directory="templates")

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler('app.log')]
)

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

def get_user_id_hybrid(request: Request):
    auth_header = request.headers.get("Authorization")
    if auth_header and "jwt_app_" in auth_header:
        try:
            token_part = auth_header.split("jwt_app_")[1]
            if token_part.isdigit():
                return int(token_part)
        except:
            pass
    if 'user' in request.session and 'id' in request.session['user']:
        return int(request.session['user']['id'])
    return None


# ==========================================
#  API PARA LA APP MÓVIL (JSON) – CORREGIDA
# ==========================================
@router.get("/api/perfil/{user_id}")
async def get_perfil_api(request: Request, user_id: int):
    conn = None
    try:
        viewer_id = get_user_id_hybrid(request)
        logging.debug(f"[API] Visitando perfil {user_id}. Visitante: {viewer_id}")

        conn = get_db_connection()
        cur = conn.cursor()

        # TRAEMOS SOLO LO QUE NECESITA EL PERFIL DE EXPLORADOR
        cur.execute("""
            SELECT 
                u.id,
                u.nombre,
                u.email
            FROM usuarios u
            WHERE u.id = %s
        """, (user_id,))

        user_row = cur.fetchone()
        
        if not user_row:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

        # DEVOLVEMOS SOLO LO QUE USA SPECIFICPROFILESCREEN
        user_data = {
            "id": user_row[0],
            "nombre": user_row[1].strip() if user_row[1] else "Usuario",
            "email": user_row[2] or "",
            "foto_perfil": "",                    # exploradores no tienen foto de perfil
            "tipo_usuario": "explorador"
        }

        # PUBLICACIONES (mantenemos tu lógica original)
        cur.execute("""
            SELECT p.id, p.user_id, p.contenido, p.imagen, p.video, 
                   p.etiquetas, p.fecha_creacion,
                   u.nombre AS display_name
            FROM publicaciones p
            JOIN usuarios u ON p.user_id = u.id
            WHERE p.user_id = %s
            ORDER BY p.fecha_creacion DESC
        """, (user_id,))

        publicaciones = cur.fetchall()
        
        publicaciones_list = [
            {
                "id": row[0],
                "user_id": int(row[1]),
                "contenido": row[2] if row[2] else "",
                "imagen_url": f"/media/{row[0]}" if row[3] else "",
                "video_url": f"/media/{row[0]}" if row[4] else "",
                "etiquetas": row[5] if row[5] else [],
                "fecha_creacion": row[6].strftime("%Y-%m-%d %H:%M:%S"),
                "nombre_empresa": row[7]  # este campo lo usas en las publicaciones
            }
            for row in publicaciones
        ]

        return {
            "user": user_data,
            "posts": publicaciones_list,
            "is_owner": viewer_id == user_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"[API ERROR] Perfil explorador {user_id}: {e}")
        # Siempre devolvemos 404 para que Flutter entre al fallback y saque nombre del auth
        raise HTTPException(status_code=404, detail="Perfil no encontrado")
    finally:
        if conn:
            conn.close()


# ==========================================
#  RUTA WEB (HTML) – también corregida
# ==========================================
@router.get("/perfil/{user_id}", response_class=HTMLResponse)
async def get_perfil(request: Request, user_id: int):
    conn = None
    try:
        current_user_id = get_user_id_hybrid(request)
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT u.id, u.nombre, du.nombre_empresa,
                   CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END,
                   CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN '/foto_perfil/' || u.id ELSE '' END,
                   du.descripcion
            FROM usuarios u
            LEFT JOIN datos_usuario du ON u.id = du.user_id
            WHERE u.id = %s
        """, (user_id,))

        user = cur.fetchone()
        if not user:
            raise HTTPException(status_code=404)

        user_data = {
            "id": user[0],
            "nombre": user[1].strip() if user[1] else "Usuario",
            "nombre_empresa": user[2].strip() if user[2] else None,
            "tipo_usuario": user[3],
            "foto_perfil_url": user[4] if user[4] else "",
            "descripcion": user[5] if user[5] else ""
        }

        # publicaciones web (igual)
        cur.execute("""
            SELECT p.id, p.user_id, p.contenido, p.imagen, p.video, p.etiquetas, p.fecha_creacion,
                   COALESCE(du.nombre_empresa, u.nombre),
                   CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN '/foto_perfil/' || p.user_id ELSE '' END,
                   COUNT(i.user_id), EXISTS(SELECT 1 FROM intereses i WHERE i.publicacion_id = p.id AND i.user_id = %s),
                   p.imagen IS NOT NULL, p.video IS NOT NULL
            FROM publicaciones p
            JOIN usuarios u ON p.user_id = u.id
            LEFT JOIN datos_usuario du ON p.user_id = du.user_id
            LEFT JOIN intereses i ON p.id = i.publicacion_id
            WHERE p.user_id = %s
            GROUP BY p.id, du.nombre_empresa, u.nombre, du.categoria
            ORDER BY p.fecha_creacion DESC
        """, (current_user_id if current_user_id else -1, user_id))

        publicaciones = cur.fetchall()
        user_data["posts"] = [
            {
                "id": r[0], "user_id": r[1], "contenido": r[2] or "",
                "imagen_url": f"/media/{r[0]}" if r[10] else "",
                "video_url": f"/media/{r[0]}" if r[11] else "",
                "etiquetas": r[5] or [], "fecha_creacion": r[6].strftime("%Y-%m-%d %H:%M:%S"),
                "display_name": r[7], "foto_perfil_url": r[8] or "",
                "interesados_count": int(r[9]), "interesado": r[10]
            } for r in publicaciones
        ]

        is_owner = current_user_id == user_id
        template = "perfil.html" if is_owner else "perfil-especifico.html"

        current_user = None
        if current_user_id:
            cur.execute("SELECT u.id, COALESCE(du.nombre_empresa, u.nombre), CASE WHEN du.categoria IS NOT NULL AND du.categoria != '' THEN 'emprendedor' ELSE 'explorador' END FROM usuarios u LEFT JOIN datos_usuario du ON u.id = du.user_id WHERE u.id = %s", (current_user_id,))
            cu = cur.fetchone()
            if cu:
                current_user = {"id": cu[0], "nombre_empresa": cu[1], "tipo_usuario": cu[2]}

        return templates.TemplateResponse(template, {
            "request": request, "user": user_data,
            "is_owner": is_owner, "current_user": current_user
        })

    except Exception as e:
        logging.error(f"Error Web Perfil: {e}")
        raise HTTPException(status_code=500)
    finally:
        if conn:
            conn.close()