import psycopg2
import os
import uuid

def get_db_connection():
    return psycopg2.connect(
        host="localhost",
        database="prendia_db",
        user="postgres",
        password="Elbicho7"
    )

def migrar_chats():
    print("🚀 ¡Arre! Iniciando migración de Archivos de Chat...")
    
    # Aseguramos que la carpeta exista
    os.makedirs(os.path.join("media", "chats"), exist_ok=True)
    
    conn = get_db_connection()
    cur = conn.cursor()

    print("1️⃣ Preparando la base de datos...")
    cur.execute("ALTER TABLE mensajes_chat ADD COLUMN IF NOT EXISTS ruta_media TEXT;")
    conn.commit()

    print("2️⃣ Extrayendo archivos pesados (fotos, videos, audios, docs)...")
    cur.execute("SELECT id, chat_id, tipo, media_content, contenido FROM mensajes_chat WHERE media_content IS NOT NULL")
    mensajes = cur.fetchall()
    
    if not mensajes:
        print("✨ No hay archivos atrapados. ¡Todo está limpio!")
        
    for msg in mensajes:
        msg_id, chat_id, tipo, media_content, contenido = msg
        
        # Obtener los bytes reales
        file_bytes = media_content.tobytes() if hasattr(media_content, 'tobytes') else bytes(media_content)
        
        # Filtro inteligente: Si el tamaño es muy pequeño y empieza con "media/", es una ruta disfrazada de nuestros experimentos previos
        try:
            decoded_path = file_bytes.decode('utf-8')
            if decoded_path.startswith("media/chats/"):
                cur.execute("UPDATE mensajes_chat SET ruta_media = %s, media_content = NULL WHERE id = %s", (decoded_path, msg_id))
                conn.commit()
                continue
        except:
            pass # Son bytes de un archivo real
        
        chat_folder = os.path.join("media", "chats", str(chat_id))
        os.makedirs(chat_folder, exist_ok=True)
        
        ext = "bin"
        if tipo == "imagen": ext = "jpg"
        elif tipo == "video": ext = "mp4"
        elif tipo == "voz": ext = "m4a"
        elif tipo == "document": 
            if contenido and "." in contenido:
                ext = contenido.split(".")[-1]
            else:
                ext = "pdf"

        filename = f"migrated_{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(chat_folder, filename)
        
        # 1. Guardar en el disco duro del servidor
        with open(filepath, "wb") as f:
            f.write(file_bytes)
        
        # 2. Guardar la ruta y VACIAR la columna de bytea
        cur.execute("""
            UPDATE mensajes_chat 
            SET ruta_media = %s, media_content = NULL 
            WHERE id = %s
        """, (filepath, msg_id))
        conn.commit()
        print(f"✅ Archivo del mensaje {msg_id} salvado en disco.")

    print("3️⃣ Limpiando la base de datos (Eliminando la columna bytea para siempre)...")
    try:
        cur.execute("ALTER TABLE mensajes_chat DROP COLUMN media_content;")
        cur.execute("ALTER TABLE mensajes_chat RENAME COLUMN ruta_media TO media_content;")
        conn.commit()
        print("✅ ¡Columna bytea eliminada con éxito!")
    except Exception as e:
        print(f"⚠️ Nota: {e}")
        conn.rollback()

    cur.close()
    conn.close()
    print("🎉 ¡Migración de chats completada! Tu Base de Datos ahora es de alta velocidad.")

if __name__ == "__main__":
    migrar_chats()