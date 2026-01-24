# apple_auth.py
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

router = APIRouter()

# ==========================================
# 🔴 DATOS DE APPLE (LLENAR CON CUIDADO)
# ==========================================
APPLE_TEAM_ID = "ZRTLHL9GXR"             # Ej: 3K92AAAAAA
APPLE_KEY_ID = "YFNS7NW42N"               # Ej: 4X88BBBBBB
APPLE_CLIENT_ID_WEB = "com.prendiax.web.service"     # El Service ID de la Web
APPLE_BUNDLE_ID_IOS = "com.prendiax.web" # El Bundle ID de Xcode
APPLE_PRIVATE_KEY_FILE = "AuthKey_YFNS7NW42N.p8" # Nombre de tu archivo .p8

# 🔴 CONFIGURACIÓN DB
DB_CONFIG = {
    "database": "prendia_db",
    "user": "postgres",
    "password": "Elbicho7",
    "host": "localhost",
    "port": "5432"
}

# ==========================================
# 🧠 LÓGICA DE FUSIÓN (EL CEREBRO)
# ==========================================

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)

def process_unified_login(apple_sub, email, name, tipo, user_agent):
    """
    Esta función decide si crea usuario, lo loguea o fusiona cuentas.
    Maneja la lógica de redirección Explorador vs Emprendedor.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        user_id = None
        es_nuevo = False
        
        # 1. ¿Existe por ID de Apple? (Usuario recurrente)
        cursor.execute("SELECT id, email FROM usuarios WHERE apple_sub = %s", (apple_sub,))
        user = cursor.fetchone()

        if user:
            print(f"[DB] Usuario Apple recurrente encontrado: ID {user['id']}")
            user_id = user['id']
            # Actualizar email si Apple nos mandó uno y en la BD faltaba
            if not user['email'] and email:
                cursor.execute("UPDATE usuarios SET email = %s WHERE id = %s", (email, user_id))
                conn.commit()

        else:
            # 2. No existe por ID Apple. ¿Existe por Email? (Fusión con Google/Manual)
            if email:
                cursor.execute("SELECT id FROM usuarios WHERE email = %s", (email,))
                user_email = cursor.fetchone()
                
                if user_email:
                    print(f"[DB] FUSIONANDO CUENTAS: El email {email} ya existía. Vinculando Apple ID.")
                    user_id = user_email['id']
                    # AQUÍ UNIMOS LAS CUENTAS
                    cursor.execute("UPDATE usuarios SET apple_sub = %s WHERE id = %s", (apple_sub, user_id))
                    conn.commit()
                else:
                    # 3. Usuario Totalmente Nuevo
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
                # 4. Usuario Nuevo PRIVADO (Sin email)
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

        # === LÓGICA DE REDIRECCIÓN (Explorador vs Emprendedor) ===
        redirect_url = "/dashboard" # Default
        
        if tipo == "explorador":
            # Si quiere ser explorador, lo mandamos al perfil público/feed
            redirect_url = "/perfil-especifico"
            # Opcional: Podrías limpiar datos de empresa si quisieras ser estricto
        else:
            # Si quiere ser emprendedor, verificamos si ya tiene datos
            cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s", (user_id,))
            if cursor.fetchone():
                redirect_url = "/perfil" # Ya tiene negocio, va a su perfil
            else:
                redirect_url = "/dashboard" # No tiene negocio, va a crearlo

        # Generar Token Simulado (Reemplazar con JWT real en prod)
        fake_token = f"jwt_apple_{user_id}"

        return {
            "status": "ok",
            "token": fake_token,
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
# 📱 RUTA 1: APP MÓVIL (iOS/Flutter)
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
        # 1. Validar Token contra Apple
        header = jwt.get_unverified_header(data.identityToken)
        public_key = get_apple_public_key(header['kid'])
        
        # Validamos que el token sea para NUESTRA APP (Bundle ID)
        decoded = jwt.decode(data.identityToken, public_key, algorithms=['RS256'], audience=APPLE_BUNDLE_ID_IOS)
        
        apple_sub = decoded['sub']
        token_email = decoded.get('email')
        
        # Priorizamos el email explícito que manda Flutter la primera vez
        final_email = data.email if data.email else token_email
        final_name = data.fullName if data.fullName else "Usuario Apple"

        # 2. Ejecutar Lógica Unificada
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
    # Guardamos el tipo de usuario en la cookie de sesión para recordarlo al volver
    tipo = request.query_params.get("tipo", "emprendedor")
    request.session["tipo"] = tipo
    
    redirect_uri = "https://prendiax.com/api/auth/apple/callback" # Debe ser exacta
    
    # URL oficial de Apple Auth
    url = (
        f"https://appleid.apple.com/auth/authorize?"
        f"client_id={APPLE_CLIENT_ID_WEB}&"
        f"redirect_uri={redirect_uri}&"
        f"response_type=code id_token&"
        f"scope=name email&"
        f"response_mode=form_post"
    )
    return RedirectResponse(url)

@router.post("/api/auth/apple/callback")
async def login_apple_web_callback(request: Request):
    """Apple nos responde aquí con un POST"""
    try:
        form_data = await request.form()
        id_token_str = form_data.get('id_token')
        user_json = form_data.get('user') # Solo viene la primera vez
        
        if not id_token_str:
            return RedirectResponse("/login?error=no_token", status_code=303)

        # Validar Token
        header = jwt.get_unverified_header(id_token_str)
        public_key = get_apple_public_key(header['kid'])
        
        # OJO: Aquí validamos contra el SERVICE ID WEB
        decoded = jwt.decode(id_token_str, public_key, algorithms=['RS256'], audience=APPLE_CLIENT_ID_WEB)
        
        apple_sub = decoded['sub']
        email = decoded.get('email')
        
        # Intentar sacar nombre
        name = "Usuario Apple Web"
        if user_json:
            try:
                u = json.loads(user_json)
                name = f"{u.get('name', {}).get('firstName','')} {u.get('name', {}).get('lastName','')}".strip()
            except: pass

        # Recuperar tipo de usuario de la sesión
        tipo = request.session.get("tipo", "emprendedor")

        # Ejecutar Lógica Unificada
        result = process_unified_login(apple_sub, email, name, tipo, "Web Browser")
        
        # Guardar en Sesión Web
        request.session['user'] = {
            "id": result['user_id'],
            "email": result['email'],
            "tipo": tipo
        }
        
        # Redirigir según la lógica
        return RedirectResponse(result['redirect_url'], status_code=303)

    except Exception as e:
        print(f"[ERROR WEB] {e}")
        return RedirectResponse("/login?error=apple_callback_failed", status_code=303)