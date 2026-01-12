from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import JSONResponse, RedirectResponse  # <--- IMPORTANTE: RedirectResponse
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import bcrypt
import os
from dotenv import load_dotenv
import httpx
from datetime import datetime, timezone
import traceback

load_dotenv()

email_router = APIRouter()

# ==========================================
#  MODELOS DE DATOS (PYDANTIC)
# ==========================================

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
#  RUTAS WEB (LOGIN CON MANEJO DE ERRORES VISUALES)
# ==========================================

@email_router.post("/auth/email")
async def login_via_email(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    tipo: str = Form("emprendedor"),
    target: str = Form("perfil"),
    g_recaptcha_response: str = Form(..., alias="g-recaptcha-response")
):
    # URL base para regresar si algo sale mal (mantenemos el tipo y target)
    error_url = f"/?tipo={tipo}&target={target}&error=" 
    # NOTA: Si tu login está en /login, cambia "/" por "/login" arriba.

    try:
        ip_address = request.client.host
        user_agent = request.headers.get("user-agent")
        
        # 1. Validar Captcha
        if not await verify_recaptcha(g_recaptcha_response, ip_address):
            conn = get_db_connection()
            log_failed_attempt(email, ip_address, conn)
            conn.close()
            # EN LUGAR DE ERROR 400, REDIRIGIMOS:
            return RedirectResponse(url=error_url + "captcha_failed", status_code=303)

        conn = get_db_connection()
        
        # Validaciones de seguridad
        if is_ip_blocked(ip_address, conn):
            conn.close()
            return RedirectResponse(url=error_url + "ip_blocked", status_code=303)
            
        if is_user_quarantined(email, conn):
            conn.close()
            return RedirectResponse(url=error_url + "quarantined", status_code=303)

        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id, nombre, password, verified FROM usuarios WHERE email = %s", (email,))
            user = cursor.fetchone()

            # 2. Verificar Usuario y Contraseña
            if not user or not user["password"] or not bcrypt.checkpw(password.encode('utf-8'), user["password"].encode('utf-8')):
                log_failed_attempt(email, ip_address, conn)
                # AQUÍ ESTÁ LA MAGIA: Regresamos con el error en la URL
                return RedirectResponse(url=error_url + "invalid_credentials", status_code=303)

            # Actualizar datos técnicos
            cursor.execute(
                "UPDATE usuarios SET ip_address = %s, user_agent = %s, verified = TRUE WHERE email = %s",
                (ip_address, user_agent, email)
            )
            conn.commit()

            # Redirección de Éxito
            cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s;", (user["id"],))
            tiene_datos = cursor.fetchone() is not None
            
            redirect_url = "/perfil-especifico" if tipo == "explorador" else ("/perfil" if tiene_datos else "/dashboard")

            # Guardar sesión
            request.session["user"] = {
                "id": user["id"],
                "email": email,
                "nombre": user["nombre"],
                "tipo": tipo
            }

            return RedirectResponse(url=redirect_url, status_code=303)

        except Exception as e:
            conn.rollback()
            print(f"[ERROR SQL LOGIN] {traceback.format_exc()}")
            return RedirectResponse(url=error_url + "auth_failed", status_code=303)
        finally:
            conn.close()

    except Exception as e:
        print(f"[ERROR GENERAL LOGIN] {traceback.format_exc()}")
        return RedirectResponse(url=error_url + "auth_init_failed", status_code=303)


