import asyncio
import sys
import time
from pathlib import Path
from sqlalchemy.future import select

# Add root directory to python path
sys.path.append(".")

from database import AsyncSessionLocal, init_db
from models import Invoice
from services.processor import _run_pipeline

async def test_e2e():
    print("Initializing Database...")
    await init_db()
    
    async with AsyncSessionLocal() as db:
        # Find the last invoice
        result = await db.execute(select(Invoice).order_by(Invoice.id.desc()).limit(1))
        invoice = result.scalar_one_or_none()
        
        if not invoice:
            print("No invoices found in database. Please run migrations/seed first.")
            return
            
        uploads_dir = Path("uploads")
        file_path = uploads_dir / invoice.filename
        
        if not file_path.exists():
            print(f"Test file not found at: {file_path}")
            return
            
        print(f"Running end-to-end pipeline for: {invoice.filename}")
        start_time = time.perf_counter()
        
        # Execute the pipeline
        await _run_pipeline(invoice, file_path, db)
        
        elapsed = time.perf_counter() - start_time
        print(f"\nPipeline execution completed in {elapsed:.2f} seconds.")
        print(f"Invoice Number: {invoice.invoice_number_extracted}")
        print(f"Confidence Score: {invoice.confidence_score}")
        print(f"Status: {invoice.status}")
        print(f"Validation Result: {invoice.validation_result}")

if __name__ == "__main__":
    asyncio.run(test_e2e())
