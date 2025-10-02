from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
import psycopg2
from psycopg2.extras import RealDictCursor
import bcrypt
import os
from dotenv import load_dotenv
import httpx
from datetime import datetime, timezone

load_dotenv()

email_router = APIRouter()

# 🔧 Conexión a la base de datos
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
        print("[DEBUG] Conexión a la base de datos exitosa")
        return conn
    except Exception as e:
        print(f"[ERROR] Error al conectar a la base de datos: {e}")
        raise HTTPException(status_code=500, detail="Error al conectar a la base de datos")

# 🔍 Verificar reCAPTCHA
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
            print(f"[DEBUG] Respuesta de reCAPTCHA: {result}")
            return result.get("success", False) and result.get("score", 1.0) >= 0.5
    except Exception as e:
        print(f"[ERROR] Error al verificar reCAPTCHA: {e}")
        return False

# 🔍 Verificar si la IP está bloqueada
def is_ip_blocked(ip: str, conn) -> bool:
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM ips_bloqueadas WHERE ip = %s", (ip,))
        return cursor.fetchone() is not None
    except Exception as e:
        print(f"[ERROR] Error al verificar IP bloqueada: {e}")
        return False
    finally:
        cursor.close()

# 🔍 Verificar si el usuario está en cuarentena
def is_user_quarantined(email: str, conn) -> bool:
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT quarantined FROM usuarios WHERE email = %s", (email,))
        user = cursor.fetchone()
        return user and user["quarantined"]
    except Exception as e:
        print(f"[ERROR] Error al verificar cuarentena: {e}")
        return False
    finally:
        cursor.close()

# 📝 Registrar intento fallido y manejar cuarentena
def log_failed_attempt(email: str, ip: str, conn):
    try:
        cursor = conn.cursor()
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
        print(f"[DEBUG] Intento fallido registrado, usuario {email} en cuarentena")
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Error al registrar intento fallido: {e}")
    finally:
        cursor.close()

