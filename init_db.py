# init_db.py
from database import Base, engine
from models import Usuario

print("Creando las tablas en la base de datos...")
Base.metadata.create_all(bind=engine)
print("¡Tablas creadas con éxito!")
