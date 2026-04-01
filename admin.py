from fastapi import APIRouter, HTTPException
import psycopg2
from datetime import date

router = APIRouter(
    prefix="/api/admin",
    tags=["Admin Dashboard"]
)

# 🔥 Usamos exactamente la misma conexión cruda que en tu chats.py
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
        print(f"Error al conectar a la base de datos en admin: {e}")
        raise HTTPException(status_code=500, detail="Error de conexión a la base de datos")

# 1. RUTA PARA LAS MÉTRICAS EN VIVO (Dashboard)
@router.get("/metricas")
def obtener_metricas():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Total de usuarios registrados
        cur.execute("SELECT COUNT(id) FROM usuarios")
        total_usuarios = cur.fetchone()[0]
        
        # Nuevos usuarios hoy
        cur.execute("SELECT COUNT(id) FROM usuarios WHERE DATE(created_at) = CURRENT_DATE")
        nuevos_hoy = cur.fetchone()[0]
        
        # Reportes pendientes publicaciones
        cur.execute("SELECT COUNT(id) FROM reportes_publicaciones WHERE estatus = 'pendiente'")
        reportes_pubs = cur.fetchone()[0]
        
        # Reportes pendientes usuarios
        cur.execute("SELECT COUNT(id) FROM reportes_usuarios WHERE estatus = 'pendiente'")
        reportes_users = cur.fetchone()[0]
        
        reportes_totales = reportes_pubs + reportes_users
        
        # Total de bloqueos realizados
        cur.execute("SELECT COUNT(id) FROM bloqueos")
        bloqueos_totales = cur.fetchone()[0]
        
        return {
            "total_usuarios": total_usuarios or 0,
            "nuevos_hoy": nuevos_hoy or 0,
            "reportes_pendientes": reportes_totales or 0,
            "bloqueos_activos": bloqueos_totales or 0
        }
    except Exception as e:
        print(f"Error al obtener métricas: {e}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    finally:
        cur.close()
        conn.close()

# 2. RUTA PARA VER PUBLICACIONES REPORTADAS
@router.get("/reportes-pubs")
def obtener_reportes_publicaciones():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT rp.id, rp.publicacion_id, rp.motivo, u.nombre as reportado_por, rp.estatus, rp.fecha_reporte
            FROM reportes_publicaciones rp
            LEFT JOIN usuarios u ON rp.denunciante_id = u.id
            ORDER BY rp.fecha_reporte DESC
            LIMIT 50
        """)
        resultados = cur.fetchall()
        
        return [
            {
                "id": r[0],
                "publicacion_id": r[1],
                "motivo": r[2],
                "reportado_por": r[3] or "Usuario desconocido",
                "estatus": r[4],
                "fecha": str(r[5])
            } for r in resultados
        ]
    except Exception as e:
        print(f"Error en reportes de publicaciones: {e}")
        raise HTTPException(status_code=500, detail="Error al cargar reportes")
    finally:
        cur.close()
        conn.close()

# 3. RUTA PARA VER USUARIOS REPORTADOS
@router.get("/reportes-users")
def obtener_reportes_usuarios():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT ru.id, u.nombre as usuario_reportado, ru.motivo, ru.estatus, ru.fecha_reporte
            FROM reportes_usuarios ru
            LEFT JOIN usuarios u ON ru.usuario_reportado_id = u.id
            ORDER BY ru.fecha_reporte DESC
            LIMIT 50
        """)
        resultados = cur.fetchall()
        
        return [
            {
                "id": r[0],
                "usuario_reportado": r[1] or "Usuario desconocido",
                "motivo": r[2],
                "estatus": r[3],
                "fecha": str(r[4])
            } for r in resultados
        ]
    except Exception as e:
        print(f"Error en reportes de usuarios: {e}")
        raise HTTPException(status_code=500, detail="Error al cargar reportes")
    finally:
        cur.close()
        conn.close()

# 4. RUTA PARA VER BLOQUEOS
@router.get("/bloqueos")
def obtener_bloqueos():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT b.id, b.fecha_bloqueo, 
                   u_bloqueador.nombre as quien_bloqueo, 
                   u_bloqueado.nombre as quien_fue_bloqueado
            FROM bloqueos b
            LEFT JOIN usuarios u_bloqueador ON b.bloqueador_id = u_bloqueador.id
            LEFT JOIN usuarios u_bloqueado ON b.bloqueado_id = u_bloqueado.id
            ORDER BY b.fecha_bloqueo DESC
            LIMIT 50
        """)
        resultados = cur.fetchall()
        
        return [
            {
                "id": r[0],
                "quien_bloqueo": r[2] or "Desconocido",
                "quien_fue_bloqueado": r[3] or "Desconocido",
                "fecha": str(r[1])
            } for r in resultados
        ]
    except Exception as e:
        print(f"Error en bloqueos: {e}")
        raise HTTPException(status_code=500, detail="Error al cargar bloqueos")
    finally:
        cur.close()
        conn.close()