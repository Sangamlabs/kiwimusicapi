from datetime import datetime
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text
)
from sqlalchemy.orm import relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id = Column(BigInteger, unique=True, index=True, nullable=False)
    status = Column(String(32), default="active", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")

class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    api_key = Column(String(64), nullable=True)  # Plaintext key for retrieval/copying by owner
    key_hash = Column(String(64), unique=True, index=True, nullable=False)
    key_prefix = Column(String(16), index=True, nullable=False)
    total_quota = Column(Integer, default=200, nullable=False)
    used_requests = Column(Integer, default=0, nullable=False)
    failed_requests = Column(Integer, default=0, nullable=False)
    status = Column(String(32), default="active", index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    revoked_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="api_keys")
    usage_logs = relationship("ApiUsage", back_populates="api_key", cascade="all, delete-orphan")

    __table_args__ = (
        Index(
            "uq_api_keys_user_active",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

class Track(Base):
    __tablename__ = "tracks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    track_id = Column(String(128), unique=True, index=True, nullable=False)
    provider = Column(String(64), default="jiosaavn", index=True, nullable=False)
    provider_song_id = Column(String(128), nullable=False)
    title = Column(String(256), index=True, nullable=False)
    artist = Column(String(256), index=True, nullable=False)
    album = Column(String(256), nullable=True)
    duration = Column(Integer, default=0, nullable=False)
    language = Column(String(64), nullable=True)
    thumbnail = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)
    storage_provider = Column(String(64), default="yukisbox", nullable=False)
    storage_uid = Column(String(256), index=True, nullable=True)
    storage_url = Column(Text, nullable=True)
    file_size = Column(BigInteger, nullable=True)
    audio_format = Column(String(32), default="mp3", nullable=False)
    status = Column(String(32), default="available", index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_accessed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_tracks_provider_and_song_id", "provider", "provider_song_id"),
        Index("ix_tracks_title_and_artist", "title", "artist"),
    )

class ApiUsage(Base):
    __tablename__ = "api_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_key_id = Column(Integer, ForeignKey("api_keys.id", ondelete="CASCADE"), index=True, nullable=False)
    endpoint = Column(String(128), nullable=False)
    status_code = Column(Integer, nullable=False)
    success = Column(Boolean, default=False, nullable=False)
    latency_ms = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    api_key = relationship("ApiKey", back_populates="usage_logs")

class UploadJob(Base):
    __tablename__ = "upload_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    track_id = Column(String(128), unique=True, index=True, nullable=False)
    status = Column(String(32), default="PENDING", index=True, nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    last_error = Column(Text, nullable=True)
    locked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

