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

def migrar():
    print("🚀 Iniciando migración masiva de multimedia...")
    
    # Aseguramos que la carpeta exista
    os.makedirs(os.path.join("media", "publicaciones"), exist_ok=True)
    
    conn = get_db_connection()
    cur = conn.cursor()

    # --- 1. MIGRAR IMÁGENES ---
    print("📸 Buscando imágenes atrapadas en la base de datos...")
    # Solo traemos los IDs primero para no saturar la RAM de golpe
    cur.execute("SELECT id FROM publicacion_imagenes WHERE imagen IS NOT NULL")
    img_ids = [row[0] for row in cur.fetchall()]
    
    for img_id in img_ids:
        cur.execute("SELECT imagen FROM publicacion_imagenes WHERE id = %s", (img_id,))
        img_data = cur.fetchone()[0]
        
        if img_data:
            filename = f"old_img_{uuid.uuid4().hex}.jpg"
            filepath = os.path.join("media", "publicaciones", filename)
            
            # 1. Guardar en el disco duro
            with open(filepath, "wb") as f:
                f.write(img_data)
            
            # 2. Actualizar la ruta y VACIAR la columna de bytea para liberar RAM
            cur.execute("""
                UPDATE publicacion_imagenes 
                SET ruta_imagen = %s, imagen = NULL 
                WHERE id = %s
            """, (filename, img_id))
            conn.commit()
            print(f"✅ Imagen {img_id} salvada en disco y borrada de la DB.")

    # --- 2. MIGRAR VIDEOS ---
    print("🎥 Buscando videos atrapados en la base de datos...")
    cur.execute("SELECT id FROM publicaciones WHERE video IS NOT NULL")
    video_ids = [row[0] for row in cur.fetchall()]
    
    for post_id in video_ids:
        cur.execute("SELECT video FROM publicaciones WHERE id = %s", (post_id,))
        video_data = cur.fetchone()[0]
        
        if video_data:
            filename = f"old_vid_{uuid.uuid4().hex}.mp4"
            filepath = os.path.join("media", "publicaciones", filename)
            
            with open(filepath, "wb") as f:
                f.write(video_data)
            
            cur.execute("""
                UPDATE publicaciones 
                SET ruta_video = %s, video = NULL 
                WHERE id = %s
            """, (filename, post_id))
            conn.commit()
            print(f"✅ Video del post {post_id} salvado en disco y borrado de la DB.")

    cur.close()
    conn.close()
    print("🎉 ¡Migración completada con éxito! Tu servidor respira de nuevo.")

if __name__ == "__main__":
    migrar()