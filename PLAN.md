# PIXORA AI — development plan

## Product decisions
- Telegram Mini App only; no separate registration or password.
- Telegram identity is verified server-side using Mini App initData.
- Two free completed processing jobs per user, shared across both modes.
- Modes: general photo editing and marketplace product-card creation.
- A pack of five product-card slides counts as one job.
- Proposed subscriptions: 50 RUB / 1 month, 100 RUB / 3 months, 200 RUB / 6 months.
- Payments for digital services inside Telegram must use Telegram Stars; verify current Stars pricing and payment flow before launch.

## Delivery phases
1. UX prototype and responsive Mini App shell.
2. Backend, Telegram initData verification, SQLite user/job ledger.
3. Basic deterministic photo tools using free libraries.
4. Product-card workflow: upload, seller-provided facts, template-based slides. Never invent product specifications.
5. Connect and test an image-generation model only when compute is available; do not pretend a filter is generative AI.
6. Stars payments, subscription entitlement checks, admin controls.
7. Security, abuse limits, file cleanup, deployment and end-to-end tests.

## Zero-budget constraints
Free hosting may sleep, limit CPU/RAM, or impose storage limits. Generative AI may not be reliably available at zero cost. Launch only features that can be operated reliably for free; disclose unavailable features clearly.
