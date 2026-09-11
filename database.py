import os
import uuid

from dotenv import load_dotenv
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Table, create_engine
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
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=True, index=True)
    english_word = Column(String, index=True, nullable=False)
    german_word = Column(String, index=True, nullable=False)
    audio_filename = Column(String, nullable=False)
    entry_type = Column(String, index=True, nullable=False, default="word")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    groups = relationship("Group", secondary=word_groups, back_populates="words")


class Group(Base):
    __tablename__ = "groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String, nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    words = relationship("Word", secondary=word_groups, back_populates="groups")


class Review(Base):
    __tablename__ = "reviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=True, index=True)
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
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
