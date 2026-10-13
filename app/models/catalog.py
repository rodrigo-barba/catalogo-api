from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


ID_TYPE = BigInteger().with_variant(Integer, "sqlite")


class Organigrama(Base):
    __tablename__ = "organigrama"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True)
    ruta_completa: Mapped[str] = mapped_column(String(255), nullable=False)
    es_vigente: Mapped[int | None] = mapped_column(Integer, nullable=True)

    proyectos: Mapped[list[Proyecto]] = relationship(back_populates="organigrama")


class Proyecto(Base):
    __tablename__ = "proyecto"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True)
    organigrama_id: Mapped[int | None] = mapped_column(ForeignKey("organigrama.id"), nullable=True)
    id_proyecto_plataforma: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    nombre: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nombre_normalizado: Mapped[str] = mapped_column(String(100), nullable=False)
    plataforma: Mapped[str] = mapped_column(String(10), nullable=False)
    estado: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'ACTIVO'"))
    ruta_organigrama: Mapped[str] = mapped_column(String(700), nullable=False)
    nro_ticket_setup: Mapped[str | None] = mapped_column(String(255), nullable=True)
    registro_alta_id: Mapped[int] = mapped_column(ForeignKey("registro_integracion.id"), nullable=False)
    ruta_requiere_revision: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("0"),
    )

    organigrama: Mapped[Organigrama | None] = relationship(back_populates="proyectos")
    componentes: Mapped[list[Componente]] = relationship(back_populates="proyecto")


class Componente(Base):
    __tablename__ = "componente"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True)
    proyecto_id: Mapped[int] = mapped_column(ForeignKey("proyecto.id"), nullable=False)
    gitlab_repositorio_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    nombre: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nombre_normalizado: Mapped[str] = mapped_column(String(100), nullable=False)
    estado: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'ACTIVO'"))
    gitlab_repositorio_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    registro_alta_id: Mapped[int] = mapped_column(ForeignKey("registro_integracion.id"), nullable=False)

    proyecto: Mapped[Proyecto] = relationship(back_populates="componentes")
    entornos: Mapped[list[ComponenteEntorno]] = relationship(back_populates="componente")


class ComponenteEntorno(Base):
    __tablename__ = "componente_entorno"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True)
    componente_id: Mapped[int] = mapped_column(ForeignKey("componente.id"), nullable=False)
    entorno_id: Mapped[int] = mapped_column(ForeignKey("entorno.id"), nullable=False)
    espacio: Mapped[str] = mapped_column(String(200), nullable=False)
    estado: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'ACTIVO'"))
    version_vigente_id: Mapped[int | None] = mapped_column(ID_TYPE, nullable=True)
    despliegue_vigente_id: Mapped[int | None] = mapped_column(ID_TYPE, nullable=True)
    registro_alta_id: Mapped[int] = mapped_column(ForeignKey("registro_integracion.id"), nullable=False)

    componente: Mapped[Componente] = relationship(back_populates="entornos")


class RegistroIntegracion(Base):
    __tablename__ = "registro_integracion"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True)
    id_evento_origen: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tipo_registro: Mapped[str] = mapped_column(String(20), nullable=False)
    herramienta_origen: Mapped[str | None] = mapped_column(String(50), nullable=True)
    nro_ticket_invgate: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fecha_evento: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    setup_evento: Mapped[SetupEvento | None] = relationship(back_populates="registro_integracion")
    sincronizacion_outbox: Mapped[SincronizacionOutbox | None] = relationship(
        back_populates="registro_integracion"
    )


class Entorno(Base):
    __tablename__ = "entorno"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    nombre: Mapped[str] = mapped_column(String(50), nullable=False)
    orden: Mapped[int] = mapped_column(Integer, nullable=False)

class SetupEvento(Base):
    __tablename__ = "setup_evento"
    __table_args__ = (UniqueConstraint("registro_integracion_id", name="uq_setup_evento_registro_integracion"),)

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True)
    registro_integracion_id: Mapped[int] = mapped_column(
        ForeignKey("registro_integracion.id"),
        nullable=False,
    )
    hash_semantico: Mapped[str] = mapped_column(String(64), nullable=False)
    evidencia_canonica: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    actor_cuil: Mapped[str] = mapped_column(String(11), nullable=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    registro_integracion: Mapped[RegistroIntegracion] = relationship(back_populates="setup_evento")


class SincronizacionOutbox(Base):
    __tablename__ = "sincronizacion_outbox"
    __table_args__ = (
        UniqueConstraint(
            "registro_integracion_id",
            name="uq_sincronizacion_outbox_registro_integracion",
        ),
    )

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True)
    registro_integracion_id: Mapped[int] = mapped_column(
        ForeignKey("registro_integracion.id"),
        nullable=False,
    )
    estado: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'pending'"))
    creado_en: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    registro_integracion: Mapped[RegistroIntegracion] = relationship(
        back_populates="sincronizacion_outbox"
    )
