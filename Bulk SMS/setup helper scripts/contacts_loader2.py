import sqlite3
import os
import re

DB_NAME = "sms_gateway.db"

def extract_and_clean_numbers(file_path):
    """
    Reads the file, removes all spaces/dashes, and then 
    extracts Ugandan numbers in +2567XXXXXXXX format.
    """
    found_numbers = set()
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            # 1. Remove all spaces, dashes, and brackets to handle (+256) 771 842-520
            clean_content = re.sub(r'[\s\-\(\)]', '', content)
            
            # 2. Look for the Ugandan pattern (9 digits starting with 7)
            # This handles: 2567..., 07..., or just 7...
            pattern = re.compile(r'(?:256|0)?(7\d{8})')
            matches = pattern.findall(clean_content)
            
            for m in matches:
                # Standardize to +256 format
                found_numbers.add("+256" + m)
                
    except Exception as e:
        print(f"Error reading file: {e}")
        
    return found_numbers

def smart_ingest(file_path, group_name):
    numbers = extract_and_clean_numbers(file_path)
    if not numbers:
        print("No numbers found in that file! Check the file path and format.")
        return

    # Connect to your existing database
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Ensure Group exists
    cursor.execute("INSERT OR IGNORE INTO Groups (group_name) VALUES (?)", (group_name,))
    cursor.execute("SELECT id FROM Groups WHERE group_name = ?", (group_name,))
    group_id = cursor.fetchone()[0]

    new_entries = 0
    for num in numbers:
        # 1. Add Phone Number
        cursor.execute("INSERT OR IGNORE INTO Phone_Numbers (phone_number) VALUES (?)", (num,))
        cursor.execute("SELECT id FROM Phone_Numbers WHERE phone_number = ?", (num,))
        num_id = cursor.fetchone()[0]

        # 2. Associate with Group
        cursor.execute("INSERT OR IGNORE INTO Group_Association (number_id, group_id) VALUES (?, ?)", 
                       (num_id, group_id))
        if cursor.rowcount > 0:
            new_entries += 1

    conn.commit()
    conn.close()
    print(f"\n--- SUCCESS ---")
    print(f"File Processed: {file_path}")
    print(f"Numbers Found: {len(numbers)}")
    print(f"Added to '{group_name}': {new_entries} (New)")
    print(f"Duplicates Skipped: {len(numbers) - new_entries}")

if __name__ == "__main__":
    print("--- UGANDA SMS SMART INGESTOR (v2) ---")
    path = input("Enter filename (e.g., ug.csv): ").strip()
    group = input("Enter the target Group Name: ").strip()
    smart_ingest(path, group)





