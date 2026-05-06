import psycopg2
import firebase_admin
from firebase_admin import credentials, messaging
import sys
import random

# --- CONFIGURACIÓN DE BASE DE DATOS ---
DB_HOST = "localhost"
DB_NAME = "prendia_db"
DB_USER = "postgres"
DB_PASS = "Elbicho7"

# 🔥 OPCIONES DE MENSAJES (Se elige uno al azar o puedes forzar uno) 🔥
MENSAJES = [
    {
        "titulo": "¡Inicia la semana con ventas! 🚀",
        "cuerpo": "Publica hoy tus servicios o productos en PrendiaX y llega a más clientes cerca de ti."
    },
    {
        "titulo": "¿Qué ofreces hoy? 📸",
        "cuerpo": "Mantente visible en tu zona. Sube una foto o video de tu negocio y atrae nuevos clientes."
    },
    {
        "titulo": "¡Prepárate para el fin de semana! 🔥",
        "cuerpo": "Aprovecha el tráfico de hoy. Promociona tu talento local en PrendiaX totalmente gratis."
    },
    {
        "titulo": "Tu próximo cliente te está buscando 🔍",
        "cuerpo": "Actualiza tu catálogo. Un perfil con fotos recientes genera 80% más confianza."
    }
]

def enviar_recordatorios():
    # Inicializar Firebase solo si no está inicializado (Ajusta la ruta de tu JSON de Firebase)
    if not firebase_admin._apps:
        try:
            cred = credentials.Certificate("ruta/a/tu/archivo-firebase-adminsdk.json") # <- REVISA ESTA RUTA
            firebase_admin.initialize_app(cred)
        except Exception as e:
            print(f"Error inicializando Firebase: {e}")
            return

    try:
        print("Conectando a la base de datos...")
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()

        # Seleccionar solo a los que tienen la app instalada
        cur.execute("SELECT id, fcm_token FROM usuarios WHERE fcm_token IS NOT NULL")
        usuarios_app = cur.fetchall()
        cur.close()
        conn.close()

        if not usuarios_app:
            print("No hay usuarios con la app instalada para enviar Push.")
            return

        # Elegir un mensaje al azar de la lista
        mensaje_elegido = random.choice(MENSAJES)
        
        print(f"Enviando Push a {len(usuarios_app)} usuarios...")
        print(f"📢 Título: {mensaje_elegido['titulo']}")
        print(f"💬 Cuerpo: {mensaje_elegido['cuerpo']}")

        enviados = 0
        for user in usuarios_app:
            user_id, fcm_token = user
            try:
                push_msg = messaging.Message(
                    notification=messaging.Notification(
                        title=mensaje_elegido['titulo'], 
                        body=mensaje_elegido['cuerpo']
                    ), 
                    apns=messaging.APNSConfig(
                        payload=messaging.APNSPayload(aps=messaging.Aps(sound="default"))
                    ),
                    data={"tipo": "recordatorio"}, 
                    token=fcm_token
                )
                messaging.send(push_msg)
                enviados += 1
            except Exception as e:
                print(f"Error al enviar Push a usuario {user_id}: {e}")

        print(f"¡Campaña finalizada! Notificaciones enviadas con éxito: {enviados}")

    except Exception as e:
        print(f"Error crítico en el script: {e}")

if __name__ == "__main__":
    enviar_recordatorios()