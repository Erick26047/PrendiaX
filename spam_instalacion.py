import psycopg2
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- CONFIGURACIÓN ---
DB_HOST = "localhost"
DB_NAME = "prendia_db"
DB_USER = "postgres"
DB_PASS = "Elbicho7" # <-- Corregido: esta es la de tu BD

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "prendiax@gmail.com" 
SMTP_PASSWORD = "ekux nkus emdm azcv" # <-- Corregido: esta es la de Google

ASUNTO = "¿Ya tienes PrendiaX en tu celular? 📱"
CUERPO_HTML = """
<h2>Lleva tu negocio al siguiente nivel con la App de PrendiaX</h2>
<p>Hola,</p>
<p>Notamos que te registraste en nuestra plataforma web, pero aún no disfrutas de la experiencia completa en tu dispositivo móvil.</p>
<p>Con la aplicación oficial podrás:</p>
<ul>
    <li>Recibir notificaciones en tiempo real de clientes interesados.</li>
    <li>Chatear directamente con otros emprendedores.</li>
    <li>Publicar fotos y videos de tus servicios al instante.</li>
</ul>
<br>
<p style="background-color: #1976d2; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; font-weight: bold; display: inline-block;">Búscanos en App Store y Google Play</p>
<br><br>
<p>No dejes pasar la oportunidad de conectar con tu comunidad local desde cualquier lugar.</p>
<p>Atentamente,<br><b>El equipo de PrendiaX</b></p>
"""

def enviar_spam_retencion():
    try:
        print("Conectando a la base de datos...")
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()

        # Solo selecciona a los que NO tienen la app (fcm_token nulo) y sí tienen correo
        cur.execute("SELECT email FROM usuarios WHERE fcm_token IS NULL AND email IS NOT NULL AND email != ''")
        usuarios_sin_app = cur.fetchall()
        cur.close()
        conn.close()

        # Extraemos solo los correos a una lista limpia
        lista_envios = [user[0] for user in usuarios_sin_app]

        # 🔥 AGREGAMOS TU CORREO SIEMPRE PARA PRUEBAS 🔥
        correo_prueba = "egutierez059@gmail.com"
        if correo_prueba not in lista_envios:
            lista_envios.append(correo_prueba)

        if not lista_envios:
            print("No hay correos para enviar.")
            return

        print(f"Enviando correo de instalación a {len(lista_envios)} usuarios...")

        # 🔥 CONEXIÓN A GMAIL OPTIMIZADA PARA EVITAR EL ERROR DE RED 🔥
        print(f"Conectando a {SMTP_SERVER}...")
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASSWORD)

        enviados = 0
        for email in lista_envios:
            try:
                msg = MIMEMultipart()
                msg['From'] = f"PrendiaX <{SMTP_USER}>"
                msg['To'] = email
                msg['Subject'] = ASUNTO
                msg.attach(MIMEText(CUERPO_HTML, 'html'))
                
                server.send_message(msg)
                enviados += 1
            except Exception as e:
                print(f"Error al enviar a {email}: {e}")

        server.quit()
        print(f"¡Campaña finalizada! Correos enviados con éxito: {enviados}")

    except Exception as e:
        print(f"Error crítico en el script: {e}")

if __name__ == "__main__":
    enviar_spam_retencion()