from sqlalchemy import Column, Integer, String, Text, LargeBinary, ForeignKey
from database import Base

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    nombre = Column(String)

class DatosUsuario(Base):
    __tablename__ = "datos_usuario"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    nombre_empresa = Column(String(255), nullable=False)
    direccion = Column(String(255), nullable=True)  # Ya no es obligatorio
    ubicacion_google_maps = Column(Text, nullable=True)
    telefono = Column(String(20), nullable=True)
    horario = Column(String(100), nullable=True)
    categoria = Column(String(100), nullable=True)
    otra_categoria = Column(String(100), nullable=True)
    servicios = Column(Text, nullable=True)
    sitio_web = Column(Text, nullable=True)
    foto = Column(LargeBinary, nullable=True)
