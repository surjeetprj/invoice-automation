import os
import sys
import time
import json
import sqlite3
from pydantic import BaseModel
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from schemas import InvoiceData

def test_performance():
    # Load .env file
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
    print("API Key loaded successfully:", bool(api_key))
    
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash", 
        google_api_key=api_key,
        temperature=0.0
    )
    
    print("Initializing structured LLM...")
    # ChatGoogleGenerativeAI with_structured_output natively binds schemas to Gemini's structured output API
    structured_llm = llm.with_structured_output(InvoiceData)
    
    system_prompt = """You are an expert Indian GST invoice processing agent.
Extract all details from the layout-preserving text into the requested schema.
Be concise. Never hallucinate."""

    messages = [
        ("system", system_prompt),
        ("human", f"Invoice text:\n\n{raw_markdown}")
    ]
    
    print("Calling Gemini 2.5 Flash with native structured output...")
    start = time.perf_counter()
    try:
        result = structured_llm.invoke(messages)
        elapsed = time.perf_counter() - start
        print(f"Success! Took {elapsed:.2f} seconds.")
        print("Result type:", type(result))
        if isinstance(result, InvoiceData):
            print("Successfully parsed into Pydantic model.")
            print("Invoice No:", result.invoice_number)
            print("Line items count:", len(result.line_items))
    except Exception as e:
        print("Structured output error:", e)

if __name__ == "__main__":
    test_performance()
