from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base

# Every client-owned row carries tenant_id — this is what lets us sell to multiple firms
# with isolated data. Row-Level Security (Phase 7) is enforced on this column.


class Tenant(Base):
    """One firm. Inbound WhatsApp messages route to it by phone_number_id."""
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    phone_number_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    access_token: Mapped[str] = mapped_column(String)          # Graph API token for this number
    config: Mapped[dict] = mapped_column(JSONB, default=dict)  # pricing, workflows, languages...


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    # PII — never logged. Indexed because returning customers are matched on it.
    sin: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    dob: Mapped[str | None] = mapped_column(String, nullable=True)   # DD/MM/YYYY per spec
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    marital_status: Mapped[str | None] = mapped_column(String, nullable=True)
    spouse_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    children_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    rent_paid: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    rent_proof: Mapped[str | None] = mapped_column(String, nullable=True)
    province_changed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    move_date: Mapped[str | None] = mapped_column(String, nullable=True)
    new_province: Mapped[str | None] = mapped_column(String, nullable=True)
    left_canada_date: Mapped[str | None] = mapped_column(String, nullable=True)
    landing_date: Mapped[str | None] = mapped_column(String, nullable=True)
    is_newcomer: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    additional_notes: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_answers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # full intake, lossless
    status: Mapped[str] = mapped_column(String, default="intake")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    # Files arrive during intake, before a Client is materialised — attach to the session,
    # link the client at confirmation (Phase 5).
    session_id: Mapped[int | None] = mapped_column(ForeignKey("chat_sessions.id"), nullable=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String)
    storage_path: Mapped[str] = mapped_column(String)   # Supabase Storage key
    file_type: Mapped[str | None] = mapped_column(String, nullable=True)
    slip_type: Mapped[str | None] = mapped_column(String, nullable=True)
    employer_name: Mapped[str | None] = mapped_column(String, nullable=True)
    income_amount: Mapped[str | None] = mapped_column(String, nullable=True)
    parsed_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatSession(Base):
    """Per end-user conversation state. WhatsApp sessions key on (tenant, wa_number)."""
    __tablename__ = "chat_sessions"
    __table_args__ = (UniqueConstraint("tenant_id", "wa_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String, default="whatsapp")  # whatsapp | web
    wa_number: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    current_question_index: Mapped[int] = mapped_column(default=0)
    conversation_state_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    reference_number: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    summary_pdf_url: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    admin_notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Payment(Base):
    """e-Transfer (manual confirmation per spec) — no gateway. Reference number is the key."""
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    reference_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    amount: Mapped[float] = mapped_column(Numeric)
    method: Mapped[str] = mapped_column(String, default="etransfer")
    status: Mapped[str] = mapped_column(String, default="awaiting_confirmation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    reason: Mapped[str] = mapped_column(String)
    context_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
