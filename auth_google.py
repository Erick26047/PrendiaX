from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel  # <--- ESTO ES LO CORRECTO import BaseModel
from authlib.integrations.starlette_client import OAuth
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional
import os

router = APIRouter()

# ==========================================
#  CONFIGURACIÓN Y BBDD
# ==========================================

# 🔧 Conexión a la base de datos
def get_db_connection():
    return psycopg2.connect(
        database="prendia_db",
        user="postgres",
        password="Elbicho7", 
        host="localhost",
        port="5432",
        cursor_factory=RealDictCursor
    )

# 🔐 Configurar OAuth con Google (PARA FLUJO WEB)
oauth = OAuth()
oauth.register(
    name='google',
    client_id='88827775174-dj7lv3km63vlm6nht817m5qv1utg466b.apps.googleusercontent.com', # ID WEB
    client_secret='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',
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile',
        'prompt': 'select_account'
    }
)

# 📋 LISTA DE CLIENT IDs PERMITIDOS (Para validar el token de la App)
ALLOWED_CLIENT_IDS = [
    "88827775174-dj7lv3km63vlm6nht817m5qv1utg466b.apps.googleusercontent.com", # WEB Client ID
    "88827775174-s3dv0u8mbcdcdrbb8vkmdcv7sagl804n.apps.googleusercontent.com", # iOS Client ID (El nuevo)
    # "TU_ANDROID_CLIENT_ID.apps.googleusercontent.com" # Futuro Android ID
]

# ==========================================
#  RUTAS PARA LA APP (FLUTTER) - JSON
# ==========================================

class GoogleLoginApp(BaseModel):
    id_token: str
    tipo: str = "explorador"
    target: str = "perfil"
    user_agent: Optional[str] = None

@router.post("/api/auth/google")
async def google_login_app(data: GoogleLoginApp):
    print(f"[GOOGLE APP] Recibiendo login desde App...")
    
    try:
        # 1. Validar el token con Google
        # Usamos GoogleRequest() y audience=None para verificar manualmente contra nuestra lista
        idinfo = id_token.verify_oauth2_token(
            data.id_token, 
            GoogleRequest(), 
            audience=None 
        )

        # 2. Verificar que el token venga de NUESTRA app (iOS o Web)
        if idinfo['aud'] not in ALLOWED_CLIENT_IDS:
            print(f"[ERROR] Cliente no autorizado (Audience mismatch): {idinfo['aud']}")
            # Si estás probando y te da error aquí, comenta la siguiente línea temporalmente:
            # raise HTTPException(status_code=401, detail="Cliente inválido")

        email = idinfo['email']
        name = idinfo.get('name', 'Usuario Google')
        picture = idinfo.get('picture', '')

        print(f"[GOOGLE APP] Usuario verificado: {email}")

        # 3. Lógica de Base de Datos
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            # Buscar usuario existente
            cursor.execute("SELECT id, nombre, email FROM usuarios WHERE email = %s", (email,))
            user = cursor.fetchone()
            
            es_nuevo = False
            if not user:
                print("[GOOGLE APP] Creando usuario nuevo...")
                cursor.execute(
                    """
                    INSERT INTO usuarios (nombre, email, foto_perfil_url, verified, created_at, user_agent)
                    VALUES (%s, %s, %s, TRUE, NOW(), %s)
                    RETURNING id
                    """,
                    (name, email, picture, data.user_agent or "App")
                )
                user_id = cursor.fetchone()['id']
                conn.commit()
                es_nuevo = True
            else:
                print(f"[GOOGLE APP] Usuario existente ID: {user['id']}")
                user_id = user['id']
                # Opcional: Actualizar foto o marcar verificado si no lo estaba
                cursor.execute("UPDATE usuarios SET verified = TRUE WHERE id = %s", (user_id,))
                conn.commit()

            # 4. Determinar Redirección (Dashboard o Perfil)
            redirect_url = "/dashboard"
            
            if data.tipo == "explorador":
                redirect_url = "/perfil-especifico"
                # Opcional: Limpiar datos de negocio si entra como explorador
                # cursor.execute("DELETE FROM datos_usuario WHERE user_id = %s", (user_id,))
                # conn.commit()
            else:
                # Si es emprendedor, checar si ya tiene datos
                cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s", (user_id,))
                if cursor.fetchone():
                    redirect_url = "/perfil"
                else:
                    redirect_url = "/dashboard"

            # 5. Generar Token falso (Para tu lógica actual)
            # En producción usarías jwt.encode(...)
            fake_token = f"jwt_app_{user_id}"

            return JSONResponse(content={
                "status": "ok",
                "token": fake_token,
                "user_id": user_id,
                "email": email,
                "name": name,
                "tipo": data.tipo,
                "redirect_url": redirect_url,
                "es_nuevo": es_nuevo
            })

        finally:
            cursor.close()
            conn.close()

    except ValueError as e:
        print(f"[GOOGLE ERROR] Token inválido: {e}")
        raise HTTPException(status_code=400, detail="Token de Google inválido o caducado")
    except Exception as e:
        print(f"[SERVER ERROR] {e}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")


# ==========================================
#  RUTAS WEB (LOGIN TRADICIONAL HTML)
# ==========================================

@router.get("/login", response_class=HTMLResponse)
async def show_login(request: Request):
    tipo = request.query_params.get("tipo")
    target = request.query_params.get("target")

    if tipo:
        request.session["tipo"] = tipo
    if target:
        request.session["target"] = target

    try:
        with open("login.html", "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>Error: login.html no encontrado</h1>", status_code=500)

