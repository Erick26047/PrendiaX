from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
import psycopg2
from psycopg2.extras import RealDictCursor
import bcrypt
from dotenv import load_dotenv
import os

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

# 🔁 Ruta de autenticación con email
@email_router.post("/auth/email")
async def login_via_email(request: Request):
    try:
        form_data = await request.form()
        email = form_data.get("email")
        password = form_data.get("password")
        tipo = form_data.get("tipo", "emprendedor")
        target = form_data.get("target", "perfil")

        print(f"[DEBUG] /auth/email: email={email}, tipo={tipo}, target={target}")

        if not email or not password:
            print("[ERROR] /auth/email: Correo o contraseña no proporcionados")
            return RedirectResponse(
                url=f"/login?tipo={tipo}&target={target}&error=invalid_credentials",
                status_code=302
            )

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, nombre, password FROM usuarios WHERE email = %s", (email,))
            user = cursor.fetchone()

            if not user:
                print("[ERROR] /auth/email: Correo no registrado")
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
                return RedirectResponse(
                    url=f"/login?tipo={tipo}&target={target}&error=invalid_credentials",
                    status_code=302
                )

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

        print(f"[DEBUG] /auth/register: email={email}, nombre={nombre}, tipo={tipo}, target={target}")

        if not email or not password or not nombre:
            print("[ERROR] /auth/register: Campos incompletos")
            return RedirectResponse(
                url=f"/login?tipo={tipo}&target={target}&error=invalid_input",
                status_code=302
            )

        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id FROM usuarios WHERE email = %s", (email,))
            if cursor.fetchone():
                print("[ERROR] /auth/register: El correo ya está registrado")
                return RedirectResponse(
                    url=f"/login?tipo={tipo}&target={target}&error=email_exists",
                    status_code=302
                )

            cursor.execute(
                """
                INSERT INTO usuarios (nombre, email, password)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (nombre, email, hashed_password)
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