import sqlite3
import time 
from typing import List, Dict, Tuple, Any

DATABASE_NAME = 'sms_gateway.db'
# Near the top, after imports
MAX_ATTEMPTS = 3   # Maximum number of retries per message

def connect_db():
    """Establishes a connection to the SQLite database."""
    conn = sqlite3.connect(DATABASE_NAME)
    # Enable foreign key enforcement
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL") 
    return conn

def setup_database():
    """
    Creates the necessary tables including the new Message_Queue and Job_Status tables.
    """
    conn = connect_db()
    cursor = conn.cursor()

    # --- 1, 2, 3: Permanent Tables (Phone_Numbers, Groups, Group_Association) ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Phone_Numbers (
            id INTEGER PRIMARY KEY,
            phone_number TEXT UNIQUE NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Groups (
            id INTEGER PRIMARY KEY,
            group_name TEXT UNIQUE NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Group_Association (
            id INTEGER PRIMARY KEY,
            number_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            UNIQUE(number_id, group_id),
            FOREIGN KEY (number_id) REFERENCES Phone_Numbers(id) ON DELETE CASCADE,
            FOREIGN KEY (group_id) REFERENCES Groups(id) ON DELETE CASCADE
        )
    """)
    
    # --- 4. NEW: Message_Queue Table (Volatile, Recovery Data) ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Message_Queue (
            id INTEGER PRIMARY KEY,
            number_id INTEGER NOT NULL UNIQUE,
            message_body TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER DEFAULT 0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (number_id) REFERENCES Phone_Numbers(id) ON DELETE CASCADE
        )
    """)
        
    # --- 5. NEW: Job_Status Table (Single Row, Transactional State) ---
    # This is CRITICAL for the recovery system.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Job_Status (
            id INTEGER PRIMARY KEY DEFAULT 1,
            is_active BOOLEAN NOT NULL DEFAULT FALSE,
            group_names TEXT,
            message_body TEXT,
            start_time TEXT,
            -- Ensure there is only ever one row (ID 1)
            CHECK (id = 1)
        )
    """)
    
    # Ensure the single row exists (for the first time)
    cursor.execute("INSERT OR IGNORE INTO Job_Status (id, is_active) VALUES (1, FALSE)")

    conn.commit()
    conn.close()
    print("Database setup complete: All permanent, volatile, and job status tables are ready.")


# ------------------------------------------------------------------
# --- Core DB Functions (Retained from original code) ---
# ------------------------------------------------------------------

def save_or_update_number_and_group(phone_number: str, group_name: str) -> str:
    # (Original function body remains unchanged)
    conn = connect_db()
    cursor = conn.cursor()
    
    # Standardize input
    phone_number = phone_number.strip()
    group_name = group_name.strip()
    
    result_message = ""

    try:
        # --- 1. Ensure Group Exists (or insert it) ---
        cursor.execute(
            "INSERT OR IGNORE INTO Groups (group_name) VALUES (?)", 
            (group_name,)
        )
        # Get the group_id (either newly inserted or existing)
        cursor.execute("SELECT id FROM Groups WHERE group_name = ?", (group_name,))
        group_id = cursor.fetchone()[0]

        # --- 2. Ensure Phone Number Exists (or insert it) ---
        # Try to find the number
        cursor.execute("SELECT id FROM Phone_Numbers WHERE phone_number = ?", (phone_number,))
        number_record = cursor.fetchone()
        
        if number_record:
            number_id = number_record[0]
            result_message += f"Number {phone_number} already exists. "
            is_new_number = False
        else:
            # Insert the new number
            cursor.execute(
                "INSERT INTO Phone_Numbers (phone_number) VALUES (?)", 
                (phone_number,)
            )
            number_id = cursor.lastrowid
            result_message += f"New number {phone_number} saved. "
            is_new_number = True
        
        # --- 3. Create Association (Non-duplicating) ---
        cursor.execute(
            """
            INSERT OR IGNORE INTO Group_Association (number_id, group_id) 
            VALUES (?, ?)
            """, 
            (number_id, group_id)
        )
        
        if cursor.rowcount > 0:
            result_message += f"Associated with group '{group_name}'."
        elif is_new_number:
            result_message += f"Associated with group '{group_name}'."
        else:
            result_message += f"Group '{group_name}' was already associated with this number. No change made."


        conn.commit()
        return result_message
    
    except Exception as e:
        conn.rollback()
        return f"Database Error: {e}"
    finally:
        conn.close()





def query_numbers_by_groups(group_names: List[str]) -> List[Tuple[int, str, int]]:
    """
    Fetches numbers based on specific groups, OR fetches all numbers if "ALL" is passed.
    """
    if not group_names:
        return []

    conn = connect_db()
    cursor = conn.cursor()
    
    try:
        # --- NEW LOGIC: Check if the user wants ALL numbers ---
        # Convert all requested groups to uppercase to safely check for "ALL"
        if "ALL" in [g.upper().strip() for g in group_names]:
            cursor.execute("""
                SELECT 
                    pn.id, -- number_id
                    pn.phone_number,
                    MIN(ga.group_id) -- Satisfies the NOT NULL constraint in Message_Queue
                FROM 
                    Phone_Numbers AS pn
                INNER JOIN 
                    Group_Association AS ga ON pn.id = ga.number_id
                GROUP BY 
                    pn.id, pn.phone_number
            """)
            
        # --- ORIGINAL LOGIC: Specific groups only ---
        else:
            placeholders = ','.join(['?'] * len(group_names))
            cursor.execute(f"""
                SELECT DISTINCT 
                    pn.id, -- number_id
                    pn.phone_number,
                    g.id -- group_id
                FROM 
                    Phone_Numbers AS pn
                INNER JOIN 
                    Group_Association AS ga ON pn.id = ga.number_id
                INNER JOIN 
                    Groups AS g ON ga.group_id = g.id
                WHERE 
                    g.group_name IN ({placeholders})
            """, group_names)

        results =  cursor.fetchall()

        # --- ADD THIS PRINT FOR TESTING ---
        print(f"[TEST] query_numbers_by_groups for {group_names} returned {len(results)} numbers")
        # Optionally print the first few results (be careful not to flood the terminal)
        # print("First 5 results:", results[:5])

        return results
        
    except Exception as e:
        print(f"Query Error: {e}")
        return []
    finally:
        conn.close()





# ------------------------------------------------------------------
# --- NEW JOB STATUS & RECOVERY FUNCTIONS (CRITICAL CHANGES) ---
# ------------------------------------------------------------------

def get_job_status() -> Dict[str, Any]:
    """
    Reads the single Job_Status row to check for active jobs and recovery data.
    Returns a dictionary of the status or an empty dict if the row is missing.
    """
    conn = connect_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT is_active, group_names, message_body FROM Job_Status WHERE id = 1")
        row = cursor.fetchone()
        
        if row:
            columns = ['is_active', 'group_names', 'message_body']
            # is_active comes back as 0 or 1 from SQLite
            return dict(zip(columns, row))
        return {}
    except Exception as e:
        print(f"Get Job Status Error: {e}")
        return {}
    finally:
        conn.close()










def update_job_status(is_active: bool, group_names: str = None, message_body: str = None) -> None:
    """
    Updates the single Job_Status row. Used to start a job (TRUE) or clear it (FALSE).
    """
    conn = connect_db()
    cursor = conn.cursor()
    current_time = time.strftime('%Y-%m-%d %H:%M:%S')

    try:
        # If setting to FALSE (job complete), clear the recovery data fields.
        if not is_active:
            cursor.execute("""
                UPDATE Job_Status
                SET is_active = FALSE, group_names = NULL, message_body = NULL
                WHERE id = 1
            """)
        # If setting to TRUE (job started/recovered), set all fields.
        else:
            # We use INSERT OR REPLACE to guarantee the single row with ID=1 exists
            cursor.execute("""
                INSERT OR REPLACE INTO Job_Status 
                (id, is_active, group_names, message_body, start_time) 
                VALUES (?, ?, ?, ?, ?)
            """, (1, True, group_names, message_body, current_time))
        
        conn.commit()
    except Exception as e:
        print(f"Update Job Status Error: {e}")
    finally:
        conn.close()


def load_message_queue(group_names: List[str], message_body: str, is_recovery: bool = False) -> int:
    """
    Loads all numbers associated with the groups into the Message_Queue table.
    If it's a new job, it clears the Message_Queue first.
    If it's recovery, it loads based on the group names from Job_Status.
    Returns the number of messages successfully loaded.
    """
    conn = connect_db()
    cursor = conn.cursor()
    
    if not is_recovery:
        # 1. Clear any remnants of previous, incomplete jobs for a NEW job
        cursor.execute("DELETE FROM Message_Queue") 
    
    # 2. Get all required data (number_id, phone_number, group_id)
    # The previous logic for query_numbers_by_groups is retained
    number_data = query_numbers_by_groups(group_names)
    
    if not number_data:
        conn.close()
        return 0

    # 3. Prepare data for bulk insertion into the Message_Queue
    # messages_to_insert = [
    #     # For a new job, status is PENDING. Recovery logic will check existing statuses later.
    #     (number_id, group_id, message_body, 'PENDING')
    #     for number_id, phone_number, group_id in number_data
    # ]
    
    # # 4. Insert PENDING messages, using INSERT OR IGNORE to handle recovery case
    # # where the queue might have been partially populated before crash.
    # cursor.executemany(
    #     """
    #     INSERT OR IGNORE INTO Message_Queue (number_id, group_id, message_body, status) 
    #     VALUES (?, ?, ?, ?)
    #     """, 
    #     messages_to_insert
    # )



    messages_to_insert = [
        (number_id, message_body, 'PENDING')
        for number_id, phone_number, _ in number_data   # ignore group_id
    ]
    
    cursor.executemany(
        "INSERT OR IGNORE INTO Message_Queue (number_id, message_body, status) VALUES (?, ?, ?)",
        messages_to_insert
    )

    conn.commit()
    loaded_count = cursor.rowcount
    conn.close()
    return loaded_count


