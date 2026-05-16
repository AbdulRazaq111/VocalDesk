from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

# .env file se password aur settings load karna
load_dotenv()

# Database ka link uthana
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

# Engine banana (Connection start karna)
engine = create_engine(SQLALCHEMY_DATABASE_URL)

# Session banana (Iske zariye hum data bhejenge/mangwayenge)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class (Is se hum tables banayenge)
Base = declarative_base()

# Ye function har request ke liye database open aur close karega
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()