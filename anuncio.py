import psycopg2
import firebase_admin
from firebase_admin import messaging

# Inicializamos Firebase (Este script corre independiente a tu app, por lo que necesita inicializarse)
try:
    firebase_admin.initialize_app()
except ValueError:
    pass

def enviar_anuncio_masivo():
    print("\n" + "="*50)
    print("📢 MÁQUINA DE ANUNCIOS MASIVOS PRENDIAX 📢")
    print("="*50 + "\n")
    
    # 1. El script te pregunta qué quieres enviar
    titulo = input("👉 Ingresa el TÍTULO de la notificación: ")
    cuerpo = input("👉 Ingresa el MENSAJE: ")
    
    # 2. Confirmación anti-dedazos
    print(f"\nVista previa de tu mensaje:\n- Título: {titulo}\n- Mensaje: {cuerpo}\n")
    confirmacion = input("¿Seguro que quieres disparar esto a TODOS los usuarios? (s/n): ")
    
    if confirmacion.lower() != 's':
        print("\n❌ Misión abortada. No se envió nada.")
        return

    conn = None
    try:
        # Nos conectamos a la base de datos
        conn = psycopg2.connect(
            host="localhost",
            database="prendia_db",
            user="postgres",
            password="Elbicho7"
        )
        cur = conn.cursor()
        
        # Sacamos a TODOS los usuarios con la app instalada
        cur.execute("SELECT id, fcm_token FROM usuarios WHERE fcm_token IS NOT NULL")
        usuarios = cur.fetchall()
        
        # Extraemos solo los tokens
        tokens = [u[1] for u in usuarios if u[1]]
        
        if not tokens:
            print("\n⚠️ No hay usuarios con tokens registrados para enviar notificaciones.")
            return

        print(f"\n🚀 Preparando misiles para {len(tokens)} usuarios...")

        # Firebase solo permite mandar de 500 en 500, así que armamos los lotes
        lotes = [tokens[i:i + 500] for i in range(0, len(tokens), 500)]
        
        exitos = 0
        fallas = 0

        for lote in lotes:
            message = messaging.MulticastMessage(
                notification=messaging.Notification(
                    title=titulo,
                    body=cuerpo
                ),
                data={
                    "tipo": "general" # Esto le dice a Flutter que es un anuncio global
                },
                tokens=lote,
            )
            # ¡Fuego! Disparamos a los celulares
            response = messaging.send_each_for_multicast(message)
            exitos += response.success_count
            fallas += response.failure_count

        print("\n========================================")
        print("✅ ¡BOMBA SOLTADA CON ÉXITO!")
        print(f"📱 Entregado en: {exitos} celulares")
        print(f"❌ Falló en: {fallas} celulares (app desinstalada o notificaciones apagadas)")
        print("========================================\n")

    except Exception as e:
        print(f"\n💥 Error catastrófico: {e}")
    finally:
        if conn:
            cur.close()
            conn.close()

if __name__ == "__main__":
    enviar_anuncio_masivo()