# ------------------------------------------------------------------
# --- Remaining Core DB Functions (Retained from original code) ---
# ------------------------------------------------------------------

def get_pending_messages(batch_size: int) -> List[Dict[str, Any]]:
    conn = connect_db()
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
            SELECT 
                mq.id,
                mq.number_id,
                pn.phone_number,
                mq.message_body,
                mq.attempt_count
            FROM 
                Message_Queue AS mq
            INNER JOIN 
                Phone_Numbers AS pn ON mq.number_id = pn.id
            WHERE 
                mq.status IN ('PENDING', 'FAILED')
                AND mq.attempt_count < ?
            ORDER BY 
                mq.attempt_count ASC, mq.timestamp ASC 
            LIMIT ?
        """, (MAX_ATTEMPTS, batch_size))
        
        columns = [col[0] for col in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return results
    except Exception as e:
        print(f"Get Pending Error: {e}")
        return []
    finally:
        conn.close()

    

def update_message_status(message_id: int, status: str, details: str) -> str:
    conn = connect_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT attempt_count FROM Message_Queue WHERE id = ?", (message_id,))
        row = cursor.fetchone()
        if row:
            new_attempt = row[0] + 1
            final_status = status.upper()
            if final_status == 'FAILED' and new_attempt >= MAX_ATTEMPTS:
                final_status = 'FAILED_PERMANENT'
            cursor.execute("""
                UPDATE Message_Queue 
                SET 
                    status = ?,
                    attempt_count = ?,
                    timestamp = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (final_status, new_attempt, message_id))
            conn.commit()
            return final_status
        return status.upper()
    except Exception as e:
        print(f"Update Status Error for ID {message_id}: {e}")
        return status.upper()
    finally:
        conn.close()



