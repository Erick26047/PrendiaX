from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor
import jwt
from jwt.algorithms import RSAAlgorithm
import json
import requests
import time
from datetime import datetime, timedelta # IMPORTANTE PARA JWT

router = APIRouter()

# ==========================================
# 🔴 DATOS DE APPLE (Tus credenciales)
# ==========================================
APPLE_TEAM_ID = "ZRTLHL9GXR"
APPLE_KEY_ID = "YFNS7NW42N"
APPLE_CLIENT_ID_WEB = "com.prendiax.web.service"
APPLE_BUNDLE_ID_IOS = "com.prendiax.app" 
APPLE_PRIVATE_KEY_FILE = "AuthKey_YFNS7NW42N.p8"

# 🔴 CONFIGURACIÓN DB
DB_CONFIG = {
    "database": "prendia_db",
    "user": "postgres",
    "password": "Elbicho7",
    "host": "localhost",
    "port": "5432"
}

# ==========================================
# 🧠 LÓGICA DE FUSIÓN Y BASE DE DATOS
# ==========================================

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)

def process_unified_login(apple_sub, email, name, tipo, user_agent):
    """
    Gestiona la creación, vinculación y genera un JWT REAL.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        user_id = None
        es_nuevo = False
        
        # 1. ¿Existe por ID de Apple?
        cursor.execute("SELECT id, email FROM usuarios WHERE apple_sub = %s", (apple_sub,))
        user = cursor.fetchone()

        if user:
            print(f"[DB] Usuario Apple recurrente encontrado: ID {user['id']}")
            user_id = user['id']
            if not user['email'] and email:
                cursor.execute("UPDATE usuarios SET email = %s WHERE id = %s", (email, user_id))
                conn.commit()

        else:
            # 2. ¿Existe por Email? (Fusión)
            if email:
                cursor.execute("SELECT id FROM usuarios WHERE email = %s", (email,))
                user_email = cursor.fetchone()
                
                if user_email:
                    print(f"[DB] FUSIONANDO: El email {email} ya existía. Vinculando Apple ID.")
                    user_id = user_email['id']
                    cursor.execute("UPDATE usuarios SET apple_sub = %s WHERE id = %s", (apple_sub, user_id))
                    conn.commit()
                else:
                    # 3. Nuevo Usuario (Con Email)
                    print(f"[DB] Creando usuario nuevo para: {email}")
                    cursor.execute(
                        """
                        INSERT INTO usuarios (nombre, email, apple_sub, verified, created_at, user_agent)
                        VALUES (%s, %s, %s, TRUE, NOW(), %s)
                        RETURNING id
                        """,
                        (name, email, apple_sub, user_agent)
                    )
                    user_id = cursor.fetchone()['id']
                    conn.commit()
                    es_nuevo = True
            else:
                # 4. Nuevo Usuario (Privado / Hide My Email)
                print(f"[DB] Creando usuario privado (Hide My Email).")
                cursor.execute(
                    """
                    INSERT INTO usuarios (nombre, apple_sub, verified, created_at, user_agent)
                    VALUES (%s, %s, TRUE, NOW(), %s)
                    RETURNING id
                    """,
                    (name or "Usuario Apple", apple_sub, user_agent)
                )
                user_id = cursor.fetchone()['id']
                conn.commit()
                es_nuevo = True

        # === LÓGICA DE REDIRECCIÓN ===
        redirect_url = "/dashboard"
        if tipo == "explorador":
            redirect_url = "/perfil-especifico"
        else:
            cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s", (user_id,))
            if cursor.fetchone():
                redirect_url = "/perfil"
            else:
                redirect_url = "/dashboard"

        # 🔥🔥🔥 CORRECCIÓN: GENERAR TOKEN JWT REAL 🔥🔥🔥
        # ¡IMPORTANTE!: Esta CLAVE debe ser IGUAL a la que usas para validar tokens en el resto de tu app.
        SECRET_KEY = "TU_CLAVE_SECRETA_SUPER_SEGURA" 
        
        payload = {
            "sub": str(user_id),      # ID del usuario como string (estándar JWT)
            "user_id": user_id,       # ID como entero (por si acaso)
            "email": email,
            "tipo": tipo,
            "exp": datetime.utcnow() + timedelta(days=365), # Expira en 1 año
            "iat": datetime.utcnow()
        }
        
        # Generamos el token real
        real_token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")

        # Aseguramos que sea string (en versiones viejas de PyJWT devuelve bytes)
        if isinstance(real_token, bytes):
            real_token = real_token.decode('utf-8')

        return {
            "status": "ok",
            "token": real_token, # <--- AHORA SÍ ES UN TOKEN REAL QUE LA APP PUEDE USAR
            "user_id": user_id,
            "email": email,
            "redirect_url": redirect_url,
            "es_nuevo": es_nuevo
        }

    except Exception as e:
        conn.rollback()
        print(f"[DB ERROR] {e}")
        raise e
    finally:
        cursor.close()
        conn.close()

# 🛠️ HELPER: Obtener llaves públicas de Apple
def get_apple_public_key(kid):
    try:
        keys = requests.get("https://appleid.apple.com/auth/keys").json()['keys']
        for key in keys:
            if key['kid'] == kid:
                return RSAAlgorithm.from_jwk(json.dumps(key))
    except:
        pass
    return None

# ==========================================
# 📱 RUTA 1: APP MÓVIL (iOS - Flutter)
# ==========================================

class AppleLoginAppModel(BaseModel):
    identityToken: str
    email: Optional[str] = None
    fullName: Optional[str] = None
    tipo: str = "explorador"
    user_agent: Optional[str] = "App iOS"

@router.post("/api/auth/apple/ios")
async def login_apple_ios(data: AppleLoginAppModel):
    print("[APPLE iOS] Procesando login...")
    try:
        # Validar Token
        header = jwt.get_unverified_header(data.identityToken)
        public_key = get_apple_public_key(header['kid'])
        
        decoded = jwt.decode(data.identityToken, public_key, algorithms=['RS256'], audience=APPLE_BUNDLE_ID_IOS)
        
        apple_sub = decoded['sub']
        token_email = decoded.get('email')
        
        final_email = data.email if data.email else token_email
        final_name = data.fullName if data.fullName else "Usuario Apple"

        # Procesar
        result = process_unified_login(apple_sub, final_email, final_name, data.tipo, data.user_agent)
        
        return JSONResponse(content=result)

    except Exception as e:
        print(f"[ERROR iOS] {e}")
        raise HTTPException(status_code=400, detail=str(e))

# ==========================================
# 🌐 RUTA 2: WEB (Navegador)
# ==========================================

@router.get("/auth/apple/web/login")
async def login_apple_web_start(request: Request):
    """Inicia el flujo redirigiendo a Apple"""
    tipo = request.query_params.get("tipo", "emprendedor")
    redirect_uri = "https://prendiax.com/api/auth/apple/callback" 
    
    url = (
        f"https://appleid.apple.com/auth/authorize?"
        f"client_id={APPLE_CLIENT_ID_WEB}&"
        f"redirect_uri={redirect_uri}&"
        f"response_type=code id_token&"
        f"scope=name email&"
        f"response_mode=form_post&"
        f"state={tipo}"
    )
    return RedirectResponse(url)

@router.post("/api/auth/apple/callback")
async def login_apple_web_callback(request: Request):
    """Apple nos responde aquí con un POST"""
    try:
        form_data = await request.form()
        id_token_str = form_data.get('id_token')
        user_json = form_data.get('user')
        tipo_recuperado = form_data.get('state', 'emprendedor')
        
        print(f"[APPLE WEB] Callback recibido. Tipo: {tipo_recuperado}")
        
        if not id_token_str:
            return RedirectResponse("/login?error=no_token", status_code=303)

        # Validar Token
        header = jwt.get_unverified_header(id_token_str)
        public_key = get_apple_public_key(header['kid'])
        
        decoded = jwt.decode(id_token_str, public_key, algorithms=['RS256'], audience=APPLE_CLIENT_ID_WEB)
        
        apple_sub = decoded['sub']
        email = decoded.get('email')
        
        name = "Usuario Apple Web"
        if user_json:
            try:
                u = json.loads(user_json)
                name = f"{u.get('name', {}).get('firstName','')} {u.get('name', {}).get('lastName','')}".strip()
            except: pass

        # Ejecutamos la lógica central
        result = process_unified_login(apple_sub, email, name, tipo_recuperado, "Web Browser")
        
        # Guardamos sesión (Cookies para Web)
        request.session['user'] = {
            "id": result['user_id'],
            "email": result['email'],
            "tipo": tipo_recuperado
        }
        
        return RedirectResponse(result['redirect_url'], status_code=303)

    except Exception as e:
        print(f"[ERROR WEB] {e}")
        return RedirectResponse("/login?error=apple_callback_failed", status_code=303)