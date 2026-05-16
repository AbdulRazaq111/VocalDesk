from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from database import Base
import datetime

# 1. User Table (Customer ki details yahan save hongi)
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, index=True) # WhatsApp number
    name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationship: Ek user ki bohot sari chats ho sakti hain
    conversations = relationship("Conversation", back_populates="owner")

class BusinessKnowledge(Base):
    __tablename__ = "business_knowledge"
    id = Column(Integer, primary_key=True, index=True)
    question = Column(Text)  # Is mein Product/Website ka Title ayega
    answer = Column(Text)    # Is mein Product ki Details ayengi

    
# 2. Conversation Table (Baat cheet yahan save hogi)
class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id")) # Kiske sath baat hui
    message_type = Column(String) # 'text' ya 'audio'
    user_message = Column(Text, nullable=True) # User ne kya kaha
    ai_response = Column(Text, nullable=True)  # AI ne kya jawab diya
    audio_url = Column(String, nullable=True)  # Audio file kahan save hai
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    owner = relationship("User", back_populates="conversations")

