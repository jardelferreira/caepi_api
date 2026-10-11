from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, Integer, String, Text, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Certificate(Base):
    """Projeção consolidada dos dados de um Certificado de Aprovação."""

    __tablename__ = "certificates"
    __table_args__ = (
        Index("ix_certificates_status", "status"),
        Index("ix_certificates_manufacturer", "manufacturer"),
        Index("ix_certificates_valid_until", "valid_until"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ca_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    manufacturer_document: Mapped[str | None] = mapped_column(String(32), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    equipment_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    equipment_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    protection_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    consolidated_data: Mapped[dict] = mapped_column(JSON, default=dict)
    last_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_source_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
