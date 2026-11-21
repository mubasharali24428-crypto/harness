# W33 decision briefs for the people-owned rows (2026-11-05)

**These are drafts for the owner. Every "Proposal" is a suggestion to accept, change or reject;
none of it is a decision, nothing here is signed, and no row in the readiness pack was ticked
because of this file.** Evidence is cited by path; facts I could not confirm are said to be
unconfirmed. Source checklist: `docs/m2-w20-plan.md`; refilled copy:
`docs/reviews/w33-readiness-pack-2026-11-05.md`.

## Correction to the readiness pack

The pack says "W17 owner's SLOs ... (no owner SLOs)". That is wrong in part:
`docs/runbooks/slo-and-capacity.md` records three objectives **adopted as pilot objectives on
2026-10-31 by the owner, with a review on 2026-11-30**. What is still missing is measurement (no
30-day window), a paging destination, and a re-baseline on the release candidate. The pack row is
corrected in the same commit as this file.

---

## 1. SLOs and capacity (W17)

* **Question:** are the adopted pilot objectives (API availability ≥99.5% per 30 days; control-plane
  p95 ≤250 ms; event freshness ≥99% within 5 s) still the ones to measure, and when does the
  30-day window start?
* **In hand:** the objectives and their review date (`slo-and-capacity.md`); instruments for
  freshness and backup age; a dashboard definition; W25/W26 benchmark runs, n≤5 per cell
  (`STATUS.md`, "W25/W26 benchmark run").
* **Missing:** a 30-day window; durable time-series retention; the capacity envelope on the
  release candidate (the published baseline is a dry-engine, single-replica run).
* **Proposal:** keep the three objectives; start the 30-day window on the date the pilot deployment
  is declared; treat 2026-11-30 as the review, not as attainment.
* **Signer:** owner. Decision: ______ Date: ______

## 2. Restore drill on the real deployment, and the scheduled restore (W15)

* **Question:** who runs a restore of the real deployment into a scratch volume, and when?
* **In hand:** scripted drills against MinIO and scratch Postgres (`docs/runbooks/backup-and-pitr.md`,
  `scripts/pitr_drill.sh`); a verified rollback restore path on scratch Postgres on 2026-11-05;
  the live dump copy at `~/.harness-backups/live-before-0073-0075.dump`.
* **Missing:** a restore of the real deployment; whether the scheduled monthly restore
  (next run was 2026-11-01T12:00Z) actually ran — **unconfirmed**; the earlier trigger hit an
  account credit limit.
* **Proposal:** one owner-witnessed restore into a new volume with `deploy/backup/restore.sh` /
  `store-restore.sh`, row counts compared, result recorded in `STATUS.md`; check the scheduled run
  history in the same sitting.
* **Signer:** owner (witness). Date done: ______

## 3. On-call, backup monitoring and status ownership (W17)

* **Question:** which named people receive pages, and where do they go?
* **In hand:** alert rules and a delivery drill with a synthetic page to a local sink
  (`scripts/e2e/w17_alert_delivery_drill.py`, `docs/runbooks/alerting.md`).
* **Missing:** a real paging destination (the running email receiver points at a catcher that is not
  running), named primary and backup, a status-page owner.
* **Proposal:** name the owner as primary until a second person exists; choose one destination
  (email or chat webhook), configure Alertmanager, and fire one real test page; record the names
  in `alerting.md`.
* **Signer:** owner. Primary: ______ Backup: ______ Destination: ______

## 4. W13 bring-your-own runner proven on a real remote host

* **Question:** is a second machine available for a runner, and who operates it?
* **In hand:** local runner drills pass (`docs/m2-w13-plan.md`, `deploy/runners/`).
* **Missing:** any run from a host that is not this machine; the roadmap names this as an exit
  criterion. This is a product feature that the plan itself calls not built for remote use.
* **Proposal:** run the existing drill on one other machine you control (a second laptop or a small
  VM), over the network path a customer would use; or narrow the claim to "local runner only" in
  public text.
