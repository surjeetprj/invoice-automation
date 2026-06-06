import sqlite3
import json
import logging
import time
from services.ai_parser import parse_invoice

# Configure logging to print to stdout so we see graph validation logs
logging.basicConfig(level=logging.INFO)

def run_test():
    conn = sqlite3.connect('invoices_v3.db')
    cur = conn.cursor()
    cur.execute("SELECT id, filename, raw_markdown FROM invoices ORDER BY id DESC LIMIT 1;")
    row = cur.fetchone()
    if not row:
        print("No invoices found in database to test.")
        conn.close()
        return
        
    inv_id, filename, raw_markdown = row
    print(f"Testing LangGraph parse_invoice on Invoice #{inv_id} ({filename})...")
    
    start_time = time.perf_counter()
    result = parse_invoice(raw_markdown)
    elapsed = time.perf_counter() - start_time
    
    print(f"\nCompleted in {elapsed:.2f} seconds.")
    print("Result Keys:", list(result.keys()))
    print("Confidence Score:", result.get("confidence_score"))
    
    conn.close()

if __name__ == "__main__":
    run_test()
