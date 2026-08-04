# config/database.py
import os
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConnectionFailure
import logging

logger = logging.getLogger(__name__)

class Database:
    client: AsyncIOMotorClient = None
    database = None

# MongoDB configuration
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "interview_db")

async def connect_to_mongo():
    """Create database connection"""
    try:
        Database.client = AsyncIOMotorClient(MONGODB_URL, serverSelectionTimeoutMS=2000)
        Database.database = Database.client[DATABASE_NAME]
        
        # Test the connection
        await Database.client.admin.command('ping')
        logger.info(f"Connected to MongoDB at {MONGODB_URL}")
        
    except Exception as e:
        logger.warning(f"Could not connect to MongoDB ({e}). Falling back to in-memory mode.")
        Database.client = None
        Database.database = None

async def close_mongo_connection():
    """Close database connection"""
    if Database.client:
        Database.client.close()
        logger.info("Disconnected from MongoDB")

def get_database():
    """Get database instance"""
    return Database.database
