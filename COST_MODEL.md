# MarginOS Cost Model (hosting + AI inference)

All figures are planning estimates for the free-tier launch, not measured
costs. Re-measure before quoting a customer.

## Hosting (Render free tier, $0/mo at launch)

| Item | Tier | Cost |
|---|---|---|
| Web service (Docker, 512 MB) | Free | $0 |
| Postgres | Free (90-day expiry on Render free tier — see note) | $0 |
| Static files / uploads | On-service disk | $0 |
| **Total hosting at launch** | | **$0/mo** |

Note: Render's free Postgres expires after 90 days. Before expiry, either
upgrade to paid Postgres ($6/mo, 1 GB) or migrate the database. Budget the
$6/mo from the first paying customer. At ~10 customers, paid tier:
web $7/mo (Starter) + Postgres $6/mo ≈ **$13/mo total hosting**.

## AI inference (v1: $0)

v1 makes **no external AI calls** — extraction and supplier-name
normalization are deterministic rules. Inference cost in v1 is $0.

If M3 adds LLM extraction (gated, optional), the expected shape is:
- One extraction call per uploaded document (~2–4k tokens in, ~1k out).
- At Groq/OpenRouter-class pricing (~$0.10–$0.50 per 1M tokens), a
  100-invoice/month customer costs roughly **$0.05–$0.25/mo** in inference.
- Cap: extraction calls are bounded per org per day; over-cap documents
  fall back to deterministic parsing (never blocked, never silently billed).

## Delivery cost per customer (hypothesis to validate)

| Item | Estimate |
|---|---|
| Hosting share (at 10 customers) | ~$1.30/mo |
| AI inference (M3, if enabled) | ~$0.25/mo |
| Support / ops (amortized) | TBD by pilot |
| **Target ceiling** | **≤$500/mo** (mandate hypothesis) |

The ≤$500/mo delivery-cost ceiling is generous against the ~$2/mo
infrastructure above; the real cost driver will be human support time,
which the pilot must measure.

## Commercial hypothesis (UNVALIDATED — to test in M4 pilot)

- Subscription: **$1,500/mo** per customer.
- Documented customer benefit: **~$4,500/mo** in recovered leakage.
- Delivery cost: **≤$500/mo** per customer.

None of these three numbers is proven. The M4 pilot must measure actual
recovered leakage per customer before the price is quoted as fact.
