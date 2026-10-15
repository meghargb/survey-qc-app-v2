"""
Database setup.
Uses SQLite for the pilot (a single file, no server needed).
Later, when you're ready to scale beyond a pilot, you can switch this
to PostgreSQL by changing DATABASE_URL below and installing psycopg2.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = "sqlite:///./survey_qc.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