@email_router.post("/auth/register")
async def register_via_email(
    request: Request,
    nombre: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    fecha_nacimiento: str = Form(...), 
    tipo: str = Form("emprendedor"),   
    target: str = Form("perfil"),
    g_recaptcha_response: str = Form(..., alias="g-recaptcha-response")
):
    # URL base para regresar si hay error
    error_url = f"/?tipo={tipo}&target={target}&error="

    try:
        ip_address = request.client.host
        user_agent = request.headers.get("user-agent")

        conn = get_db_connection()

        if is_ip_blocked(ip_address, conn):
            conn.close()
            return RedirectResponse(url=error_url + "ip_blocked", status_code=303)

        if not await verify_recaptcha(g_recaptcha_response, ip_address):
            conn.close()
            return RedirectResponse(url=error_url + "captcha_failed", status_code=303)

        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM usuarios WHERE email = %s", (email,))
            if cursor.fetchone():
                # SI YA EXISTE, AVISAMOS BONITO
                return RedirectResponse(url=error_url + "email_exists", status_code=303)

            cursor.execute(
                """
                INSERT INTO usuarios (nombre, email, password, ip_address, user_agent, verified, created_at)
                VALUES (%s, %s, %s, %s, %s, TRUE, NOW())
                RETURNING id
                """,
                (nombre, email, hashed, ip_address, user_agent)
            )
            user_id = cursor.fetchone()["id"]
            conn.commit()

            redirect_url = "/perfil-especifico" if tipo == "explorador" else "/dashboard"

            request.session["user"] = {
                "id": user_id,
                "email": email,
                "nombre": nombre,
                "tipo": tipo
            }
            return RedirectResponse(url=redirect_url, status_code=303)

        except Exception as e:
            conn.rollback()
            print(f"[ERROR SQL REGISTRO] {traceback.format_exc()}")
            return RedirectResponse(url=error_url + "auth_failed", status_code=303)
        finally:
            conn.close()

    except Exception as e:
        print(f"[ERROR GENERAL REGISTRO] {e}")
        return RedirectResponse(url=error_url + "auth_init_failed", status_code=303)
    
# ==========================================
#  RUTAS APP (Siguen respondiendo JSON)
# ==========================================

@email_router.post("/api/auth/email")
async def login_via_email_app(datos: LoginRequestApp):
    # ... (Tu código de app sigue igual, responde JSON para Flutter)
    print(f"[APP LOGIN] Iniciando sesión para: {datos.email}")
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, nombre, password, email FROM usuarios WHERE email = %s", (datos.email,))
        user = cursor.fetchone()

        if not user:
            raise HTTPException(status_code=401, detail="Usuario no encontrado")
        
        if not user["password"]:
             raise HTTPException(status_code=400, detail="Esta cuenta usa Google/Apple Login")

        if not bcrypt.checkpw(datos.password.encode('utf-8'), user["password"].encode('utf-8')):
             raise HTTPException(status_code=401, detail="Contraseña incorrecta")

        user_id = user['id']
        cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s", (user_id,))
        tiene_datos = cursor.fetchone() is not None
        
        if datos.tipo == "explorador":
            redirect_url = "/perfil-especifico"
        elif tiene_datos:
            redirect_url = "/perfil"
        else:
            redirect_url = "/dashboard"

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

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"[APP ERROR GENERAL] {e}") 
        raise HTTPException(status_code=500, detail="Error interno del servidor")
    finally:
        conn.close()

@email_router.post("/api/auth/register")
async def register_via_email_app(datos: RegisterRequestApp):
    # ... (Tu código de app sigue igual)
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM usuarios WHERE email = %s", (datos.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="El correo ya está registrado")

        hashed_pw = bcrypt.hashpw(datos.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

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
        raise HTTPException(status_code=500, detail=f"Error al registrar: {str(e)}")
    finally:
        conn.close()

@email_router.get("/api/auth/current_user")
async def get_current_user_api(request: Request):
    # ... (Tu código mixto sigue igual)
    conn = None
    try:
        user_id = None
        tipo = "explorador" 

        user_session = request.session.get("user")
        if user_session:
            user_id = user_session.get("id")
            tipo = user_session.get("tipo", "explorador")
        
        if not user_id:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]
                if token.startswith("jwt_app_"):
                    try:
                        user_id_str = token.replace("jwt_app_", "")
                        user_id = int(user_id_str)
                    except ValueError:
                        pass
        
        if not user_id:
             raise HTTPException(status_code=401, detail="No autenticado")

        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, nombre, email, verified FROM usuarios WHERE id = %s", (user_id,))
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
        return JSONResponse(status_code=500, content={"detail": f"Error interno: {str(e)}"})
    finally:
        if conn:
            conn.close()

