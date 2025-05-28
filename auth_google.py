from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from authlib.integrations.starlette_client import OAuth
import psycopg2

router = APIRouter()

# 🔧 Conexión a la base de datos
conn = psycopg2.connect(
    database="prendia_db",
    user="postgres",
    password="Elbicho7",  # ⚠️ Considera usar variables de entorno en producción
    host="localhost",
    port="5432"
)
cur = conn.cursor()

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

# 🟡 Mostrar login.html y guardar tipo/target
@router.get("/login", response_class=HTMLResponse)
async def show_login(request: Request):
    tipo = request.query_params.get("tipo", "emprendedor")  # Por defecto, emprendedor
    target = request.query_params.get("target", "perfil")
    request.session["tipo"] = tipo
    request.session["target"] = target
    print(f"[DEBUG] /login: tipo={tipo}, target={target}, sesión={request.session}")
    with open("login.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

# 🔁 Ruta de autenticación con Google
@router.get("/auth/google")
async def login_via_google(request: Request):
    tipo = request.query_params.get("tipo", request.session.get("tipo", "emprendedor"))
    target = request.query_params.get("target", request.session.get("target", "perfil"))
    redirect_uri = request.url_for("auth_google_callback")
    request.session["tipo"] = tipo
    request.session["target"] = target
    print(f"[DEBUG] /auth/google: tipo={tipo}, target={target}, sesión={request.session}")
    return await oauth.google.authorize_redirect(request, redirect_uri)

# ✅ Callback luego de iniciar sesión
@router.get("/auth/google/callback")
async def auth_google_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        user = await oauth.google.get("https://www.googleapis.com/oauth2/v3/userinfo", token=token)
        user_info = user.json()

        # 🔄 Insertar o actualizar usuario en PostgreSQL
        cur.execute("""
            INSERT INTO usuarios (nombre, email)
            VALUES (%s, %s)
            ON CONFLICT (email) DO UPDATE SET nombre = EXCLUDED.nombre;
        """, (user_info["name"], user_info["email"]))
        conn.commit()

        # 🔍 Obtener ID del usuario
        cur.execute("SELECT id FROM usuarios WHERE email = %s;", (user_info["email"],))
        user_id = cur.fetchone()[0]

        # 💾 Guardar datos de sesión
        tipo = request.session.get("tipo", "emprendedor")  # Por defecto, emprendedor
        print(f"[DEBUG] /auth/google/callback: tipo={tipo} para user_id={user_id}")
        request.session['user'] = {
            "id": user_id,
            "email": user_info["email"],
            "name": user_info["name"],
            "tipo": tipo
        }
        print(f"[DEBUG] /auth/google/callback: Sesión creada: {request.session}")

        # 🎯 Redirección según tipo
        if tipo == "explorador":
            print("[DEBUG] /auth/google/callback: Redirigiendo a /perfil-especifico por tipo=explorador")
            cur.execute("DELETE FROM datos_usuario WHERE user_id = %s;", (user_id,))
            conn.commit()
            return RedirectResponse(url="/perfil-especifico")

        # Para emprendedores, verificar si ya tienen datos
        cur.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s;", (user_id,))
        ya_tiene_datos = cur.fetchone()
        print(f"[DEBUG] /auth/google/callback: ¿Usuario tiene datos?: {ya_tiene_datos is not None}")

        if ya_tiene_datos:
            print("[DEBUG] /auth/google/callback: Redirigiendo a /perfil (datos existentes)")
            return RedirectResponse(url="/perfil")
        else:
            print("[DEBUG] /auth/google/callback: Redirigiendo a /dashboard (sin datos)")
            return RedirectResponse(url="/dashboard")

    except Exception as e:
        print(f"[ERROR] /auth/google/callback: Error en la autenticación: {e}")
        conn.rollback()
        return RedirectResponse(url="/login")

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

    if tipo == "explorador":
        print("[DEBUG] /perfil: Redirigiendo a /perfil-especifico por tipo=explorador")
        cur.execute("DELETE FROM datos_usuario WHERE user_id = %s;", (user_id,))
        conn.commit()
        return RedirectResponse(url="/perfil-especifico")

    # Para emprendedores, verificar si tienen datos
    cur.execute("SELECT 1 FROM datos_usuario WHERE user_id = %s;", (user_id,))
    ya_tiene_datos = cur.fetchone()
    print(f"[DEBUG] /perfil: ¿Usuario tiene datos?: {ya_tiene_datos is not None}")

    if ya_tiene_datos:
        # Servir el contenido de perfil.html directamente
        try:
            cur.execute("SELECT nombre, email FROM usuarios WHERE id = %s;", (user_id,))
            user_data = cur.fetchone()
            if user_data is None:
                print(f"[ERROR] /perfil: Usuario no encontrado: user_id={user_id}")
                return {"error": "Usuario no encontrado"}
            print(f"[DEBUG] /perfil: Mostrando perfil.html para user_id={user_id}, nombre={user_data[0]}")
            with open("perfil.html", "r", encoding="utf-8") as f:
                html = f.read().replace("{nombre}", user_data[0]).replace("{email}", user_data[1])
            return HTMLResponse(content=html)
        except Exception as e:
            print(f"[ERROR] /perfil: Error al cargar perfil.html: {e}")
            return {"error": "Error al cargar el perfil"}
    else:
        print("[DEBUG] /perfil: Redirigiendo a /dashboard (sin datos)")
        return RedirectResponse(url="/dashboard")

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
        return RedirectResponse(url="/login?tipo=explorador&target=perfil-especifico")
    user_id = request.session['user']['id']
    try:
        cur.execute("SELECT nombre, email FROM usuarios WHERE id = %s;", (user_id,))
        user_data = cur.fetchone()
        if user_data is None:
            print(f"[ERROR] /perfil-especifico: Usuario no encontrado: user_id={user_id}")
            return {"error": "Usuario no encontrado"}
        print(f"[DEBUG] /perfil-especifico: Mostrando datos para user_id={user_id}, nombre={user_data[0]}")
        with open("perfil_especifico.html", "r", encoding="utf-8") as f:
            html = f.read().replace("{nombre}", user_data[0]).replace("{email}", user_data[1])
        return HTMLResponse(content=html)
    except Exception as e:
        print(f"[ERROR] /perfil-especifico: Error al obtener datos del usuario: {e}")
        return {"error": "Error al obtener datos del usuario"}

# 🛠 Ruta para dashboard
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if "user" not in request.session:
        print("[DEBUG] /dashboard: No hay sesión, redirigiendo a /login")
        return RedirectResponse(url="/login?tipo=emprendedor&target=perfil")
    
    tipo = request.session["user"].get("tipo", "emprendedor")
    user_id = request.session["user"]["id"]
    print(f"[DEBUG] /dashboard: Tipo={tipo}, User ID={user_id}")

    if tipo == "explorador":
        print("[DEBUG] /dashboard: Acceso denegado para explorador, redirigiendo a /perfil-especifico")
        return RedirectResponse(url="/perfil-especifico")

    # Mostrar formulario para llenar datos_usuario
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
        return RedirectResponse(url="/login?tipo=emprendedor&target=perfil")
    
    tipo = request.session["user"].get("tipo", "emprendedor")
    user_id = request.session["user"]["id"]
    print(f"[DEBUG] /dashboard POST: Tipo={tipo}, User ID={user_id}")

    if tipo == "explorador":
        print("[DEBUG] /dashboard POST: Acceso denegado para explorador, redirigiendo a /perfil-especifico")
        return RedirectResponse(url="/perfil-especifico")

    # Procesar formulario
    try:
        form_data = await request.form()
        nombre_empresa = form_data.get("nombre_empresa")
        descripcion = form_data.get("descripcion")
        
        cur.execute("""
            INSERT INTO datos_usuario (user_id, nombre_empresa, descripcion)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                nombre_empresa = EXCLUDED.nombre_empresa,
                descripcion = EXCLUDED.descripcion;
        """, (user_id, nombre_empresa, descripcion))
        conn.commit()
        print(f"[DEBUG] /dashboard POST: Datos guardados para user_id={user_id}")
        return RedirectResponse(url="/perfil", status_code=303)
    except Exception as e:
        print(f"[ERROR] /dashboard POST: Error al guardar datos: {e}")
        conn.rollback()
        return {"error": "Error al guardar datos"}

# 🔚 Ruta para cerrar sesión
@router.post("/logout")
@router.get("/logout")
async def logout(request: Request):
    print(f"[DEBUG] /logout: Método={request.method}, Sesión antes de cerrar: {request.session}")
    request.session.clear()
    print("[DEBUG] /logout: Sesión cerrada")
    return RedirectResponse(url="/login?tipo=emprendedor&target=perfil", status_code=303)