# 🔁 Ruta de autenticación con email
@email_router.post("/auth/email")
async def login_via_email(request: Request):
    try:
        form_data = await request.form()
        email = form_data.get("email")
        password = form_data.get("password")
        tipo = form_data.get("tipo", "emprendedor")
        target = form_data.get("target", "perfil")
        captcha_response = form_data.get("g-recaptcha-response")
        ip_address = request.client.host
        user_agent = request.headers.get("user-agent")

        print(f"[DEBUG] /auth/email: email={email}, tipo={tipo}, target={target}, ip={ip_address}, user_agent={user_agent}")

        if not email or not password or not captcha_response:
            print("[ERROR] /auth/email: Correo, contraseña o CAPTCHA no proporcionados")
            return RedirectResponse(
                url=f"/login?tipo={tipo}&target={target}&error=invalid_input",
                status_code=302
            )

        # Verificar si la IP está bloqueada
        conn = get_db_connection()
        if is_ip_blocked(ip_address, conn):
            print("[ERROR] /auth/email: IP bloqueada")
            conn.close()
            return RedirectResponse(
                url=f"/login?tipo={tipo}&target={target}&error=ip_blocked",
                status_code=302
            )

        # Verificar si el usuario está en cuarentena
        if is_user_quarantined(email, conn):
            print("[ERROR] /auth/email: Usuario en cuarentena")
            conn.close()
            return RedirectResponse(
                url=f"/login?tipo={tipo}&target={target}&error=quarantined",
                status_code=302
            )

        # Verificar reCAPTCHA
        if not await verify_recaptcha(captcha_response, ip_address):
            print("[ERROR] /auth/email: Verificación de CAPTCHA fallida")
            log_failed_attempt(email, ip_address, conn)
            conn.close()
            return RedirectResponse(
                url=f"/login?tipo={tipo}&target={target}&error=captcha_failed",
                status_code=302
            )

        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, nombre, password, verified
                FROM usuarios
                WHERE email = %s
                """,
                (email,)
            )
            user = cursor.fetchone()

            if not user:
                print("[ERROR] /auth/email: Correo no registrado")
                log_failed_attempt(email, ip_address, conn)
                return RedirectResponse(
                    url=f"/login?tipo={tipo}&target={target}&error=invalid_credentials",
                    status_code=302
                )

            if user["password"] is None:
                print("[ERROR] /auth/email: Correo registrado con Google")
                return RedirectResponse(
                    url=f"/login?tipo={tipo}&target={target}&error=google_account",
                    status_code=302
                )

            if not bcrypt.checkpw(password.encode('utf-8'), user["password"].encode('utf-8')):
                print("[ERROR] /auth/email: Contraseña incorrecta")
                log_failed_attempt(email, ip_address, conn)
                return RedirectResponse(
                    url=f"/login?tipo={tipo}&target={target}&error=invalid_credentials",
                    status_code=302
                )

            # Actualizar IP y user agent
            cursor.execute(
                """
                UPDATE usuarios
                SET ip_address = %s, user_agent = %s, verified = TRUE
                WHERE email = %s
                """,
                (ip_address, user_agent, email)
            )
            conn.commit()

            user_id = user["id"]
            name = user["nombre"]
            print(f"[DEBUG] /auth/email: Email válido, user_id={user_id}, nombre={name}")

            request.session["user"] = {"id": user_id, "email": email, "name": name, "tipo": tipo}
            print(f"[DEBUG] /auth/email: Sesión creada: {request.session}")

            if tipo == "explorador":
                print("[DEBUG] /auth/email: Redirigiendo a /perfil-especifico por tipo=explorador")
                cursor.execute("DELETE FROM datos_usuario WHERE user_id = %s;", (user_id,))
                conn.commit()
                return RedirectResponse(url="/perfil-especifico", status_code=302)

            cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s;", (user_id,))
            ya_tiene_datos = cursor.fetchone()
            print(f"[DEBUG] /auth/email: ¿Usuario tiene datos?: {ya_tiene_datos is not None}")

            if ya_tiene_datos:
                print("[DEBUG] /auth/email: Redirigiendo a /perfil (datos existentes)")
                return RedirectResponse(url="/perfil", status_code=302)
            else:
                print("[DEBUG] /auth/email: Redirigiendo a /dashboard (sin datos)")
                return RedirectResponse(url="/dashboard", status_code=302)

        except Exception as e:
            conn.rollback()
            print(f"[ERROR] /auth/email: Error en la base de datos: {e}")
            raise HTTPException(status_code=500, detail=f"Error en la base de datos: {str(e)}")
        finally:
            cursor.close()
            conn.close()

    except Exception as e:
        print(f"[ERROR] /auth/email: Error general en la autenticación: {e}")
        return RedirectResponse(
            url=f"/login?tipo={tipo}&target={target}&error=auth_failed&details=general_error:{str(e)}",
            status_code=302
        )

# 🔁 Ruta de registro con email
@email_router.post("/auth/register")
async def register_via_email(request: Request):
    try:
        form_data = await request.form()
        email = form_data.get("email")
        password = form_data.get("password")
        nombre = form_data.get("nombre")
        tipo = form_data.get("tipo", "emprendedor")
        target = form_data.get("target", "perfil")
        captcha_response = form_data.get("g-recaptcha-response")
        ip_address = request.client.host
        user_agent = request.headers.get("user-agent")

        print(f"[DEBUG] /auth/register: email={email}, nombre={nombre}, tipo={tipo}, target={target}, ip={ip_address}, user_agent={user_agent}")

        if not email or not password or not nombre or not captcha_response:
            print("[ERROR] /auth/register: Campos incompletos")
            return RedirectResponse(
                url=f"/login?tipo={tipo}&target={target}&error=invalid_input",
                status_code=302
            )

        # Verificar si la IP está bloqueada
        conn = get_db_connection()
        if is_ip_blocked(ip_address, conn):
            print("[ERROR] /auth/register: IP bloqueada")
            conn.close()
            return RedirectResponse(
                url=f"/login?tipo={tipo}&target={target}&error=ip_blocked",
                status_code=302
            )

        # Verificar reCAPTCHA
        if not await verify_recaptcha(captcha_response, ip_address):
            print("[ERROR] /auth/register: Verificación de CAPTCHA fallida")
            log_failed_attempt(email, ip_address, conn)
            conn.close()
            return RedirectResponse(
                url=f"/login?tipo={tipo}&target={target}&error=captcha_failed",
                status_code=302
            )

        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM usuarios WHERE email = %s", (email,))
            if cursor.fetchone():
                print("[ERROR] /auth/register: El correo ya está registrado")
                return RedirectResponse(
                    url=f"/login?tipo={tipo}&target={target}&error=email_exists",
                    status_code=302
                )

            cursor.execute(
                """
                INSERT INTO usuarios (nombre, email, password, ip_address, user_agent, verified)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (nombre, email, hashed_password, ip_address, user_agent, True)
            )
            user_id = cursor.fetchone()["id"]
            print(f"[DEBUG] /auth/register: Nuevo usuario creado, user_id={user_id}")

            conn.commit()
            request.session["user"] = {"id": user_id, "email": email, "name": nombre, "tipo": tipo}
            print(f"[DEBUG] /auth/register: Sesión creada: {request.session}")

            if tipo == "explorador":
                print("[DEBUG] /auth/register: Redirigiendo a /perfil-especifico por tipo=explorador")
                cursor.execute("DELETE FROM datos_usuario WHERE user_id = %s;", (user_id,))
                conn.commit()
                return RedirectResponse(url="/perfil-especifico", status_code=302)

            cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s;", (user_id,))
            ya_tiene_datos = cursor.fetchone()
            print(f"[DEBUG] /auth/register: ¿Usuario tiene datos?: {ya_tiene_datos is not None}")

            if ya_tiene_datos:
                print("[DEBUG] /auth/register: Redirigiendo a /perfil (datos existentes)")
                return RedirectResponse(url="/perfil", status_code=302)
            else:
                print("[DEBUG] /auth/register: Redirigiendo a /dashboard (sin datos)")
                return RedirectResponse(url="/dashboard", status_code=302)

        except Exception as e:
            conn.rollback()
            print(f"[ERROR] /auth/register: Error en la base de datos: {e}")
            raise HTTPException(status_code=500, detail=f"Error en la base de datos: {str(e)}")
        finally:
            cursor.close()
            conn.close()

    except Exception as e:
        print(f"[ERROR] /auth/register: Error general en el registro: {e}")
        return RedirectResponse(
            url=f"/login?tipo={tipo}&target={target}&error=auth_failed&details=general_error:{str(e)}",
            status_code=302
        )