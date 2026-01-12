from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import bcrypt
import os
from dotenv import load_dotenv
import httpx
from datetime import datetime, timezone
from typing import Optional

load_dotenv()

email_router = APIRouter()

# ==========================================
#  MODELOS DE DATOS (PYDANTIC)
# ==========================================

# Modelos para la WEB
class EmailAuthRequest(BaseModel):
    email: str
    password: str
    tipo: str = "emprendedor"
    target: str = "perfil"
    g_recaptcha_response: str
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

class RegisterRequest(BaseModel):
    nombre: str
    email: str
    password: str
    tipo: str = "emprendedor"
    target: str = "perfil"
    g_recaptcha_response: str
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

# Modelos para la APP MÓVIL
class LoginRequestApp(BaseModel):
    email: str
    password: str
    tipo: str = "emprendedor"
    target: str = "perfil"

class RegisterRequestApp(BaseModel):
    nombre: str
    email: str
    password: str
    tipo: str = "emprendedor"
    target: str = "perfil"

# ==========================================
#  FUNCIONES AUXILIARES
# ==========================================

def get_db_connection():
    try:
        conn = psycopg2.connect(
            database=os.getenv("DB_NAME", "prendia_db"),
            user=os.getenv("DB_USER", "prendiax_user"),
            password=os.getenv("DB_PASSWORD", "Elbicho7"),
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            cursor_factory=RealDictCursor
        )
        return conn
    except Exception as e:
        print(f"[ERROR] DB Connection: {e}")
        raise HTTPException(status_code=500, detail="Error de conexión a BD")

async def verify_recaptcha(token: str, ip: str) -> bool:
    # Si estás probando en local y te da flojera el captcha, descomenta esto:
    # return True 
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://www.google.com/recaptcha/api/siteverify",
                data={
                    "secret": os.getenv("RECAPTCHA_SECRET_KEY"),
                    "response": token,
                    "remoteip": ip
                }
            )
            result = response.json()
            return result.get("success", False) and result.get("score", 1.0) >= 0.5
    except Exception as e:
        print(f"[ERROR] Recaptcha: {e}")
        return False

def is_ip_blocked(ip: str, conn) -> bool:
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM ips_bloqueadas WHERE ip = %s", (ip,))
            return cursor.fetchone() is not None
    except:
        return False

def is_user_quarantined(email: str, conn) -> bool:
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT quarantined FROM usuarios WHERE email = %s", (email,))
            user = cursor.fetchone()
            return user and user["quarantined"]
    except:
        return False

