import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import func
from dotenv import load_dotenv

load_dotenv()

DATABASE_PATH = os.getenv("DATABASE_PATH", "./vocab.db")
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Word(Base):
    __tablename__ = "words"

    id = Column(Integer, primary_key=True, index=True)
    english_word = Column(String, index=True, nullable=False)
    german_word = Column(String, index=True, nullable=False)
    audio_filename = Column(String, nullable=False)
    entry_type = Column(String, index=True, nullable=False, default="word")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# Create tables
Base.metadata.create_all(bind=engine)

def migrate_add_entry_type():
    """Add entry_type column if it doesn't exist (for existing databases)."""
    import sqlite3
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.execute("PRAGMA table_info(words)")
    columns = [row[1] for row in cursor.fetchall()]
    if "entry_type" not in columns:
        conn.execute("ALTER TABLE words ADD COLUMN entry_type VARCHAR NOT NULL DEFAULT 'word'")
        conn.commit()
    conn.close()

migrate_add_entry_type()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
