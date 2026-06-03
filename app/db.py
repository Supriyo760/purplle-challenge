from sqlalchemy import create_engine, Column, String, Integer, Float, Boolean, DateTime, Enum as SQLEnum, JSON, event
from sqlalchemy.orm import declarative_base, sessionmaker
from .models import EventType

DATABASE_URL = "sqlite:///./store_intelligence.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class DBEvent(Base):
    __tablename__ = "events"

    # We use event_id as the primary key since it must be globally unique
    event_id = Column(String, primary_key=True, index=True)
    store_id = Column(String, index=True)
    camera_id = Column(String)
    visitor_id = Column(String, index=True)
    event_type = Column(SQLEnum(EventType), index=True)
    timestamp = Column(DateTime, index=True)
    zone_id = Column(String, index=True, nullable=True)
    dwell_ms = Column(Integer, default=0)
    is_staff = Column(Boolean, default=False)
    confidence = Column(Float)
    metadata_json = Column(JSON, nullable=True)  # Store the metadata dict as JSON

class DBTransaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    store_id = Column(String, index=True)
    timestamp = Column(DateTime, index=True)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
