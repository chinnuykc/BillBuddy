import logging
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    logger.info("Successfully connected to MongoDB")
except ConnectionFailure as e:
    logger.error(f"Failed to connect to MongoDB: {str(e)}")
    raise Exception(f"Failed to connect to MongoDB: {str(e)}")

db = client["splitwise_db"]
users_collection = db["users"]
expenses_collection = db["expenses"]
groups_collection = db["groups"]
payments_collection = db["payments"]

logger.info(f"Using database: {db.name}")
logger.info(f"Users collection: {users_collection.name}")
logger.info(f"Expenses collection: {expenses_collection.name}")
logger.info(f"Groups collection: {groups_collection.name}")
logger.info(f"Payments collection: {payments_collection.name}")

def close_mongo_connection():
    try:
        client.close()
        logger.info("MongoDB connection closed")
    except Exception as e:
        logger.error(f"Error closing MongoDB connection: {str(e)}")