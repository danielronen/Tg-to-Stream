from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne
import os

# --- CONFIGURATION ---
load_dotenv("config.env")
MONGO_URI = os.getenv("DATABASE_URL")
DB_NAME = 'surftg'
COLLECTION_NAME = 'files'

# set to True to PREVIEW changes, False to APPLY changes
DRY_RUN = True 

# The fields to check
FIELDS_TO_CHECK = ['img', 'background']

# Exact strings to swap
# Note: I removed the trailing '/' from the new domain to prevent double slashes 
# (e.g. .com//api) since your existing data likely already has a slash after .com
OLD_DOMAIN = "https://command-britannica-jones-far.trycloudflare.com"
NEW_DOMAIN = "https://advisor-feedback-quiz-irc.trycloudflare.com"


def update_urls():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    collection = db[COLLECTION_NAME]

    # 1. Find records where ANY relevant field starts with the old domain
    query = {
        "$or": [
            {field: {"$regex": f"^{OLD_DOMAIN}"}} for field in FIELDS_TO_CHECK
        ]
    }

    cursor = collection.find(query)
    bulk_operations = []
    wating = True
    while wating: 
        user_input = input("1) DRY RUN\n2) LIVE UPDATE\n",)
        if user_input == "1":
            DRY_RUN = True
            wating = False
        if user_input == "2":
            DRY_RUN = False
            wating = False
        
        
    print(f"--- STARTED ({'DRY RUN' if DRY_RUN else 'LIVE UPDATE'}) ---")

    for doc in cursor:
        update_fields = {}
        doc_modified = False
        
        # Check each field (img, background)
        for field in FIELDS_TO_CHECK:
            original_value = doc.get(field)
            
            # Ensure it's a string and starts with the target domain
            if isinstance(original_value, str) and original_value.startswith(OLD_DOMAIN):
                # Replace the domain part only once (count=1)
                new_value = original_value.replace(OLD_DOMAIN, NEW_DOMAIN, 1)
                update_fields[field] = new_value
                doc_modified = True

                if DRY_RUN:
                    print(f"\n[Doc ID: {doc['_id']}] Field: '{field}'")
                    print(f"  Old: {original_value}")
                    print(f"  New: {new_value}")

        # If live run, prepare the update
        if not DRY_RUN and doc_modified:
            bulk_operations.append(
                UpdateOne({"_id": doc["_id"]}, {"$set": update_fields})
            )

    # Execute if not dry run
    if not DRY_RUN:
        if bulk_operations:
            result = collection.bulk_write(bulk_operations)
            print(f"\n--- SUCCESS ---")
            print(f"Matched: {result.matched_count}")
            print(f"Modified: {result.modified_count}")
        else:
            print("\nNo documents needed updating.")
    else:
        print(f"\n--- DRY RUN COMPLETE ---")
        print(f"Found {len(list(cursor)) if 'cursor' in locals() else 'N/A'} matching documents (approx).")
        print("Check the URLs above. If they look correct, set DRY_RUN = False.")

if __name__ == "__main__":
    update_urls()