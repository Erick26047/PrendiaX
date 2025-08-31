from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth, OAuthError
import jwt
import time
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import secrets
from dotenv import load_dotenv

load_dotenv()

apple_router = APIRouter()

# 🔧 Conexión a la base de datos
def get_db_connection():
    conn = psycopg2.connect(
        database=os.getenv("DB_NAME", "prendia_db"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "Elbicho7"),
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        cursor_factory=RealDictCursor
    )
    return conn

# 🔐 Generar client_secret para Apple
def generate_apple_client_secret():
    private_key_path = os.getenv("APPLE_PRIVATE_KEY_PATH", "/root/PrendiaX/AuthKey_YFNS7NW42N.p8")
    with open(private_key_path, "r") as key_file:
        private_key = key_file.read()
    now = int(time.time())
    payload = {
        "iss": os.getenv("APPLE_TEAM_ID", "ZRTLHL9GXR"),
        "iat": now,
        "exp": now + 3600,
        "aud": "https://appleid.apple.com",
        "sub": os.getenv("APPLE_CLIENT_ID", "com.prendiax.web.service")
    }
    headers = {"kid": os.getenv("APPLE_KEY_ID", "YFNS7NW42N")}
    return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)

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
        "response_mode": "form_post"
    }
)

# 🔁 Ruta de autenticación con Apple
@apple_router.get("/auth/apple")
async def login_via_apple(request: Request):
    tipo = request.query_params.get("tipo", "explorador")
    target = request.query_params.get("target", "perfil-especifico" if tipo == "explorador" else "perfil")

    redirect_uri = request.url_for("auth_apple_callback")

    # Generar y guardar state manualmente
    state = secrets.token_urlsafe(16)
    request.session["state"] = state
    request.session["tipo"] = tipo
    request.session["target"] = target

    return await oauth.apple.authorize_redirect(request, redirect_uri, state=state)

# ✅ Callback de Apple
@apple_router.post("/auth/apple/callback", name="auth_apple_callback")
async def auth_apple_callback(request: Request):
    form_data = await request.form()
    print(f"[DEBUG] Apple form_data: {dict(form_data)}")

    # Validar el state
    state_enviado = form_data.get("state")
    state_guardado = request.session.get("state")
    if not state_enviado or state_enviado != state_guardado:
        print("[ERROR] State inválido")
        return RedirectResponse(url="/login?error=invalid_state", status_code=302)

    try:
        token = await oauth.apple.authorize_access_token(request)
        print(f"[DEBUG] Token Apple: {token}")
    except OAuthError as e:
        print(f"[ERROR] Apple OAuth: {e}")
        return RedirectResponse(url="/login?error=auth_failed", status_code=302)

    decoded = jwt.decode(token.get("id_token"), options={"verify_signature": False})
    print(f"[DEBUG] Apple id_token decodificado: {decoded}")

    email = decoded.get("email")
    name = decoded.get("name", email.split("@")[0] if email else "Usuario Apple")

    if not email:
        return RedirectResponse(url="/login?error=no_email", status_code=302)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM usuarios WHERE email = %s", (email,))
        user = cursor.fetchone()

        if user:
            user_id = user["id"]
            cursor.execute("UPDATE usuarios SET nombre=%s WHERE id=%s", (name, user_id))
        else:
            cursor.execute("INSERT INTO usuarios (nombre, email) VALUES (%s,%s) RETURNING id", (name, email))
            user_id = cursor.fetchone()["id"]

        conn.commit()

        tipo = request.session.get("tipo", "explorador")
        request.session["user"] = {"id": user_id, "email": email, "name": name, "tipo": tipo}

        if tipo == "explorador":
            cursor.execute("DELETE FROM datos_usuario WHERE user_id=%s", (user_id,))
            conn.commit()
            return RedirectResponse(url="/perfil-especifico", status_code=302)

        cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id=%s", (user_id,))
        if cursor.fetchone():
            return RedirectResponse(url="/perfil", status_code=302)
        else:
            return RedirectResponse(url="/dashboard", status_code=302)

    finally:
        cursor.close()
        conn.close()
