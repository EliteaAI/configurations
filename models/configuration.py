from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Integer, String, Boolean, Text, DateTime, func, UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .enums import SourceTypes
from ..local_tools import db, config


class Configuration(db.Base):
    __tablename__ = "configuration"
    __table_args__ = (
        {"schema": config.POSTGRES_TENANT_SCHEMA},
    )
    pins_entity_name: str = 'configuration'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(UUID(as_uuid=True), unique=True, default=uuid.uuid4)

    project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=True)
    elitea_title: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String, nullable=False)  # e.g., "openai", "azure"
    section: Mapped[str] = mapped_column(String, nullable=False)  # e.g., "llm", "storage"
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # credentials/settings
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # any metadata
    shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    status_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status_logs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    source: Mapped[SourceTypes] = mapped_column(String, nullable=False, default=SourceTypes.user)
    author_id: Mapped[int] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, onupdate=func.now())
