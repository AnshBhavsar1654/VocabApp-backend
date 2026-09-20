import os
import uuid

from dotenv import load_dotenv
from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, String, Table, create_engine
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.sql import func

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

engine = create_engine(
    DATABASE_URL,
    poolclass=NullPool,
    connect_args={"sslmode": "require"},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

word_groups = Table(
    "word_groups",
    Base.metadata,
    Column("word_id", UUID(as_uuid=True), ForeignKey("words.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
)


class Word(Base):
    __tablename__ = "words"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    # Plain UUID, no SQLAlchemy ForeignKey to auth.users: that table lives in
    # the Supabase auth schema and is not part of Base.metadata, so a
    # ForeignKey("auth.users.id") makes every flush fail in
    # sort_tables with NoReferencedTableError (seen on PATCH /words/{id}).
    # The Postgres-level FK (if created via dashboard/migration) still
    # enforces integrity; the ORM just doesn't need to model it.
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    english_word = Column(String, index=True, nullable=False)
    german_word = Column(String, index=True, nullable=False)
    audio_filename = Column(String, nullable=True, default="")
    entry_type = Column(String, index=True, nullable=False, default="word")
    # Linguistic metadata (nullable = unknown / not applicable).
    # pos: noun | verb | adjective | adverb | phrase | other
    pos = Column(String, nullable=True, default=None)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    groups = relationship("Group", secondary=word_groups, back_populates="words")


class Group(Base):
    __tablename__ = "groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    # See Word.user_id: no ORM-level FK to auth.users (not in metadata).
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    name = Column(String, nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Manual card order for the group's word list (drag-to-reorder in the UI).
    # Ordered list of word-id strings; words missing from the list (e.g. newly
    # added) are appended at the end on read. Nullable = keep server default.
    word_order = Column(JSON, nullable=True, default=None)
    words = relationship("Word", secondary=word_groups, back_populates="groups")


class Review(Base):
    __tablename__ = "reviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    # See Word.user_id: no ORM-level FK to auth.users (not in metadata).
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    word_id = Column(UUID(as_uuid=True), ForeignKey("words.id", ondelete="CASCADE"), nullable=False, index=True)
    is_correct = Column(Boolean, nullable=False)
    self_assessment = Column(String, nullable=True)  # Permitted values: "got" | "missed".
    typed_answer = Column(String, nullable=True)
    prompt_lang = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    word = relationship("Word")


class Profile(Base):
    __tablename__ = "profiles"

    id = Column(UUID(as_uuid=True), primary_key=True)  # References auth.users.id.
    email = Column(String, nullable=False)
    full_name = Column(String, nullable=True, default=None)
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GrammarCache(Base):
    """Shared auto-suggest cache: one row per German lemma.

    First resolution runs the services.grammar cascade (DWDS -> heuristic);
    every later lookup is DB-only, which is what makes suggestions
    deterministic. User edits live on words.pos — this table is only the
    suggestion source, never user data.
    """

    __tablename__ = "grammar_cache"

    lemma = Column(String, primary_key=True)
    pos = Column(String, nullable=True)
    source = Column(String, nullable=False, default="none")
    confidence = Column(String, nullable=False, default="none")
    ambiguous = Column(Boolean, nullable=False, default=False)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now())


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
