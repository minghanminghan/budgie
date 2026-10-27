from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UTCDateTime(TypeDecorator):
    """Stores naive UTC; returns aware UTC. Naive input is taken as UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value.replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime, dict: JSON}


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    adapter: Mapped[str] = mapped_column(String(32))
    input_url: Mapped[str] = mapped_column(Text)
    config_key: Mapped[str] = mapped_column(Text, unique=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    title: Mapped[str] = mapped_column(Text)
    site_domain: Mapped[str] = mapped_column(String(255))
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime]
    unsubscribed_at: Mapped[datetime | None]
    last_fetched_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_url: Mapped[str] = mapped_column(Text, unique=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    site_domain: Mapped[str] = mapped_column(String(255))
    published_at: Mapped[datetime | None]
    first_seen_at: Mapped[datetime]
    text: Mapped[str] = mapped_column(Text, default="")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    dedup_key: Mapped[str | None] = mapped_column(Text, index=True)
    is_teaser: Mapped[bool] = mapped_column(Boolean, default=False)


class ItemSource(Base):
    __tablename__ = "item_sources"

    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    seen_at: Mapped[datetime]
    raw_url: Mapped[str] = mapped_column(Text)


class Embedding(Base):
    __tablename__ = "embeddings"

    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), primary_key=True)
    model: Mapped[str] = mapped_column(String(255), primary_key=True)
    dim: Mapped[int]
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime]


class Issue(Base):
    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_date: Mapped[date] = mapped_column(Date, unique=True)
    built_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(16))
    ranker_version: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)


class Impression(Base):
    __tablename__ = "impressions"
    __table_args__ = (
        UniqueConstraint("issue_id", "item_id"),
        UniqueConstraint("issue_id", "slot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    slot: Mapped[int]
    is_wildcard: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float] = mapped_column(Float)
    features: Mapped[dict] = mapped_column(JSON, default=dict)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    impression_id: Mapped[int | None] = mapped_column(ForeignKey("impressions.id"))
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id"))
    created_at: Mapped[datetime]
    user_agent: Mapped[str | None] = mapped_column(Text)


class RankerModel(Base):
    __tablename__ = "ranker_models"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    trained_at: Mapped[datetime]
    n_labels: Mapped[int]
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_path: Mapped[str | None] = mapped_column(Text)
