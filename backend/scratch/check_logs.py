import sqlite3

def check_audit_logs():
    conn = sqlite3.connect('invoices_v3.db')
    cur = conn.cursor()
    
    cur.execute("SELECT id, filename, processing_time_ms, status FROM invoices;")
    invoices = cur.fetchall()
    print("Invoices:")
    for inv in invoices:
        print(f"  ID: {inv[0]}, File: {inv[1]}, Time: {inv[2]}ms, Status: {inv[3]}")
        
        # Get audit logs for this invoice
        cur.execute("SELECT timestamp, action, user, reason FROM audit_logs WHERE invoice_id = ? ORDER BY timestamp ASC;", (inv[0],))
        logs = cur.fetchall()
        print("  Audit Logs:")
        for log in logs:
            action_safe = str(log[1]).replace("→", "->")
            reason_safe = str(log[3]).replace("→", "->") if log[3] else None
            print(f"    {log[0]} | {action_safe} | User: {log[2]} | Reason: {reason_safe}")
        print()
    
    conn.close()

if __name__ == "__main__":
    check_audit_logs()
