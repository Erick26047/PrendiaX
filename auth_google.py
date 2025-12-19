from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.pydantic import BaseModel
from authlib.integrations.starlette_client import OAuth
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional
import requests

router = APIRouter()

# 🔧 Conexión a la base de datos
def get_db_connection():
    return psycopg2.connect(
        database="prendia_db",
        user="postgres",
        password="Elbicho7",  # ⚠️ Usar variables de entorno en producción
        host="localhost",
        port="5432",
        cursor_factory=RealDictCursor
    )

# 🔐 Configurar OAuth con Google
oauth = OAuth()
oauth.register(
    name='google',
    client_id='88827775174-dj7lv3km63vlm6nht817m5qv1utg466b.apps.googleusercontent.com',
    client_secret='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',
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile',
        'prompt': 'select_account'
    }
)

# 🟡 Mostrar login.html y guardar tipo/target sin forzar valores por defecto
@router.get("/login", response_class=HTMLResponse)
async def show_login(request: Request):
    tipo = request.query_params.get("tipo")  # No se fuerza "emprendedor"
    target = request.query_params.get("target")

    # Guardar solo si el usuario lo mandó explícitamente en la URL
    if tipo:
        request.session["tipo"] = tipo
    if target:
        request.session["target"] = target

    print(f"[DEBUG] /login: tipo={tipo}, target={target}, sesión={request.session}")

    with open("login.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


# 🔁 Ruta de autenticación con GoogleA
@router.get("/auth/google")
async def login_via_google(request: Request):
    tipo = request.query_params.get("tipo", request.session.get("tipo", "explorador"))
    target = request.query_params.get("target", request.session.get("target", "perfil"))
    redirect_uri = request.url_for("auth_google_callback")
    request.session["tipo"] = tipo
    request.session["target"] = target
    print(f"[DEBUG] /auth/google: tipo={tipo}, target={target}, redirect_uri={redirect_uri}")
    return await oauth.google.authorize_redirect(request, redirect_uri)

# ✅ Callback luego de iniciar sesión
@router.get("/auth/google/callback")
async def auth_google_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        user = await oauth.google.get("https://www.googleapis.com/oauth2/v3/userinfo", token=token)
        user_info = user.json()

        email = user_info.get("email")
        name = user_info.get("name") or "Usuario"

        if not email:
            print("[ERROR] /auth/google/callback: No se proporcionó un correo electrónico")
            return RedirectResponse(url="/login?tipo=emprendedor&target=perfil", status_code=302)

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # Verificar si el email ya existe
            cursor.execute("SELECT id FROM usuarios WHERE email = %s", (email,))
            user = cursor.fetchone()

            if user:
                # Email ya registrado, usar el id existente
                user_id = user["id"]
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

            conn.commit()
            # Guardar datos de sesión
            tipo = request.session.get("tipo", "emprendedor")
            request.session['user'] = {
                "id": user_id,
                "email": email,
                "name": name,
                "tipo": tipo
            }
            print(f"[DEBUG] /auth/google/callback: Sesión creada: {request.session}")

            # 🎯 Redirección según tipo
            if tipo == "explorador":
                print("[DEBUG] /auth/google/callback: Redirigiendo a /perfil-especifico por tipo=explorador")
                cursor.execute("DELETE FROM datos_usuario WHERE user_id = %s;", (user_id,))
                conn.commit()
                return RedirectResponse(url="/perfil-especifico", status_code=302)

            # Para emprendedores, verificar si ya tienen datos
            cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s;", (user_id,))
            ya_tiene_datos = cursor.fetchone()
            print(f"[DEBUG] /auth/google/callback: ¿Usuario tiene datos?: {ya_tiene_datos is not None}")

            if ya_tiene_datos:
                print("[DEBUG] /auth/google/callback: Redirigiendo a /perfil (datos existentes)")
                return RedirectResponse(url="/perfil", status_code=302)
            else:
                print("[DEBUG] /auth/google/callback: Redirigiendo a /dashboard (sin datos)")
                return RedirectResponse(url="/dashboard", status_code=302)

        except Exception as e:
            conn.rollback()
            print(f"[ERROR] /auth/google/callback: Error en la base de datos: {e}")
            raise HTTPException(status_code=500, detail=f"Error en la base de datos: {str(e)}")
        finally:
            cursor.close()
            conn.close()

    except Exception as e:
        print(f"[ERROR] /auth/google/callback: Error en la autenticación: {e}")
        return RedirectResponse(url="/login?tipo=emprendedor&target=perfil", status_code=302)

# ✅ Ruta protegida para perfil
@router.get("/perfil", response_class=HTMLResponse)
async def redireccionar_a_perfil(request: Request):
    print(f"[DEBUG] /perfil: Sesión completa: {request.session}")
    if "user" not in request.session:
        tipo = request.query_params.get("tipo", "emprendedor")
        target = "perfil-especifico" if tipo == "explorador" else "perfil"
        print(f"[DEBUG] /perfil: No hay sesión, redirigiendo a /login?tipo={tipo}&target={target}")
        return RedirectResponse(url=f"/login?tipo={tipo}&target={target}")

    tipo = request.session["user"].get("tipo", "emprendedor")
    user_id = request.session["user"]["id"]
    print(f"[DEBUG] /perfil: Tipo={tipo}, User ID={user_id}")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if tipo == "explorador":
            print("[DEBUG] /perfil: Redirigiendo a /perfil-especifico por tipo=explorador")
            cursor.execute("DELETE FROM datos_usuario WHERE user_id = %s;", (user_id,))
            conn.commit()
            return RedirectResponse(url="/perfil-especifico", status_code=302)

        # Para emprendedores, verificar si tienen datos
        cursor.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s;", (user_id,))
        ya_tiene_datos = cursor.fetchone()
        print(f"[DEBUG] /perfil: ¿Usuario tiene datos?: {ya_tiene_datos is not None}")

        if ya_tiene_datos:
            # Servir el contenido de perfil.html
            cursor.execute("SELECT nombre, email FROM usuarios WHERE id = %s;", (user_id,))
            user_data = cursor.fetchone()
            if user_data is None:
                print(f"[ERROR] /perfil: Usuario no encontrado: user_id={user_id}")
                return {"error": "Usuario no encontrado"}
            print(f"[DEBUG] /perfil: Mostrando perfil.html para user_id={user_id}, nombre={user_data['nombre']}")
            with open("perfil.html", "r", encoding="utf-8") as f:
                html = f.read().replace("{nombre}", user_data["nombre"]).replace("{email}", user_data["email"])
            return HTMLResponse(content=html)
        else:
            print("[DEBUG] /perfil: Redirigiendo a /dashboard (sin datos)")
            return RedirectResponse(url="/dashboard", status_code=302)
    except Exception as e:
        print(f"[ERROR] /perfil: Error al cargar perfil.html: {e}")
        return {"error": "Error al cargar el perfil"}
    finally:
        cursor.close()
        conn.close()

# 🔍 Obtener información del usuario actual
@router.get("/current_user")
async def get_current_user(request: Request):
    if "user" not in request.session:
        print("[DEBUG] /current_user: No hay sesión activa")
        return {"user_id": None, "tipo": ""}
    print(f"[DEBUG] /current_user: user_id={request.session['user']['id']}, tipo={request.session['user'].get('tipo', '')}")
    return {
        "user_id": request.session["user"]["id"],
        "tipo": request.session["user"].get("tipo", "")
    }

# 🔎 Ruta para perfil específico
@router.get("/perfil-especifico", response_class=HTMLResponse)
async def perfil_especifico(request: Request):
    if 'user' not in request.session:
        print("[DEBUG] /perfil-especifico: No hay sesión, redirigiendo a login")
        return RedirectResponse(url="/login?tipo=explorador&target=perfil-especifico", status_code=302)
    user_id = request.session['user']['id']
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT nombre, email FROM usuarios WHERE id = %s;", (user_id,))
        user_data = cursor.fetchone()
        if user_data is None:
            print(f"[ERROR] /perfil-especifico: Usuario no encontrado: user_id={user_id}")
            return {"error": "Usuario no encontrado"}
        print(f"[DEBUG] /perfil-especifico: Mostrando datos para user_id={user_id}, nombre={user_data['nombre']}")
        with open("perfil-especifico.html", "r", encoding="utf-8") as f:
            html = f.read().replace("{nombre}", user_data["nombre"]).replace("{email}", user_data["email"])
        return HTMLResponse(content=html)
    except Exception as e:
        print(f"[ERROR] /perfil-especifico: Error al obtener datos del usuario: {e}")
        return {"error": "Error al obtener datos del usuario"}
    finally:
        cursor.close()
        conn.close()

# 🛠 Ruta para dashboard
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if "user" not in request.session:
        print("[DEBUG] /dashboard: No hay sesión, redirigiendo a /login")
        return RedirectResponse(url="/login?tipo=emprendedor&target=perfil", status_code=302)
    
    tipo = request.session["user"].get("tipo", "emprendedor")
    user_id = request.session["user"]["id"]
    print(f"[DEBUG] /dashboard: Tipo={tipo}, User ID={user_id}")

    if tipo == "explorador":
        print("[DEBUG] /dashboard: Acceso denegado para explorador, redirigiendo a /perfil-especifico")
        return RedirectResponse(url="/perfil-especifico", status_code=302)

    try:
        with open("dashboard.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        print(f"[ERROR] /dashboard: Error al cargar dashboard.html: {e}")
        return {"error": "Error al cargar el dashboard"}

# 🛠 Ruta para guardar datos del dashboard
@router.post("/dashboard")
async def save_dashboard(request: Request):
    if "user" not in request.session:
        print("[DEBUG] /dashboard POST: No hay sesión, redirigiendo a /login")
        return RedirectResponse(url="/login?tipo=emprendedor&target=perfil", status_code=302)
    
    tipo = request.session["user"].get("tipo", "emprendedor")
    user_id = request.session["user"]["id"]
    print(f"[DEBUG] /dashboard POST: Tipo={tipo}, User ID={user_id}")

    if tipo == "explorador":
        print("[DEBUG] /dashboard POST: Acceso denegado para explorador, redirigiendo a /perfil-especifico")
        return RedirectResponse(url="/perfil-especifico", status_code=302)

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
        print(f"[DEBUG] /dashboard POST: Datos guardados para user_id={user_id}")
        return RedirectResponse(url="/perfil", status_code=303)
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] /dashboard POST: Error al guardar datos: {e}")
        return {"error": "Error al guardar datos"}
    finally:
        cursor.close()
        conn.close()

# EN TU BACKEND PYTHON (auth_google.py)

class GoogleLoginApp(BaseModel):
    id_token: str
    tipo: str = "explorador"
    target: str = "perfil"
    user_agent: Optional[str] = None

@router.post("/api/auth/google") # <--- OJO CON LA RUTA /api/
async def google_login_app(data: GoogleLoginApp):
    try:
        # 1. Validar el token con Google
        idinfo = id_token.verify_oauth2_token(data.id_token, requests.Request(), "TU_CLIENT_ID_DE_GOOGLE")

        email = idinfo['email']
        name = idinfo.get('name', 'Usuario Google')
        
        # 2. Lógica de Base de Datos (Buscar o Crear usuario)
        # ... (Tu lógica para buscar usuario por email) ...
        # ... Si no existe, lo creas ...

        # 3. Generar respuesta JSON para Flutter
        # return {
        #    "token": "jwt_app_...",
        #    "user_id": user_id,
        #    "redirect_url": "/perfil..."
        # }
        
        # SI NECESITAS EL CÓDIGO PYTHON COMPLETO DE ESTE ENDPOINT, AVÍSAME.
        pass 
    except ValueError:
        raise HTTPException(status_code=400, detail="Token de Google inválido")


# 🔚 Ruta para cerrar sesión
@router.post("/logout")
@router.get("/logout")
async def logout(request: Request):
    print(f"[DEBUG] /logout: Método={request.method}, Sesión antes de cerrar: {request.session}")
    
    # Detectar tipo antes de limpiar la sesión
    tipo_actual = request.session.get("tipo", "")
    target = request.session.get("target", "perfil")

    # Borrar la sesión
    request.session.clear()
    print("[DEBUG] /logout: Sesión cerrada")

    # Redirigir según el tipo que tenía antes de cerrar
    if tipo_actual == "explorador":
        return RedirectResponse(url="/login?tipo=explorador&target=perfil-especifico", status_code=303)
    else:
        return RedirectResponse(url="/login?tipo=emprendedor&target=perfil", status_code=303)
