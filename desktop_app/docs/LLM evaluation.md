The best way is to evaluate the LLM output against a **human-verified ground truth dataset**, not directly against the PDF/image every time.

For your invoice app, I’d use this evaluation structure:

**1. Create Ground Truth Per Invoice**
For each PDF/image invoice, store a verified JSON file:

```text
invoice_file.pdf
ground_truth.json
```

Ground truth should include fields your ERP export depends on:

```json
{
  "invoice_number": "SKEC2026042908",
  "date": "29-05-2026",
  "vendor_name": "SKE CLOUD PRIVATE LIMITED",
  "vendor_gstin": "07ABJCS6385D1Z1",
  "customer_gstin": "09AAOCS7654P3Z5",
  "total_taxable_amount": 21613.0,
  "total_igst": 3890.34,
  "total_amount": 25503.34,
  "line_items": [
    {
      "item_name": "VPS Custom Configuration",
      "description": "...",
      "hsn_sac": "997315",
      "quantity": 1,
      "rate": 22750,
      "discount": 1137,
      "taxable_value": 21613,
      "igst_rate": 18,
      "igst_amount": 3890.34
    }
  ]
}
```

**2. Compare Field By Field**
Use deterministic comparison rules:

- Exact match:
  - invoice number
  - GSTIN
  - PAN
  - HSN/SAC
  - IFSC
- Normalized string match:
  - vendor name
  - customer name
  - addresses
  - item names
- Date-normalized match:
  - `29/05/2026`, `29-05-2026`, `2026-05-29`
- Numeric tolerance match:
  - totals, tax, discount, taxable value
  - example tolerance: `±0.01` or `±1.00` depending on invoice rounding

**3. Score By Business Importance**
Do not treat all fields equally.

Example weights:

```text
Invoice number: 10
Vendor GSTIN: 10
Customer GSTIN: 10
Date: 8
Total amount: 12
Taxable amount: 12
GST amounts/rates: 15
Line items: 20
Bank details: 5
Shipping details: 3
```

This gives you a practical “ERP readiness score”, not just a generic accuracy number.

**4. Track Three Metrics**
For every invoice, calculate:

```text
Field accuracy: how many fields matched ground truth
Critical accuracy: whether ERP-critical fields are correct
Validation pass rate: whether extracted data passes your domain validation
```

For your app, the most important metric is:

```text
Can this extraction produce a correct purchase voucher?
```

**5. Use Separate Evaluation For Digital PDF vs Scanned/Image**
Evaluate these separately:

```text
digital_pdf_accuracy
scanned_pdf_accuracy
image_invoice_accuracy
```

Because they behave very differently. Digital PDFs should score higher because text/table extraction helps. Scanned/image invoices may need summary-line reconciliation.

**6. Add Regression Test Dataset**
Create a folder like:

```text
desktop_app/evaluation/
  invoices/
    ske_cloud.pdf
    relyon.pdf
    footwear_scan.pdf
  expected/
    ske_cloud.json
    relyon.json
    footwear_scan.json
  run_eval.py
```

Then run:

```powershell
python desktop_app/evaluation/run_eval.py
```

The script should:
1. Process each invoice.
2. Capture LLM result after normalization.
3. Compare with expected JSON.
4. Print pass/fail and score.
5. Save a report.

**Recommended Approach**
For this project, I’d build evaluation in this order:

1. Start with 20 real invoices.
2. Manually prepare ground-truth JSON.
3. Compare normalized extraction result against ground truth.
4. Score ERP-critical fields separately.
5. Add this as a repeatable evaluation command.
6. Use it before changing prompts, models, parser logic, or normalization.

That gives you a reliable way to answer: “Did the new prompt/model/code actually improve extraction?”