@router.get("/auth/google")
async def login_via_google(request: Request):
    tipo = request.query_params.get("tipo", request.session.get("tipo", "explorador"))
    target = request.query_params.get("target", request.session.get("target", "perfil"))
    redirect_uri = request.url_for("auth_google_callback")
    request.session["tipo"] = tipo
    request.session["target"] = target
    return await oauth.google.authorize_redirect(request, redirect_uri)

@router.get("/auth/google/callback")
async def auth_google_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        user = await oauth.google.get("https://www.googleapis.com/oauth2/v3/userinfo", token=token)
        user_info = user.json()

        email = user_info.get("email")
        name = user_info.get("name") or "Usuario"

        if not email:
            return RedirectResponse(url="/login?error=no_email", status_code=302)

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # Verificar usuario
            cursor.execute("SELECT id FROM usuarios WHERE email = %s", (email,))
            user_db = cursor.fetchone()

            if user_db:
                user_id = user_db["id"]
                # Actualizar nombre si es necesario
                cursor.execute("UPDATE usuarios SET nombre = %s WHERE id = %s", (name, user_id))
            else:
                cursor.execute(
                    "INSERT INTO usuarios (nombre, email, verified, created_at) VALUES (%s, %s, TRUE, NOW()) RETURNING id",
                    (name, email)
                )
                user_id = cursor.fetchone()["id"]

            conn.commit()

            # Guardar en Sesión Web
            tipo = request.session.get("tipo", "emprendedor")
            request.session['user'] = {
                "id": user_id,
                "email": email,
                "name": name,
                "tipo": tipo
            }

            # Redirección Web
            if tipo == "explorador":
                cursor.execute("DELETE FROM datos_usuario WHERE user_id = %s;", (user_id,))
                conn.commit()
                return RedirectResponse(url="/perfil-especifico", status_code=302)

            cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s;", (user_id,))
            if cursor.fetchone():
                return RedirectResponse(url="/perfil", status_code=302)
            else:
                return RedirectResponse(url="/dashboard", status_code=302)

        finally:
            cursor.close()
            conn.close()

    except Exception as e:
        print(f"[ERROR WEB] Google Callback: {e}")
        return RedirectResponse(url="/login?error=auth_failed", status_code=302)

# ==========================================
#  RUTAS PROTEGIDAS / PERFIL / DASHBOARD (WEB)
# ==========================================

@router.get("/perfil", response_class=HTMLResponse)
async def redireccionar_a_perfil(request: Request):
    if "user" not in request.session:
        tipo = request.query_params.get("tipo", "emprendedor")
        target = "perfil-especifico" if tipo == "explorador" else "perfil"
        return RedirectResponse(url=f"/login?tipo={tipo}&target={target}")

    user_id = request.session["user"]["id"]
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT nombre, email FROM usuarios WHERE id = %s;", (user_id,))
        user_data = cursor.fetchone()
        
        if user_data:
            with open("perfil.html", "r", encoding="utf-8") as f:
                html = f.read().replace("{nombre}", user_data["nombre"]).replace("{email}", user_data["email"])
            return HTMLResponse(content=html)
        return RedirectResponse(url="/dashboard", status_code=302)
    except Exception:
        return RedirectResponse(url="/dashboard", status_code=302)
    finally:
        cursor.close()
        conn.close()

@router.get("/current_user")
async def get_current_user(request: Request):
    if "user" not in request.session:
        return {"user_id": None, "tipo": ""}
    return {
        "user_id": request.session["user"]["id"],
        "tipo": request.session["user"].get("tipo", "")
    }

@router.get("/perfil-especifico", response_class=HTMLResponse)
async def perfil_especifico(request: Request):
    if 'user' not in request.session:
        return RedirectResponse(url="/login?tipo=explorador&target=perfil-especifico", status_code=302)
    
    user_id = request.session['user']['id']
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT nombre, email FROM usuarios WHERE id = %s;", (user_id,))
        user_data = cursor.fetchone()
        if user_data:
            with open("perfil-especifico.html", "r", encoding="utf-8") as f:
                html = f.read().replace("{nombre}", user_data["nombre"]).replace("{email}", user_data["email"])
            return HTMLResponse(content=html)
        return RedirectResponse(url="/login", status_code=302)
    finally:
        cursor.close()
        conn.close()

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if "user" not in request.session:
        return RedirectResponse(url="/login?tipo=emprendedor&target=perfil", status_code=302)
    try:
        with open("dashboard.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except:
        return HTMLResponse("Error loading dashboard", status_code=500)

@router.post("/dashboard")
async def save_dashboard(request: Request):
    if "user" not in request.session:
        return RedirectResponse(url="/login", status_code=302)
    
    user_id = request.session["user"]["id"]
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        form_data = await request.form()
        nombre_empresa = form_data.get("nombre_empresa")
        descripcion = form_data.get("descripcion")
        
        cursor.execute(
            """
            INSERT INTO datos_usuario (user_id, nombre_empresa, descripcion)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                nombre_empresa = EXCLUDED.nombre_empresa,
                descripcion = EXCLUDED.descripcion;
            """,
            (user_id, nombre_empresa, descripcion)
        )
        conn.commit()
        return RedirectResponse(url="/perfil", status_code=303)
    except:
        conn.rollback()
        return HTMLResponse("Error guardando datos", status_code=500)
    finally:
        cursor.close()
        conn.close()

@router.get("/logout")
@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)