* **Signer:** owner. Machine: ______ Date: ______

## 5. Package publish (PyPI / npm)

* **Question:** are any packages published at all, from which account, with which credentials?
* **In hand:** packages build locally; harness is private by memory note and public copy is limited
  to one vague line.
* **Missing:** a publish decision and credentials. I will not handle publish credentials.
* **Proposal:** decide "not publishing in the pilot" and record it, which closes the row honestly;
  revisit when there is a public release.
* **Signer:** owner. Decision: ______

## 6. Pricing and invoicing terms (counsel)

* **Question:** who is the contracting entity and what are the pricing, refund, tax and liability
  terms?
* **In hand:** `TERMS.md` and `PRIVACY.md` with `[PLACEHOLDER: counsel review]` markers
  (`docs/backlog.md`, "Legal: counsel-review placeholders"): entity, liability model, pricing terms,
  liability cap, termination, amendment, data controller. Stripe is undrilled by decision (not
  available to the operator in Pakistan, `docs/runbooks/external-validation.md`).
* **Missing:** counsel's review. An agent cannot close these.
* **Proposal:** for a no-charge pilot, record "no commercial operation until counsel review" and
  leave the placeholders; engage counsel before any invoice.
* **Signer:** counsel and owner. Counsel: ______ Date: ______

## 7. SSO/OIDC pilot need-by (R-45)

* **Question:** does the pilot need SSO, and by when?
* **In hand:** shipped and tested; drilled against Keycloak 26.1.5 on 2026-10-25
  (`external-validation.md`).
* **Missing:** a need-by date; a drill against the customer's identity provider.
* **Proposal:** state "SSO needed before pilot customer X, by DATE" or "not in the pilot".
* **Signer:** owner. Decision: ______

## 8. External security review

* **Question:** is a review scheduled, with whom, over what scope?
* **In hand:** internal confinement drills (Seatbelt, Landlock in the worker image, container
  mounts), the M03 and W27 drills, the capability review.
* **Missing:** anyone outside the build. No reviewer is named.
* **Proposal:** scope it to the command gate, scope mounts, tenant isolation and the worker daemon
  (the pieces with drills), give the reviewer `docs/reviews/` and the evidence directory, and
  schedule before any customer data enters.
* **Signer:** owner. Reviewer: ______ Date: ______

## 9. M01 native-tool confinement ruling

* **Question:** is M01 closed?
* **In hand:** `docs/backlog.md` lists the three M01 items as closed with evidence: scope enforced by
  mounts and the OS write set, Landlock measured in the worker image (linuxkit 6.12), native
  write measured (edit not separately driven). `native-tool-confinement` passes in the eval set.
* **Open limits recorded there:** a scope that names a file is not writable through the native tool;
  `.git` is read-only unless in scope; a bare Linux host run of the spike is still worth having.
* **Proposal for the reviewer:** rule closed for scope-path writes, with the two limits and the
  un-driven native edit stated; or ask for the native edit drill first (cheap, no model needed).
* **Signer:** independent reviewer. Ruling: ______

## 10. W18 release-candidate review by someone other than the author

* **Question:** who reviews, and from what?
* **In hand:** the readiness pack and the evidence it cites; `run_eval.py --verify-only` for image
  hashes; the capability review and the same-task comparison.
* **Missing:** the reviewer, and their re-derivation of the pack's rows.
* **Proposal:** give the reviewer the pack's hand-off section; they re-derive the table from the
  JSON, not from the prose.
* **Signer:** reviewer. Name: ______ Date: ______

## 11. Hermes rights

* **Question:** none to answer now.
* **Status:** the owner decided on 2026-10-31 that Hermes and `.hms` are out of scope
  (`docs/decisions/2026-10-31-hx-language-surface.md`). The launch claim should say Hx, not Hermes.
* **Proposal:** tick this row as "narrowed by decision" in the pack when the owner confirms the
  public text.
* **Signer:** owner. Confirmed: ______
