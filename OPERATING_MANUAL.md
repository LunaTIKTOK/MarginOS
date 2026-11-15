# MarginOS Operating Manual (for a non-programmer)

## What MarginOS is

MarginOS checks the invoices your suppliers send you against what you
actually ordered (purchase orders) and what you agreed to pay (supplier
agreements). When an invoice charges more than agreed, bills more units than
ordered, repeats an invoice, or gets the tax wrong, MarginOS flags it as an
**exception** — with the exact arithmetic shown, so you can check it yourself.

## The three money numbers (do not confuse them)

- **Identified exposure (potential):** what the flags add up to. This is
  *potential* leakage — not money saved, not money owed to you.
- **Verified recoverable:** amounts *you* have checked and confirmed are real.
- **Realized savings:** money actually recovered or credited, after you mark
  an exception resolved. This is the only number that means cash back.

MarginOS never counts a flag as savings on its own.

## Daily use

1. **Upload purchase orders first** (Upload → Purchase order, CSV or
   spreadsheet). Then **supplier agreements / price lists** (Upload →
   Supplier agreement). Then **invoices** (Upload → Supplier invoice, PDF,
   CSV, or spreadsheet). If you have receiving records, upload those too
   (Goods receipt) — received quantities then decide quantity disputes.
2. **Dashboard** shows the three money numbers, leakage by type, and the
   newest exceptions.
3. **Exceptions:** open one. Read the explanation and the **Evidence** box —
   it lists every number the figure was computed from. If the flag is right,
   enter the real recoverable amount and click **Verify amount**. If it is
   wrong, dismiss it with a reason. When the supplier credits you, click
   **Mark recovered** — only then does it count as realized savings.
4. **Invoices flagged "Needs review":** the computer could not read the
   document clearly. Check the lines against the original, then click
   **Confirm extraction & reconcile**. MarginOS never guesses from an unclear
   document.
5. **Reports:** download the exceptions list (CSV) or the reconciliation
   report (PDF) for your records or your accountant.

## File formats

CSV / spreadsheet columns (headers are flexible — "vendor" works for
"supplier", "PO" for "po_number", etc.):
- Invoice: supplier, invoice_number, date, po_number, sku, description,
  qty, unit_price, tax_rate
- Purchase order: supplier, po_number, date, sku, qty, unit_price
- Agreement: supplier, sku, description, agreed_price, valid_from
- Receipt: po_number, sku, qty (received), date

PDF invoices must have a text layer (a normal emailed PDF, not a photo/scan
of paper). Scanned PDFs are flagged, not read.

## Billing

Billing is off until Stripe test keys are set up. When it is on, it uses
Stripe's test mode (test cards only — no real charge). Your subscription
status shows on the Billing page.

## Backup

Reports → "Download full data backup (JSON)" saves everything in your
organization. Keep a copy off the computer. If data ever needs restoring,
send the backup file to support — restore is a one-command operation
(documented in DEPLOYMENT.md).

## Getting help

Everything MarginOS does is in the **Audit Log** (who did what, when).
If a number looks wrong, open the exception and read its Evidence box —
every figure can be re-checked with a calculator.
