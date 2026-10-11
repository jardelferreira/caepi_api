"""Initial schema for certificates, observations and documents."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "certificates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ca_number", sa.String(32), nullable=False),
        sa.Column("status", sa.String(80)),
        sa.Column("valid_until", sa.Date()),
        sa.Column("manufacturer_document", sa.String(32)),
        sa.Column("manufacturer", sa.String(255)),
        sa.Column("equipment_name", sa.String(500)),
        sa.Column("equipment_description", sa.Text()),
        sa.Column("brand", sa.String(255)),
        sa.Column("protection_details", sa.Text()),
        sa.Column("consolidated_data", sa.JSON(), nullable=False),
        sa.Column("last_source", sa.String(40)),
        sa.Column("last_source_update", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("ca_number", name="uq_certificates_ca_number"),
    )
    op.create_index("ix_certificates_ca_number", "certificates", ["ca_number"])
    op.create_index("ix_certificates_status", "certificates", ["status"])
    op.create_index("ix_certificates_manufacturer", "certificates", ["manufacturer"])
    op.create_index("ix_certificates_valid_until", "certificates", ["valid_until"])

    op.create_table(
        "certificate_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("certificate_id", sa.Integer(), sa.ForeignKey("certificates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column("value_text", sa.Text()),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_certificate_observations_certificate_id", "certificate_observations", ["certificate_id"])
    op.create_index("ix_observations_ca_field", "certificate_observations", ["certificate_id", "field_name"])
    op.create_index("ix_observations_source", "certificate_observations", ["source"])

    op.create_table(
        "certificate_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("certificate_id", sa.Integer(), sa.ForeignKey("certificates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("object_key", sa.String(1024)),
        sa.Column("sha256", sa.String(64)),
        sa.Column("content_type", sa.String(100)),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("source_url", sa.String(2048)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_certificate_documents_certificate_id", "certificate_documents", ["certificate_id"])


def downgrade() -> None:
    op.drop_table("certificate_documents")
    op.drop_table("certificate_observations")
    op.drop_table("certificates")
