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
            "exp": now + 3600,  # 1 hora
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

# ✅ Callback de Apple (acepta GET y POST, log completo)
@apple_router.api_route("/auth/apple/callback", methods=["GET", "POST"])
async def auth_apple_callback(request: Request):
    try:
        print("[DEBUG] /auth/apple/callback: Iniciando callback")
        print(f"[DEBUG] Método HTTP: {request.method}")
        print(f"[DEBUG] Query params: {dict(request.query_params)}")
        print(f"[DEBUG] Headers: {dict(request.headers)}")

        form_data = {}
        try:
            form_data = await request.form()
            print(f"[DEBUG] Form data recibido: {dict(form_data)}")
        except Exception:
            print("[DEBUG] No se pudo parsear form_data (probablemente GET sin body)")

        try:
            token = await oauth.apple.authorize_access_token(request)
            print(f"[DEBUG] Token recibido: {token}")
        except Exception as e:
            print(f"[ERROR] /auth/apple/callback: Error en authorize_access_token: {e}")
            return RedirectResponse(url="/login?error=auth_failed", status_code=302)

        decoded = jwt.decode(token.get("id_token"), options={"verify_signature": False})
        print(f"[DEBUG] id_token decodificado: {decoded}")

        # Extraer datos
        email = decoded.get("email")
        sub = decoded.get("sub")
        name = decoded.get("name") or (email.split("@")[0] if email else f"AppleUser_{sub[:6]}")

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            user_id = None

            if email:
                cursor.execute("SELECT id FROM usuarios WHERE email = %s", (email,))
                user = cursor.fetchone()
                if user:
                    user_id = user["id"]
                    cursor.execute("UPDATE usuarios SET nombre = %s WHERE id = %s", (name, user_id))
                    print(f"[DEBUG] Usuario encontrado con email {email}, id={user_id}")
                else:
                    cursor.execute("INSERT INTO usuarios (nombre, email) VALUES (%s, %s) RETURNING id", (name, email))
                    user_id = cursor.fetchone()["id"]
                    print(f"[DEBUG] Nuevo usuario creado con email {email}, id={user_id}")
            else:
                fake_email = sub + "@appleid.apple.com"
                cursor.execute("SELECT id FROM usuarios WHERE email = %s", (fake_email,))
                user = cursor.fetchone()
                if user:
                    user_id = user["id"]
                    print(f"[DEBUG] Usuario encontrado con sub {sub}, id={user_id}")
                else:
                    cursor.execute("INSERT INTO usuarios (nombre, email) VALUES (%s, %s) RETURNING id", (name, fake_email))
                    user_id = cursor.fetchone()["id"]
                    print(f"[DEBUG] Nuevo usuario creado con sub {sub}, id={user_id}")

            conn.commit()

            tipo = request.session.get("tipo", "explorador")
            request.session["user"] = {"id": user_id, "email": email or fake_email, "name": name, "tipo": tipo}
            print(f"[DEBUG] Sesión creada: {request.session}")

            cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s", (user_id,))
            tiene_datos = cursor.fetchone()

            if tipo == "explorador":
                cursor.execute("DELETE FROM datos_usuario WHERE user_id = %s", (user_id,))
                conn.commit()
                return RedirectResponse(url="/perfil-especifico", status_code=302)
            else:
                url = "/perfil" if tiene_datos else "/dashboard"
                return RedirectResponse(url=url, status_code=302)

        except Exception as e:
            conn.rollback()
            print(f"[ERROR] DB: {e}")
            raise HTTPException(status_code=500, detail=f"Error en la base de datos: {str(e)}")
        finally:
            cursor.close()
            conn.close()

    except Exception as e:
        print(f"[ERROR] Auth Apple Callback: {e}")
        return RedirectResponse(url="/login?error=auth_failed", status_code=302)