def log_failed_attempt(email: str, ip: str, conn):
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO usuarios_cuarentena (id, nombre, email, password, verified, verification_token, created_at, ip_address, user_agent, quarantined, quarantined_at)
                SELECT id, nombre, email, password, verified, verification_token, created_at, ip_address, user_agent, TRUE, %s
                FROM usuarios WHERE email = %s
                ON CONFLICT DO NOTHING
                """,
                (datetime.now(timezone.utc), email)
            )
            cursor.execute("UPDATE usuarios SET quarantined = TRUE WHERE email = %s", (email,))
            conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Failed attempt log: {e}")

# ==========================================
#  RUTAS WEB
# ==========================================

@email_router.post("/auth/email")
async def login_via_email(data: EmailAuthRequest, request: Request):
    print(f"--- [DEBUG WEB] INTENTO DE LOGIN: {data.email} ---") 
    try:
        ip_address = data.ip_address or request.client.host
        user_agent = data.user_agent or request.headers.get("user-agent")
        
        # Validaciones básicas
        if not data.email or not data.password:
             raise HTTPException(status_code=400, detail="Faltan datos")

        conn = get_db_connection()
        
        # 1. Validaciones de Seguridad
        if is_ip_blocked(ip_address, conn):
            conn.close()
            raise HTTPException(status_code=403, detail="IP bloqueada")
            
        if is_user_quarantined(data.email, conn):
            conn.close()
            raise HTTPException(status_code=403, detail="Usuario en cuarentena")

        if not await verify_recaptcha(data.g_recaptcha_response, ip_address):
            log_failed_attempt(data.email, ip_address, conn)
            conn.close()
            raise HTTPException(status_code=400, detail="Captcha inválido")

        # 2. Lógica de Login CORREGIDA
        try:
            cursor = conn.cursor()
            
            # --- CORRECCIÓN CLAVE: Quitamos 'tipo' del SELECT ---
            # Antes: SELECT id, nombre, password, verified, tipo ... (ERROR)
            # Ahora: SELECT id, nombre, password, verified ... (CORRECTO)
            cursor.execute("SELECT id, nombre, password, verified FROM usuarios WHERE email = %s", (data.email,))
            user = cursor.fetchone()

            if not user:
                # Logueamos intento fallido para seguridad
                log_failed_attempt(data.email, ip_address, conn)
                raise HTTPException(status_code=400, detail="Credenciales incorrectas")
            
            if not user["password"]:
                raise HTTPException(status_code=400, detail="Esta cuenta usa Google Login")

            # Verificar contraseña
            if not bcrypt.checkpw(data.password.encode('utf-8'), user["password"].encode('utf-8')):
                log_failed_attempt(data.email, ip_address, conn)
                raise HTTPException(status_code=400, detail="Credenciales incorrectas")

            # Actualizar datos de sesión
            cursor.execute(
                "UPDATE usuarios SET ip_address = %s, user_agent = %s, verified = TRUE WHERE email = %s",
                (ip_address, user_agent, data.email)
            )
            conn.commit()

            # --- Determinar redirección ---
            tiene_datos = False
            try:
                cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s;", (user["id"],))
                tiene_datos = cursor.fetchone() is not None
            except:
                pass # Si falla la tabla, asumimos que no tiene datos

            # Usamos el tipo que viene del FRONTEND (data.tipo), no de la BD
            redirect_url = "/perfil-especifico" if data.tipo == "explorador" else ("/perfil" if tiene_datos else "/dashboard")

            return {
                "user_id": user["id"],
                "email": data.email,
                "name": user["nombre"],
                "tipo": data.tipo,
                "token": "fake_web_token", 
                "redirect_url": redirect_url
            }

        except HTTPException as he:
            conn.rollback()
            raise he
        except Exception as e:
            conn.rollback()
            print(f"[ERROR SQL WEB] {e}")
            # Esto mostrará el error real en tu pantalla web si sigue fallando
            raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")
        finally:
            conn.close()

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"[ERROR GENERAL WEB] {e}")
        raise HTTPException(status_code=500, detail="Error crítico del servidor")

@email_router.post("/auth/register")
async def register_via_email(data: RegisterRequest, request: Request):
    try:
        ip_address = data.ip_address or request.client.host
        user_agent = data.user_agent or request.headers.get("user-agent")

        conn = get_db_connection()

        if is_ip_blocked(ip_address, conn):
            conn.close()
            raise HTTPException(status_code=403, detail="IP bloqueada")

        if not await verify_recaptcha(data.g_recaptcha_response, ip_address):
            conn.close()
            raise HTTPException(status_code=400, detail="Captcha inválido")

        hashed = bcrypt.hashpw(data.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM usuarios WHERE email = %s", (data.email,))
            if cursor.fetchone():
                raise HTTPException(status_code=400, detail="El correo ya existe")

            cursor.execute(
                """
                INSERT INTO usuarios (nombre, email, password, ip_address, user_agent, verified, created_at)
                VALUES (%s, %s, %s, %s, %s, TRUE, NOW())
                RETURNING id
                """,
                (data.nombre, data.email, hashed, ip_address, user_agent)
            )
            user_id = cursor.fetchone()["id"]
            conn.commit()

            redirect_url = "/perfil-especifico" if data.tipo == "explorador" else "/dashboard"

            return {
                "user_id": user_id,
                "email": data.email,
                "name": data.nombre,
                "tipo": data.tipo,
                "token": "fake_web_token",
                "redirect_url": redirect_url
            }

        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"[ERROR WEB REG] {e}")
        raise HTTPException(status_code=500, detail="Error interno")

# ==========================================
#  RUTAS APP (API REST PARA FLUTTER)
# ==========================================

@email_router.post("/api/auth/email")
async def login_via_email_app(datos: LoginRequestApp):
    print(f"[APP LOGIN] Iniciando sesión para: {datos.email}")
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # 1. Buscamos al usuario
        cursor.execute("SELECT id, nombre, password, email FROM usuarios WHERE email = %s", (datos.email,))
        user = cursor.fetchone()

        if not user:
            print("[APP LOGIN] Usuario no encontrado en tabla usuarios")
            raise HTTPException(status_code=401, detail="Usuario no encontrado")
        
        if not user["password"]:
             raise HTTPException(status_code=400, detail="Esta cuenta usa Google/Apple Login")

        if not bcrypt.checkpw(datos.password.encode('utf-8'), user["password"].encode('utf-8')):
             print("[APP LOGIN] Contraseña incorrecta")
             raise HTTPException(status_code=401, detail="Contraseña incorrecta")

        # 2. Verificar si tiene datos de negocio (PERFIL)
        user_id = user['id']
        print(f"[APP LOGIN] Usuario ID: {user_id}. Verificando tabla datos_usuario...")
        
        cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s", (user_id,))
        resultado_datos = cursor.fetchone()
        
        tiene_datos = resultado_datos is not None
        print(f"[APP LOGIN] ¿Tiene datos de negocio?: {'SÍ' if tiene_datos else 'NO'}")

        # 3. Decidir a dónde mandarlo
        if datos.tipo == "explorador":
            redirect_url = "/perfil-especifico"
        elif tiene_datos:
            redirect_url = "/perfil"
        else:
            redirect_url = "/dashboard"

        # Generar Token
        fake_token = f"jwt_app_{user_id}"

        return {
            "status": "ok",
            "token": fake_token,
            "user_id": user_id,
            "email": user["email"],
            "name": user["nombre"],
            "tipo": datos.tipo, 
            "redirect_url": redirect_url
        }

    except psycopg2.Error as db_error:
        print(f"[DB ERROR CRÍTICO] {db_error}")
        raise HTTPException(status_code=500, detail="Error de base de datos al verificar perfil")
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"[APP ERROR GENERAL] {e}") 
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    finally:
        conn.close()

@email_router.post("/api/auth/register")
async def register_via_email_app(datos: RegisterRequestApp):
    print(f"[APP REGISTER] Intento: {datos.email}")
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # 1. Verificar existencia
        cursor.execute("SELECT id FROM usuarios WHERE email = %s", (datos.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="El correo ya está registrado")

        # 2. Hashear password
        hashed_pw = bcrypt.hashpw(datos.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        # 3. Insertar
        cursor.execute(
            """
            INSERT INTO usuarios (nombre, email, password, verified, created_at, user_agent)
            VALUES (%s, %s, %s, TRUE, NOW(), 'PrendiaX-App')
            RETURNING id
            """,
            (datos.nombre, datos.email, hashed_pw)
        )
        user_id = cursor.fetchone()["id"]
        conn.commit()

        # 4. Respuesta Exitosa (200 OK)
        redirect_url = "/perfil-especifico" if datos.tipo == "explorador" else "/dashboard"
        
        return {
            "status": "ok",
            "token": f"jwt_app_{user_id}",
            "user_id": user_id,
            "email": datos.email,
            "name": datos.nombre,
            "tipo": datos.tipo,
            "redirect_url": redirect_url
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        conn.rollback()
        print(f"[APP ERROR REGISTER] {e}")
        raise HTTPException(status_code=500, detail=f"Error al registrar: {str(e)}")
    finally:
        conn.close()

@email_router.get("/api/auth/current_user")
async def get_current_user_api(request: Request):
    """
    Endpoint específico para la APP MÓVIL que devuelve JSON.
    Soporta tanto Sesión (Web) como Token Fake (App).
    """
    conn = None
    try:
        user_id = None
        tipo = "explorador" # Default

        # 1. INTENTO POR SESIÓN (Para cuando entras desde la Web)
        user_session = request.session.get("user")
        if user_session:
            user_id = user_session.get("id")
            tipo = user_session.get("tipo", "explorador")
        
        # 2. INTENTO POR TOKEN (Para la App Flutter)
        if not user_id:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1] 
                
                if token.startswith("jwt_app_"):
                    try:
                        user_id_str = token.replace("jwt_app_", "")
                        user_id = int(user_id_str)
                    except ValueError:
                        print("[AUTH ERROR] Token mal formado")
        
        if not user_id:
             print(f"[DEBUG] Headers recibidos: {request.headers}")
             raise HTTPException(status_code=401, detail="No autenticado")

        # 3. BUSCAR EN BASE DE DATOS
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, nombre, email, verified
            FROM usuarios
            WHERE id = %s
        """, (user_id,))
        
        user = cursor.fetchone()

        if not user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
        imagen_url = None
        try:
            cursor.execute("SELECT imagen_url FROM datos_usuario WHERE user_id = %s", (user_id,))
            data = cursor.fetchone()
            if data:
                imagen_url = data.get("imagen_url")
        except:
            pass 

        # 4. RESPUESTA JSON
        return JSONResponse(content={
            "id": user["id"],
            "usuario_nombre": user["nombre"],
            "email": user["email"],
            "tipo": tipo, 
            "imagen_url": imagen_url
        })

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"[ERROR] /auth/current_user: {e}")
        return JSONResponse(
            status_code=500, 
            content={"detail": f"Error interno: {str(e)}"}
        )
    finally:
        if conn:
            conn.close()