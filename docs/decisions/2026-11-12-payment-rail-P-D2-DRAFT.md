# P-D2 payment rail — evidence and decision draft

Prepared 2026-11-12 for Week 4. **PROPOSED, not decided. Billing remains disabled.**
No account, company, tax form, payment method or customer transaction was created.
This is engineering research, not legal, tax or banking advice.

## Verified public evidence

| Option | What the official source establishes | What it does not establish |
|---|---|---|
| Stripe direct | Stripe's global-availability page lists supported business countries; Pakistan is absent from that captured list. A supported-country business can sell internationally. | Pakistan-based sole proprietor/company eligibility; do not infer approval from customer-country support |
| Stripe Atlas | Atlas forms Delaware C corporations, LLCs and subsidiaries, obtains an EIN and supports banking/payments. Signup requires founder/ownership/officer information. Atlas docs warn about IP assignment, 83(b), entity choice and restricted businesses. | That this founder/business will pass underwriting; total cost; Pakistan/US tax and reporting obligations; whether an LLC or C corp is appropriate |
| Paddle | Official developer page describes selling to buyers in 200+ countries and blocking unsupported buyer countries. | Pakistan-based seller onboarding/payout eligibility. Buyer-country lists are not seller-country approval |
| FastSpring | Official developer tax page includes Pakistan in a seller W-8 treaty table for a specific downloadable-subscription-software withholding case; it says most SaaS does not qualify for that withholding. | General Pakistan seller onboarding, payout support, product approval, fees or tax treatment for this business |

Sources captured through agent-reach's Jina web reader on 2026-11-12:

- https://stripe.com/global
- https://stripe.com/atlas
- https://docs.stripe.com/atlas/signup
- https://developer.paddle.com/concepts/sell/supported-countries-locales
- https://developer.fastspring.com/docs/us-tax-withholding-for-downloadable-subscription-software

The guessed `/supported-countries` marketing URLs for Paddle and FastSpring returned 404;
they are not evidence. Search snippets were used only to locate the official pages above.
Recheck the live pages and obtain written provider confirmation before a decision.

## Current implementation

- `section1/src/harness_identity/billing.py`: flag-off state, local plan/quota display,
  admin plan updates. `HARNESS_BILLING` is read per request.
- `section1/src/harness_identity/stripe_adapter.py`: Stripe checkout and signed webhook
  adapter, injected only when `HARNESS_BILLING=stripe` and `STRIPE_API_KEY` is present
  (`section1/src/harness_identity/app.py:220–243`).
- No Paddle or FastSpring adapter was found. Selecting either requires a separately scoped
  integration, webhook/idempotency review, reconciliation and independent tests.
- Existing Stripe tests are not a live-provider drill. The legal register currently says
  Stripe is not used; enabling it requires updating subprocessors and customer consent.

## Options for the owner

1. **Unpaid supervised pilot / counsel-approved manual invoice.** Lowest integration work;
   still needs contracting entity, tax, refund and liability decisions. Does not prove a
   scalable payment rail or collected MRR.
2. **Paddle or FastSpring merchant of record.** Obtain written seller eligibility, supported
   payout route, product approval, fees, reserve/hold policy, tax treatment and DPA terms.
   Then scope the adapter. Do not assume MoR removes every tax or compliance obligation.
3. **US entity + Stripe.** Obtain cross-border legal/tax advice before formation. Review
   Atlas pricing, restricted categories, IP assignment, founder equity/83(b), registered
   agent, Delaware and federal filings, Pakistan obligations, banking/underwriting and
   ongoing costs. The existing adapter reduces integration work but does not remove these gates.

## Recommendation until evidence arrives

Keep billing off. Ask Paddle and FastSpring for written Pakistan-seller confirmation and
ask qualified counsel to compare that with the US-entity route. Do not form a company or
submit provider applications merely to unblock a demo. A signed unpaid pilot can proceed
only after the separate legal/security gates; it must not be reported as revenue.

## Required decision record

- Owner: ______
- Date: ______
- Chosen route: ______
- Provider/entity eligibility evidence: ______
- Counsel/tax adviser and written advice: ______
- Fees, payout route, reserves, refunds and tax responsibilities: ______
- Integration/test plan and rollout approval: ______
- Subprocessor/DPA/SOW updates approved: ______

Until these are recorded, P-D2 remains open and no payment data may be collected.
