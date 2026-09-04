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

def migrar_perfiles():
    print("🚀 Iniciando migración de Fotos de Perfil...")
    
    # Aseguramos que la carpeta exista
    os.makedirs(os.path.join("media", "perfiles"), exist_ok=True)
    
    conn = get_db_connection()
    cur = conn.cursor()

    print("📸 Buscando fotos de perfil atrapadas en la base de datos...")
    # Buscamos quiénes todavía tienen la foto en bytea
    cur.execute("SELECT user_id FROM datos_usuario WHERE foto IS NOT NULL")
    user_ids = [row[0] for row in cur.fetchall()]
    
    if not user_ids:
        print("✨ No hay fotos atrapadas. ¡Todo está limpio!")
        
    for uid in user_ids:
        cur.execute("SELECT foto FROM datos_usuario WHERE user_id = %s", (uid,))
        foto_data = cur.fetchone()[0]
        
        if foto_data:
            filename = f"old_profile_{uuid.uuid4().hex}.jpg"
            filepath = os.path.join("media", "perfiles", filename)
            
            # 1. Guardar en el disco duro del servidor
            with open(filepath, "wb") as f:
                f.write(foto_data)
            
            # 2. Guardar la ruta y VACIAR la columna de bytea
            cur.execute("""
                UPDATE datos_usuario 
                SET ruta_foto = %s, foto = NULL 
                WHERE user_id = %s
            """, (filename, uid))
            conn.commit()
            print(f"✅ Foto del usuario {uid} salvada en disco y borrada de la DB.")

    cur.close()
    conn.close()
    print("🎉 ¡Migración de perfiles completada! Base de datos 100% libre de fotos.")

if __name__ == "__main__":
    migrar_perfiles()