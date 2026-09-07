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
    print("🚀 Iniciando migración blindada de Archivos de Chat...")
    
    os.makedirs(os.path.join("media", "chats"), exist_ok=True)
    
    conn = get_db_connection()
    cur = conn.cursor()

    print("1️⃣ Preparando la base de datos...")
    cur.execute("ALTER TABLE mensajes_chat ADD COLUMN IF NOT EXISTS ruta_media TEXT;")
    conn.commit()

    print("2️⃣ Extrayendo y convirtiendo archivos...")
    cur.execute("SELECT id, chat_id, tipo, media_content, contenido FROM mensajes_chat WHERE media_content IS NOT NULL")
    mensajes = cur.fetchall()
    
    if not mensajes:
        print("✨ No hay archivos atrapados. ¡Todo está limpio!")
        
    for msg in mensajes:
        msg_id, chat_id, tipo, media_content, contenido = msg
        
        # Manejo seguro de tipos (texto, bytes o memoryview)
        if isinstance(media_content, str):
            file_bytes = media_content.encode('utf-8')
        elif isinstance(media_content, bytes):
            file_bytes = media_content
        elif hasattr(media_content, 'tobytes'):
            file_bytes = media_content.tobytes()
        else:
            try:
                file_bytes = bytes(media_content)
            except Exception:
                file_bytes = str(media_content).encode('utf-8')
        
        # Si ya es una ruta guardada previamente
        try:
            decoded_path = file_bytes.decode('utf-8')
            if decoded_path.startswith("media/chats/"):
                cur.execute("UPDATE mensajes_chat SET ruta_media = %s, media_content = NULL WHERE id = %s", (decoded_path, msg_id))
                conn.commit()
                continue
        except:
            pass
        
        chat_folder = os.path.join("media", "chats", str(chat_id))
        os.makedirs(chat_folder, exist_ok=True)
        
        ext = "bin"
        if tipo == "imagen": ext = "jpg"
        elif tipo == "video": ext = "mp4"
        elif tipo == "voz": ext = "m4a"
        elif tipo == "document": 
            ext = contenido.split(".")[-1] if contenido and "." in contenido else "pdf"

        filename = f"migrated_{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(chat_folder, filename)
        
        # Guardar en disco duro
        with open(filepath, "wb") as f:
            f.write(file_bytes)
        
        # Guardar la ruta y limpiar el campo antiguo
        cur.execute("""
            UPDATE mensajes_chat 
            SET ruta_media = %s, media_content = NULL 
            WHERE id = %s
        """, (filepath, msg_id))
        conn.commit()
        print(f"✅ Archivo del mensaje {msg_id} migrado correctamente.")

    print("3️⃣ Limpiando estructura de la base de datos...")
    try:
        cur.execute("ALTER TABLE mensajes_chat DROP COLUMN media_content;")
        cur.execute("ALTER TABLE mensajes_chat RENAME COLUMN media_ruta TO media_content;")
        conn.commit()
        print("✅ ¡Columna bytea eliminada para siempre!")
    except Exception as e:
        print(f"⚠️ Nota de limpieza: {e}")
        conn.rollback()

    cur.close()
    conn.close()
    print("🎉 ¡Migración finalizada con éxito absoluto!")

if __name__ == "__main__":
    migrar_chats()