#!/usr/bin/env python3
"""
Import phone numbers from a CSV file into a database group.
This script works independently of the compiled binary.
Usage: python contacts_loader.py <group_name> <csv_file>
"""

import sys
import os
import csv
import sqlite3
import argparse

DATABASE_NAME = 'sms_gateway.db'

def connect_db():
    """Establish a connection to the SQLite database."""
    conn = sqlite3.connect(DATABASE_NAME)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def clean_number(num: str) -> str:
    """Remove spaces, dashes, and parentheses from a phone number."""
    return ''.join(num.split()).replace('-', '').replace('(', '').replace(')', '')

def save_or_update_number_and_group(phone_number: str, group_name: str) -> str:
    """
    Insert or retrieve a phone number and associate it with a group.
    Returns a message describing the result.
    """
    conn = connect_db()
    cursor = conn.cursor()
    phone_number = phone_number.strip()
    group_name = group_name.strip()
    result_message = ""

    try:
        # 1. Ensure group exists
        cursor.execute("INSERT OR IGNORE INTO Groups (group_name) VALUES (?)", (group_name,))
        cursor.execute("SELECT id FROM Groups WHERE group_name = ?", (group_name,))
        group_id = cursor.fetchone()[0]

        # 2. Ensure phone number exists
        cursor.execute("SELECT id FROM Phone_Numbers WHERE phone_number = ?", (phone_number,))
        number_record = cursor.fetchone()
        if number_record:
            number_id = number_record[0]
            result_message += f"Number {phone_number} already exists. "
            is_new_number = False
        else:
            cursor.execute("INSERT INTO Phone_Numbers (phone_number) VALUES (?)", (phone_number,))
            number_id = cursor.lastrowid
            result_message += f"New number {phone_number} saved. "
            is_new_number = True

        # 3. Create association (if not already present)
        cursor.execute("""
            INSERT OR IGNORE INTO Group_Association (number_id, group_id)
            VALUES (?, ?)
        """, (number_id, group_id))

        if cursor.rowcount > 0:
            result_message += f"Associated with group '{group_name}'."
        elif is_new_number:
            result_message += f"Associated with group '{group_name}'."
        else:
            result_message += f"Group '{group_name}' was already associated with this number."

        conn.commit()
        return result_message

    except Exception as e:
        conn.rollback()
        return f"Database Error: {e}"
    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser(description='Import phone numbers from CSV into a group.')
    parser.add_argument('group', help='Group name to associate numbers with')
    parser.add_argument('csv_file', help='Path to CSV file containing phone numbers')
    args = parser.parse_args()

    group = args.group.strip()
    csv_path = args.csv_file

    if not group:
        print("Error: Group name cannot be empty.")
        sys.exit(1)

    # Read CSV file
    numbers = []
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                if row:
                    num = row[0].strip()
                    if num:
                        cleaned = clean_number(num)
                        if cleaned:
                            numbers.append(cleaned)
    except FileNotFoundError:
        print(f"Error: File '{csv_path}' not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading CSV: {e}")
        sys.exit(1)

    if not numbers:
        print("No phone numbers found in the CSV file.")
        return

    print(f"Found {len(numbers)} numbers. Adding to group '{group}'...")

    success = 0
    errors = 0
    for num in numbers:
        try:
            result = save_or_update_number_and_group(num, group)
            print(f"  {result}")
            success += 1
        except Exception as e:
            print(f"  Error adding {num}: {e}")
            errors += 1

    print(f"\nDone. Added {success} numbers, {errors} errors.")

if __name__ == '__main__':
    main()