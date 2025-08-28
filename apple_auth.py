from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth
import jwt
import time
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv

load_dotenv()

apple_router = APIRouter()

# 🔧 Conexión a la base de datos
def get_db_connection():
    try:
        conn = psycopg2.connect(
            database=os.getenv("DB_NAME", "prendia_db"),
            user=os.getenv("DB_USER", "postgres"),
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

# 🔐 Generar client_secret para Apple
def generate_apple_client_secret():
    try:
        private_key_path = os.getenv("APPLE_PRIVATE_KEY_PATH", "AuthKey_YFNS7NW42N.p8")
        if not os.path.exists(private_key_path):
            print(f"[ERROR] /apple_auth: No se encontró el archivo .p8 en {private_key_path}")
            raise HTTPException(status_code=500, detail="Archivo de clave privada no encontrado")
        
        with open(private_key_path, "r") as key_file:
            private_key = key_file.read()
        
        now = int(time.time())
        payload = {
            "iss": os.getenv("APPLE_TEAM_ID", "ZRTLHL9GXR"),
            "iat": now,
            "exp": now + 3600,  # 1 hora de validez
            "aud": "https://appleid.apple.com",
            "sub": os.getenv("APPLE_CLIENT_ID", "com.prendiax.web.service")
        }
        headers = {"kid": os.getenv("APPLE_KEY_ID", "YFNS7NW42N")}
        client_secret = jwt.encode(payload, private_key, algorithm="ES256", headers=headers)
        print("[DEBUG] /apple_auth: Client secret generado")
        return client_secret
    except Exception as e:
        print(f"[ERROR] /apple_auth: Error al generar client_secret: {e}")
        raise HTTPException(status_code=500, detail=f"Error al generar client_secret: {str(e)}")

# 🔐 Configurar OAuth con Apple
oauth = OAuth()
oauth.register(
    name="apple",
    client_id=os.getenv("APPLE_CLIENT_ID", "com.prendiax.web.service"),
    client_secret=generate_apple_client_secret,
    server_metadata_url="https://appleid.apple.com/.well-known/openid-configuration",
    client_kwargs={
        "scope": "name email",
        "response_type": "code id_token",
        "response_mode": "form_post",
        "prompt": "select_account"
    }
)

# 🔁 Ruta de autenticación con Apple
@apple_router.get("/auth/apple")
async def login_via_apple(request: Request):
    try:
        print(f"[DEBUG] /auth/apple: Sesión existente: {request.session}")
        tipo = request.query_params.get("tipo", request.session.get("tipo", "explorador"))
        target = request.query_params.get("target", "perfil-especifico" if tipo == "explorador" else "perfil")
        redirect_uri = os.getenv("APPLE_REDIRECT_URI", "https://prendiax.com/auth/apple/callback")
        request.session.update({"tipo": tipo, "target": target})
        print(f"[DEBUG] /auth/apple: tipo={tipo}, target={target}, redirect_uri={redirect_uri}")
        return await oauth.apple.authorize_redirect(request, redirect_uri)
    except Exception as e:
        print(f"[ERROR] /auth/apple: Error: {e}")
        return RedirectResponse(url=f"/login?tipo={tipo}&target={target}", status_code=302)

# ✅ Callback de Apple
@apple_router.post("/auth/apple/callback")
async def auth_apple_callback(request: Request):
    try:
        print("[DEBUG] /auth/apple/callback: Iniciando callback")
        # Verificar el contenido del form-data recibido
        form_data = await request.form()
        print(f"[DEBUG] /auth/apple/callback: Form data recibido: {dict(form_data)}")
        
        try:
            token = await oauth.apple.authorize_access_token(request)
            print(f"[DEBUG] /auth/apple/callback: Token recibido: {token}")
        except Exception as e:
            print(f"[ERROR] /auth/apple/callback: Error en authorize_access_token: {e}")
            raise

        decoded = jwt.decode(token.get("id_token"), options={"verify_signature": False})
        print(f"[DEBUG] /auth/apple/callback: id_token decodificado: {decoded}")
        
        email = decoded.get("email")
        name = decoded.get("name", email.split("@")[0] if email else "Usuario Apple")

        # Manejar caso donde el email no está disponible
        if not email:
            print("[ERROR] /auth/apple/callback: No se proporcionó un correo electrónico")
            return RedirectResponse(url="/login?tipo=emprendedor&target=perfil&error=no_email", status_code=302)

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # Verificar si el email ya existe
            cursor.execute("SELECT id FROM usuarios WHERE email = %s", (email,))
            user = cursor.fetchone()

            if user:
                # Email ya registrado, usar el id existente
                user_id = user["id"]
                print(f"[DEBUG] /auth/apple/callback: Email existente, user_id={user_id}")
                # Actualizar nombre si cambió
                cursor.execute(
                    """
                    UPDATE usuarios
                    SET nombre = %s
                    WHERE id = %s
                    """,
                    (name, user_id)
                )
            else:
                # Crear nuevo usuario
                cursor.execute(
                    """
                    INSERT INTO usuarios (nombre, email)
                    VALUES (%s, %s)
                    RETURNING id
                    """,
                    (name, email)
                )
                user_id = cursor.fetchone()["id"]
                print(f"[DEBUG] /auth/apple/callback: Nuevo usuario creado, user_id={user_id}")

            conn.commit()
            # Guardar datos de sesión
            tipo = request.session.get("tipo", "explorador")
            request.session["user"] = {"id": user_id, "email": email, "name": name, "tipo": tipo}
            print(f"[DEBUG] /auth/apple/callback: Sesión creada: {request.session}")

            # Verificar datos existentes en datos_usuario antes de redirigir
            cursor.execute("SELECT nombre_empresa, descripcion FROM datos_usuario WHERE user_id = %s", (user_id,))
            datos_usuario = cursor.fetchone()
            print(f"[DEBUG] /auth/apple/callback: Datos en datos_usuario: {datos_usuario}")

            # Redirección según tipo
            if tipo == "explorador":
                print("[DEBUG] /auth/apple/callback: Tipo explorador detectado")
                # Eliminar datos_usuario si existen
                cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s", (user_id,))
                if cursor.fetchone():
                    print("[DEBUG] /auth/apple/callback: Datos existentes, eliminando para explorador")
                    cursor.execute("DELETE FROM datos_usuario WHERE user_id = %s", (user_id,))
                    conn.commit()
                return RedirectResponse(url="/perfil-especifico", status_code=302)
            else:
                cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s", (user_id,))
                ya_tiene_datos = cursor.fetchone()
                print(f"[DEBUG] /auth/apple/callback: ¿Datos?: {ya_tiene_datos is not None}")
                url = "/perfil" if ya_tiene_datos else "/dashboard"
                print(f"[DEBUG] /auth/apple/callback: Redirigiendo a {url}")
                return RedirectResponse(url=url, status_code=302)

        except Exception as e:
            conn.rollback()
            print(f"[ERROR] /auth/apple/callback: Error en la base de datos: {e}")
            raise HTTPException(status_code=500, detail=f"Error en la base de datos: {str(e)}")
        finally:
            cursor.close()
            conn.close()

    except Exception as e:
        print(f"[ERROR] /auth/apple/callback: Error en la autenticación: {e}")
        return RedirectResponse(url="/login?tipo=emprendedor&target=perfil&error=auth_failed", status_code=302)