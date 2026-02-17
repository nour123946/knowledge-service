import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# ✅ Exemple: MONGO_URI="mongodb://localhost:27017"
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "knowledge_service_db")

client = MongoClient(MONGO_URI)
db = client[MONGO_DB_NAME]

# ✅ Collection CB13
conversation_collection = db["conversations"]

# ✅ Index utiles (performance dashboard)
conversation_collection.create_index("session_id", unique=True)
conversation_collection.create_index("updated_at")
conversation_collection.create_index("escalated")
conversation_collection.create_index("channel")
