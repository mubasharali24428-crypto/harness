# Counsel open questions

**Status: unanswered.** Each item needs a decision from qualified counsel and a
recorded answer (date, decision, who decided). Engineering cannot close these.

## 1. Contracting structure

1. Which legal entity contracts with the pilot customer, and in which
   jurisdiction? Is the operator an individual, a sole proprietorship, or a
   registered company? (This determines everything from liability to tax.)
2. Should the pilot use the existing `TERMS.md` + `PRIVACY.md`, or a separate
   pilot agreement that references the DPA?

## 2. Controller / processor mapping

3. Is the provider a processor for all customer data, or a controller for
   account administration, security logging and product telemetry?
4. When the customer connects its own repository and model key, does the
   provider remain a processor for the resulting copies?
5. Are model providers subprocessors, or independent controllers under their
   own terms? This changes the consent and transfer analysis.

## 3. International transfers

6. Which transfer mechanism applies to each subprocessor (SCC module, UK
   addendum, adequacy)? Is a transfer impact assessment required?
7. Sending prompts and code to DeepSeek (China) and OpenRouter (US) — is that
   permissible for the intended customer, and what must the SOW say?
8. Does the lack of enforced tenant residency need to be a headline service
   limitation, or is a disclosure in the DPA annex enough?

## 4. Data protection operations

9. What breach-notification clock applies, and does it run from discovery or
   confirmation?
10. Is a DPIA required for the pilot (large-scale code processing, automated
    planning, model calls)? Who owns it?
11. What are lawful retention defaults for a pilot: 30/365 days as shipped, or
    shorter/longer? Must the operator enable the retention sidecars as a
    contractual commitment?
12. How should deletion interact with backups that expire later, and with run
    holds that preserve evidence?
13. Is the account-erasure behavior (pseudonymisation rather than deletion,
    authored text retained under an opaque id) defensible under the applicable
    law?

## 5. Security and liability

14. What security warranties may the operator give, given there is no SOC 2,
    no pen test, and known limitations (backups unencrypted, macOS container
    writes unsupported, residency not enforced)?
15. What liability cap, carve-outs and data-protection indemnity are
    appropriate for a paid or unpaid pilot?
16. Is cyber-liability or professional-indemnity insurance required before the
    first customer?
17. How should open-source licenses of the stack and of model outputs be
    represented to the customer? (See `LICENSE`.)

## 6. Commercial and pilot terms

18. Can the pilot be unpaid, and does that change consumer-protection or
    liability exposure in the target market?
19. If Stripe is unavailable, how should invoices, taxes and refunds be
    handled — manual invoice, local payment rail, or free pilot?
20. What are the acceptance criteria and the exit/stop conditions the customer
    must sign (the SOW checklist has a draft)?

## 7. Claims discipline

21. Approve the exact wording for public claims: "private design-partner pilot",
    "no certification", "not production-certified", "native Linux required for
    container sandbox". Engineering will not publish a stronger claim.