def check_pending_count() -> int:
    conn = connect_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT COUNT(*) 
            FROM Message_Queue 
            WHERE status IN ('PENDING', 'FAILED')
              AND attempt_count < ?
        """, (MAX_ATTEMPTS,))
        count = cursor.fetchone()[0]
        return count
    except Exception as e:
        print(f"Check Pending Count Error: {e}")
        return -1
    finally:
        conn.close()



def clear_message_queue() -> int:
    """
    Clears the entire Message_Queue table. 
    NOTE: In the new flow, this should only be called if a job is fully complete.
    """
    conn = connect_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute("DELETE FROM Message_Queue")
        rows_deleted = cursor.rowcount
        conn.commit()
        return rows_deleted
    except Exception as e:
        print(f"Clear Queue Error: {e}")
        return 0
    finally:
        conn.close()


def delete_number_and_associations(phone_number: str) -> str:
    # (Function body remains unchanged)
    conn = connect_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM Phone_Numbers WHERE phone_number = ?", (phone_number,))
        number_record = cursor.fetchone()
        
        if not number_record:
            return f"Number {phone_number} not found in database. No action taken."
        
        number_id = number_record[0]
        
        # Deleting from Phone_Numbers cascades the delete to Group_Association and Message_Queue
        cursor.execute("DELETE FROM Phone_Numbers WHERE id = ?", (number_id,))
        
        conn.commit()
        return f"Successfully deleted number {phone_number} and all associated group/message data."

    except Exception as e:
        conn.rollback()
        return f"Database Error during deletion: {e}"
    finally:
        conn.close()


# ------------------------------------------------------------------
# --- MAIN EXECUTION FLOW (for testing) ---
# ------------------------------------------------------------------

if __name__ == '__main__':
    setup_database()
    print("\n--- TEST: DB ENTRY/UPDATE ---")
    
    # ... (Test data creation retained) ...
    save_or_update_number_and_group("+25677111222", "Church Group")
    save_or_update_number_and_group("+25677111222", "University AAA")
    save_or_update_number_and_group("+25670222333", "Market Group")
    save_or_update_number_and_group("+25670222333", "University AAA")

    # --- 7. TEST: NEW JOB STATUS FUNCTIONS ---
    print("\n--- TEST: NEW JOB STATUS FUNCTIONS (Recovery) ---")
    
    # A. Simulate job start
    groups_str = "University AAA, Market Group"
    msg_body = "Urgent Test Message"
    update_job_status(True, groups_str, msg_body)
    print(f"Set Job Status to ACTIVE with groups: {groups_str}")
    
    # B. Check status (should be active)
    status = get_job_status()
    print(f"Current Job Status: {status}") # Expected: {'is_active': 1, 'group_names': '...', 'message_body': '...'}
    
    # C. Simulate queue load (The new load_message_queue logic is simplified here)
    loaded_count = load_message_queue(["University AAA", "Market Group"], msg_body, is_recovery=False)
    print(f"Loaded {loaded_count} messages for the new job.") # Expected: 3 unique numbers
    
    # D. Simulate job failure (leave queue non-empty)
    pending = check_pending_count()
    print(f"Pending Count before mock failure: {pending}")

    # E. Simulate successful completion and cleanup (Clearing status *and* queue)
    if pending == 3: # If all messages were mock-sent/processed
        clear_message_queue()
        update_job_status(False)
        print("MOCK: Job complete. Cleared queue and Job Status.")

    status = get_job_status()
    print(f"Job Status after completion: {status}") # Expected: {'is_active': 0, 'group_names': None, 'message_body': None}

    # --- 8. TEST: DELETE FUNCTIONALITY ---
    # (Retained cleanup test)
    print("\n--- TEST: DELETE FUNCTIONALITY ---")
    print(delete_number_and_associations("+25677111222"))
    uni_numbers_after_delete = query_numbers_by_groups(["University AAA"])
    print(f"Query 'University AAA' (1 expected after delete): {len(uni_numbers_after_delete)} numbers found.")