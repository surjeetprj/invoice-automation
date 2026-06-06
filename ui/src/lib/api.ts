const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

// Replace with a real API key in production
const API_HEADERS = {
  "X-API-Key": "poc-secret-key-change-me",
  "Content-Type": "application/json",
};

export const api = {
  getStats: async () => {
    const res = await fetch(`${API_BASE_URL}/stats`, { headers: API_HEADERS });
    if (!res.ok) throw new Error("Failed to fetch stats");
    return res.json();
  },

  getInvoices: async (skip = 0, limit = 50) => {
    const res = await fetch(`${API_BASE_URL}/invoices?skip=${skip}&limit=${limit}`, { headers: API_HEADERS });
    if (!res.ok) throw new Error("Failed to fetch invoices");
    return res.json();
  },

  getInvoice: async (id: string | number) => {
    const res = await fetch(`${API_BASE_URL}/invoices/${id}`, { headers: API_HEADERS });
    if (!res.ok) throw new Error("Failed to fetch invoice detail");
    return res.json();
  },

  getInvoiceAuditLog: async (id: string | number) => {
    const res = await fetch(`${API_BASE_URL}/invoices/${id}/audit-log`, { headers: API_HEADERS });
    if (!res.ok) throw new Error("Failed to fetch audit log");
    return res.json();
  },

  processInvoice: async (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    
    // For file uploads, we do NOT set Content-Type header manually; 
    // fetch will set it to multipart/form-data with the correct boundary automatically.
    const res = await fetch(`${API_BASE_URL}/process-invoice`, {
      method: "POST",
      headers: { "X-API-Key": "poc-secret-key-change-me" },
      body: formData,
    });
    
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || "Upload failed");
    }
    return res.json();
  },

  submitReview: async (id: string | number, payload: { decision: string; corrections?: any; reviewer?: string; rejection_reason?: string }) => {
    const res = await fetch(`${API_BASE_URL}/invoices/${id}/review`, {
      method: "POST",
      headers: API_HEADERS,
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || "Failed to submit review");
    }
    return res.json();
  },

  reprocessInvoice: async (id: string | number) => {
    const res = await fetch(`${API_BASE_URL}/invoices/${id}/reprocess`, {
      method: "POST",
      headers: API_HEADERS,
    });
    if (!res.ok) throw new Error("Failed to reprocess invoice");
    return res.json();
  },

  // Utility method to get the URL of the original file for the iframe
  getFileUrl: (id: string | number) => {
    return `${API_BASE_URL}/invoices/${id}/file`;
  }
};
