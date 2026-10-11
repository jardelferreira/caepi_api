from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CertificateObservation(Base):
    """Valor bruto observado numa fonte, preservado para auditoria."""

    __tablename__ = "certificate_observations"
    __table_args__ = (
        Index("ix_observations_ca_field", "certificate_id", "field_name"),
        Index("ix_observations_source", "source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    certificate_id: Mapped[int] = mapped_column(ForeignKey("certificates.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(40))
    field_name: Mapped[str] = mapped_column(String(100))
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
