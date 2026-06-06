import os
import sys
import time
import sqlite3
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from schemas import InvoiceData

def compare_models():
    load_dotenv()
    conn = sqlite3.connect('invoices_v3.db')
    cur = conn.cursor()
    cur.execute("SELECT raw_markdown FROM invoices ORDER BY id DESC LIMIT 1;")
    row = cur.fetchone()
    if not row:
        print("No invoices to test.")
        conn.close()
        return
    raw_markdown = row[0]
    conn.close()

    api_key = os.getenv("GOOGLE_API_KEY")
    
    models = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-flash-latest"]
    
    system_prompt = """You are an expert Indian GST invoice processing agent.
Extract all details from the layout-preserving text into the requested schema.
Be concise. Never hallucinate."""

    messages = [
        ("system", system_prompt),
        ("human", f"Invoice text:\n\n{raw_markdown}")
    ]

    for model_name in models:
        print(f"\n--- Testing {model_name} ---")
        try:
            llm = ChatGoogleGenerativeAI(
                model=model_name, 
                google_api_key=api_key,
                temperature=0.0
            )
            structured_llm = llm.with_structured_output(InvoiceData)
            
            # Warm up connection
            print("Warming up connection...")
            try:
                structured_llm.invoke(messages)
            except Exception as e:
                print(f"Warm up failed: {e}")
                continue
            
            # Measure speed
            print("Running timed request...")
            start = time.perf_counter()
            result = structured_llm.invoke(messages)
            elapsed = time.perf_counter() - start
            print(f"Time taken: {elapsed:.2f} seconds.")
            print("Invoice No extracted:", result.invoice_number)
        except Exception as e:
            print(f"Error testing {model_name}:", e)

if __name__ == "__main__":
    compare_models()
