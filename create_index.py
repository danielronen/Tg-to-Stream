# Run once
from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv("config.env")
DATABASE_URL = os.getenv("DATABASE_URL")
client = MongoClient(f"{DATABASE_URL}/surftg")
db = client.get_default_database()

#db.files.create_index([("chat_id", 1), ("msg_id", 1)], unique=True)
#print("✅ Index created!")
indexes = db.files.list_indexes()
for index in indexes:
    print(index)