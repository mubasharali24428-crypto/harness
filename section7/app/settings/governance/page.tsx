"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { BreadcrumbTrail } from "@/components/Breadcrumbs";
import { getToken, loadSession } from "@/lib/auth";
import {
  OrganizationDto,
  OrganizationMemberDto,
  PolicyChangeRequestDto,
  PolicyDocumentDto,
  decideEmergencyGrant,
  getOrganizationHistory,
  getOrganization,
  getExecutionControl,
  getPolicyDecisions,
  inviteOrganizationMember,
  listEmergencyGrants,
  listOrganizationMembers,
  listOrganizations,
  listPolicyDocuments,
  listPolicyChangeRequests,
  previewPolicy,
  requestEmergencyGrant,
  requestStepupCode,
  setExecutionControl,
  savePolicyDocument,
  decidePolicyChange,
  patchOrganization,
  updateOrganizationMember,
  verifyStepupCode,
} from "./governanceApi";

const DEFAULT_POLICY: Record<string, unknown> = {
  rules: [],
  allow_lower_grants: false,
  stepup_actions: ["policy.save", "role.change", "export", "emergency.grant"],
  stepup_max_age_seconds: 300,
  emergency_max_minutes: 240,
};

const PERMISSIONS = ["read", "comment", "steer", "approve", "administer", "export", "billing"];

export default function GovernanceSettingsPage() {
  const session = loadSession();
  const token = getToken();
  const [organizations, setOrganizations] = useState<OrganizationDto[]>([]);
  const [orgId, setOrgId] = useState("");
  const selectedOrg = useMemo(() => organizations.find((org) => org.id === orgId) ?? null, [orgId, organizations]);
  const mayAdminister = selectedOrg?.role === "owner" || selectedOrg?.role === "admin";
  const [members, setMembers] = useState<OrganizationMemberDto[]>([]);
  const [policy, setPolicy] = useState<PolicyDocumentDto | null>(null);
  const [policyChanges, setPolicyChanges] = useState<PolicyChangeRequestDto[]>([]);
  const [policyText, setPolicyText] = useState("");
  const [previewChanges, setPreviewChanges] = useState<Array<Record<string, unknown>> | null>(null);
  const [targetUserId, setTargetUserId] = useState("");
  const [history, setHistory] = useState<Array<Record<string, unknown>>>([]);
  const [decisions, setDecisions] = useState<Array<Record<string, unknown>>>([]);
  const [grants, setGrants] = useState<Array<Record<string, unknown>>>([]);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [inviteToken, setInviteToken] = useState<string | null>(null);
  const [emergencyReason, setEmergencyReason] = useState("");
  const [stepupCode, setStepupCode] = useState("");
  const [executionReason, setExecutionReason] = useState("");
  const [eventRetentionDraft, setEventRetentionDraft] = useState("");
  const [auditRetentionDraft, setAuditRetentionDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refreshOrganizations = useCallback(async () => {
    if (!token) return;
    const response = await listOrganizations(token);
    setOrganizations(response.items);
    setOrgId((current) => current || response.items[0]?.id || "");
  }, [token]);

  useEffect(() => {
    refreshOrganizations().catch((cause: unknown) => setError(String((cause as Error)?.message ?? cause)));
  }, [refreshOrganizations]);

  const refreshOrg = useCallback(async () => {
    if (!token || !orgId || !mayAdminister) return;
    setLoading(true);
    setError(null);
    try {
      const [org, execution, roster, documents, orgHistory, policyDecisions, emergency, changes] = await Promise.all([
        getOrganization(orgId, token),
        getExecutionControl(orgId, token),
        listOrganizationMembers(orgId, token),
        listPolicyDocuments(orgId, token),
        getOrganizationHistory(orgId, token),
        getPolicyDecisions(orgId, token),
        listEmergencyGrants(orgId, token),
        listPolicyChangeRequests(orgId, token),
      ]);
      setOrganizations((current) => current.map((item) => item.id === org.id ? { ...item, ...org, executionDisabled: execution.disabled } : item));
      setMembers(roster.items);
      setTargetUserId((current) => current || roster.items[0]?.userId || "");
      setHistory(orgHistory.items);
      setDecisions(policyDecisions.decisions);
      setGrants(emergency.grants);
      setPolicyChanges(changes.requests);
      const document = documents.documents.find((item) => item.level === "org" && item.scopeId === orgId);
      const nextPolicy = document ?? { level: "org", scopeId: orgId, version: 0, doc: DEFAULT_POLICY };
      setPolicy(nextPolicy);
      setPolicyText(JSON.stringify(nextPolicy.doc, null, 2));
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    } finally {
      setLoading(false);
    }
  }, [mayAdminister, orgId, token]);

  useEffect(() => {
    setPolicy(null);
    setPreviewChanges(null);
    setTargetUserId("");
    refreshOrg();
  }, [refreshOrg]);

  useEffect(() => {
    setEventRetentionDraft(selectedOrg?.eventRetentionDays?.toString() ?? "");
    setAuditRetentionDraft(selectedOrg?.auditRetentionDays?.toString() ?? "");
  }, [orgId, selectedOrg?.eventRetentionDays, selectedOrg?.auditRetentionDays]);

  const onInvite = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token || !orgId) return;
    setError(null);
    setInviteToken(null);
    try {
      const result = await inviteOrganizationMember(orgId, inviteEmail.trim(), inviteRole, token);
      const raw = result.token;
      setInviteToken(typeof raw === "string" ? raw : null);
      setNotice("Invitation created. The token is shown once; send it only to the invited mailbox.");
      setInviteEmail("");
      await refreshOrg();
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  const onRole = async (member: OrganizationMemberDto, role: string) => {
    if (!token || !orgId) return;
    setError(null);
    try {
      await updateOrganizationMember(orgId, member.userId, role, token);
      await refreshOrg();
      setNotice(`Updated ${member.email} to ${role}.`);
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  const parseDraft = (): PolicyDocumentDto | null => {
    if (!policy) return null;
    try {
      const doc = JSON.parse(policyText) as Record<string, unknown>;
      if (!doc || typeof doc !== "object" || Array.isArray(doc)) throw new Error("Policy must be a JSON object.");
      return { ...policy, doc };
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
      return null;
    }
  };

  const onPreview = async () => {
    if (!token || !orgId || !targetUserId) return;
    const draft = parseDraft();
    if (!draft) return;
    setError(null);
    setPreviewChanges(null);
    try {
      const result = await previewPolicy(orgId, draft, [targetUserId], token);
      setPreviewChanges(result.changes);
      setNotice(`Simulation complete: ${result.changes.length} permission result(s) change for the selected member.`);
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  const onSavePolicy = async () => {
    if (!token || !orgId || previewChanges === null) return;
    const draft = parseDraft();
    if (!draft) return;
    setError(null);
    try {
      const saved = await savePolicyDocument(orgId, draft, token);
      setPreviewChanges(null);
      setNotice(saved.pendingApproval
        ? "High-risk policy change submitted. A different organization admin must approve it."
        : "Policy saved with a version check and audit record.");
      await refreshOrg();
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  const onPolicyDecision = async (requestId: string, decision: "approve" | "reject") => {
    if (!token || !orgId) return;
    setError(null);
    try {
      await decidePolicyChange(orgId, requestId, decision, token);
      setNotice(`Policy change ${decision}d.`);
      await refreshOrg();
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  const onDualApprovalChange = async (enabled: boolean) => {
    if (!token || !orgId) return;
    setError(null);
    try {
      const updated = await patchOrganization(orgId, { requireDualApproval: enabled }, token);
      setOrganizations((current) => current.map((item) => item.id === orgId ? { ...item, ...updated } : item));
      setNotice(`Second-person approval ${enabled ? "required for high-risk policies" : "disabled"}.`);
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  const onSaveRetention = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token || !orgId) return;
    setError(null);
    try {
      const updated = await patchOrganization(orgId, {
        eventRetentionDays: eventRetentionDraft ? Number(eventRetentionDraft) : null,
        auditRetentionDays: auditRetentionDraft ? Number(auditRetentionDraft) : null,
      }, token);
      setOrganizations((current) => current.map((item) => item.id === orgId ? { ...item, ...updated } : item));
      setNotice("Organization retention windows saved. Blank values inherit deployment defaults.");
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  const onEmergencyRequest = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token || !orgId) return;
    setError(null);
    try {
      await requestEmergencyGrant(orgId, { permissions: ["steer"], reason: emergencyReason.trim(), durationMinutes: 30 }, token);
      setEmergencyReason("");
      setNotice("Emergency request recorded. A different eligible org admin must approve it.");
      await refreshOrg();
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  const onGrantDecision = async (grantId: string, decision: "approve" | "revoke") => {
    if (!token || !orgId) return;
    setError(null);
    try {
      await decideEmergencyGrant(orgId, grantId, decision, token);
      setNotice(`Emergency grant ${decision}d.`);
      await refreshOrg();
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  const onRequestStepup = async () => {
    if (!token || !session || !members.length) return;
    const email = members.find((member) => member.userId === session.principal.userId)?.email;
    if (!email) {
      setError("Your organization membership email is not available for re-authentication.");
      return;
    }
    setError(null);
    try {
      await requestStepupCode(email, token);
      setNotice(`Verification code sent to ${email}.`);
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  const onVerifyStepup = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token) return;
    setError(null);
    try {
      await verifyStepupCode(stepupCode.trim(), token);
      setStepupCode("");
      setNotice("This session is re-authenticated for high-impact organization changes.");
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  const onExecutionControl = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token || !orgId || !selectedOrg) return;
    setError(null);
    try {
      const disabled = !selectedOrg.executionDisabled;
      const result = await setExecutionControl(orgId, disabled, executionReason.trim(), token);
      setOrganizations((current) => current.map((item) => item.id === orgId ? { ...item, executionDisabled: result.disabled } : item));
      setExecutionReason("");
      setNotice(disabled ? "Organization execution stopped. New and queued work is blocked; active runs stop as workers observe the change." : "Organization execution re-enabled.");
    } catch (cause) {
      setError(String((cause as Error)?.message ?? cause));
    }
  };

  return (
    <AppShell active="/settings">
      <BreadcrumbTrail items={[{ label: "Settings", href: "/settings" }, { label: "Organization governance" }]} />
      <h1>Organization governance</h1>
      {!session && <p role="alert">Sign in to view organization governance.</p>}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {organizations.length === 0 && session && <p>No organizations are linked to this account.</p>}
      {organizations.length > 0 && (
        <label htmlFor="organization-select">
          Organization
          <select id="organization-select" value={orgId} onChange={(event) => setOrgId(event.target.value)}>
            {organizations.map((org) => <option key={org.id} value={org.id}>{org.name} ({org.role})</option>)}
          </select>
        </label>
      )}
      {selectedOrg && !mayAdminister && (
        <p role="status">This organization role can view basic membership only. An owner or admin can manage governance.</p>
      )}
      {selectedOrg && mayAdminister && (
        <>
          <p>Organization role: <strong>{selectedOrg.role}</strong>. Admin changes are audited and version checked.</p>
          {loading && <p role="status">Loading organization controls…</p>}

          <section className="card" style={{ maxWidth: 900, marginBottom: 16 }}>
            <h2>Emergency execution control</h2>
            <p role="status">Agent execution is {selectedOrg.executionDisabled ? "stopped" : "enabled"} for this organization.</p>
            <form onSubmit={onExecutionControl} style={{ display: "grid", gap: 8, maxWidth: 520 }}>
              <label>
                Reason
                <input value={executionReason} minLength={10} maxLength={500} required onChange={(event) => setExecutionReason(event.target.value)} />
              </label>
              <button type="submit" className={selectedOrg.executionDisabled ? "btn" : "btn danger"}>
                {selectedOrg.executionDisabled ? "Re-enable execution" : "Stop organization execution"}
              </button>
            </form>
            <form onSubmit={onVerifyStepup} style={{ display: "flex", gap: 8, marginTop: 12, maxWidth: 520 }}>
              <button type="button" className="btn" onClick={onRequestStepup}>Send verification code</button>
              <label>
                Re-authentication code
                <input value={stepupCode} maxLength={12} inputMode="numeric" onChange={(event) => setStepupCode(event.target.value)} />
              </label>
              <button type="submit" className="btn" disabled={stepupCode.trim().length < 4}>Verify</button>
            </form>
          </section>

          <section className="card" style={{ maxWidth: 900, marginBottom: 16 }}>
            <h2>Organization data retention</h2>
            <p>Set shorter retention windows than the deployment defaults. Blank fields inherit the defaults.</p>
            <form onSubmit={onSaveRetention} style={{ display: "grid", gap: 8, maxWidth: 520 }}>
              <label>Agent event retention in days
                <input type="number" min={1} max={3650} value={eventRetentionDraft} onChange={(event) => setEventRetentionDraft(event.target.value)} />
              </label>
              <label>Audit history retention in days
                <input type="number" min={1} max={3650} value={auditRetentionDraft} onChange={(event) => setAuditRetentionDraft(event.target.value)} />
              </label>
              <button type="submit" className="btn" disabled={selectedOrg.role !== "owner"}>Save retention windows</button>
            </form>
            <p>Retention changes are audited. Data residency is fixed by the deployment region; this single-region pilot cannot move or guarantee tenant-specific regional placement.</p>
          </section>

          <section className="card" style={{ maxWidth: 900, marginBottom: 16 }}>
            <h2>Membership</h2>
            <form onSubmit={onInvite}>
              <label htmlFor="invite-email">Invite email</label>
              <input id="invite-email" type="email" required value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} />
              <label htmlFor="invite-role">Organization role</label>
              <select id="invite-role" value={inviteRole} onChange={(event) => setInviteRole(event.target.value)}>
                <option value="admin">Admin</option><option value="billing_admin">Billing admin</option>
                <option value="member">Member</option><option value="guest">Guest</option>
              </select>{" "}
              <button type="submit">Create invitation</button>
            </form>
            {inviteToken && <p role="status">One-time invite token: <code>{inviteToken}</code></p>}
            <div style={{ overflowX: "auto" }}>
              <table>
                <caption>Organization members and roles</caption>
                <thead><tr><th scope="col">Email</th><th scope="col">Role</th><th scope="col">Expires</th><th scope="col">Change role</th></tr></thead>
                <tbody>{members.map((member) => (
                  <tr key={member.userId}>
                    <td>{member.email}</td><td>{member.role}</td><td>{member.expiresAt ?? "—"}</td>
                    <td><label className="sr-only" htmlFor={`role-${member.userId}`}>Role for {member.email}</label>
                      <select id={`role-${member.userId}`} value={member.role} onChange={(event) => onRole(member, event.target.value)}>
                        <option value="owner">Owner</option><option value="admin">Admin</option>
                        <option value="billing_admin">Billing admin</option><option value="member">Member</option><option value="guest">Guest</option>
                      </select>
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </section>

          <section className="card" style={{ maxWidth: 900, marginBottom: 16 }}>
            <h2>Organization policy</h2>
            <p>Simulate a draft before saving. High-risk deny rules and lower-scope grants require a different admin's approval when dual approval is enabled.</p>
            <label style={{ display: "block" }}>
              <input type="checkbox" checked={Boolean(selectedOrg.requireDualApproval)} disabled={selectedOrg.role !== "owner"} onChange={(event) => onDualApprovalChange(event.target.checked)} />
              Require a second organization admin for high-risk policy changes
            </label>
            <label htmlFor="policy-target">Simulate for member</label>
            <select id="policy-target" value={targetUserId} onChange={(event) => { setTargetUserId(event.target.value); setPreviewChanges(null); }}>
              {members.map((member) => <option key={member.userId} value={member.userId}>{member.email}</option>)}
            </select>
            <label htmlFor="policy-json">Organization policy JSON</label>
            <textarea
              id="policy-json"
              rows={14}
              spellCheck={false}
              value={policyText}
              onChange={(event) => { setPolicyText(event.target.value); setPreviewChanges(null); }}
              style={{ width: "100%", fontFamily: "monospace" }}
            />
            <button type="button" onClick={onPreview} disabled={!targetUserId}>Simulate draft</button>{" "}
            <button type="button" onClick={onSavePolicy} disabled={previewChanges === null}>Save simulated policy</button>
            {previewChanges !== null && (
              <div role="status" aria-live="polite">
                <h3>Simulation results</h3>
                <p>{previewChanges.length} permission changes for {members.find((member) => member.userId === targetUserId)?.email ?? "the selected member"}.</p>
                <ul>{previewChanges.map((item, index) => (
                  <li key={`${String(item.permission)}-${index}`}>{String(item.permission)}: {JSON.stringify(item.current)} → {JSON.stringify(item.draft)}</li>
                ))}</ul>
              </div>
            )}
            {policyChanges.length > 0 && <div>
              <h3>Pending policy approvals</h3>
              {policyChanges.map((change) => <article key={change.id} style={{ borderTop: "1px solid var(--border, #ccc)", padding: 8 }}>
                <p>Requested by {members.find((member) => member.userId === change.requestedBy)?.email ?? change.requestedBy}; scope {change.level}:{change.scopeId}</p>
                <pre style={{ whiteSpace: "pre-wrap", maxHeight: 180, overflow: "auto" }}>{JSON.stringify(change.proposedDoc, null, 2)}</pre>
                <button type="button" className="btn" onClick={() => onPolicyDecision(change.id, "approve")}>Approve</button>{" "}
                <button type="button" className="btn" onClick={() => onPolicyDecision(change.id, "reject")}>Reject</button>
              </article>)}
            </div>}
          </section>

          <section className="card" style={{ maxWidth: 900, marginBottom: 16 }}>
            <h2>Emergency access</h2>
            <form onSubmit={onEmergencyRequest}>
              <label htmlFor="emergency-reason">Reason for a 30-minute agent steering grant</label>
              <textarea id="emergency-reason" minLength={10} required value={emergencyReason} onChange={(event) => setEmergencyReason(event.target.value)} />{" "}
              <button type="submit">Request emergency access</button>
            </form>
            <ul>{grants.map((grant) => (
              <li key={String(grant.id)}>
                {String(grant.status)} — {String(grant.reason)}; expires {String(grant.expiresAt ?? "when approved")}{" "}
                {grant.status === "pending" && grant.requestedBy !== session?.principal.userId && <button type="button" onClick={() => onGrantDecision(String(grant.id), "approve")}>Approve</button>}{" "}
                {(grant.status === "pending" || grant.status === "active") && <button type="button" onClick={() => onGrantDecision(String(grant.id), "revoke")}>Revoke</button>}
              </li>
            ))}</ul>
          </section>

          <section className="card" style={{ maxWidth: 900, marginBottom: 16 }}>
            <h2>Administrative history</h2>
            <p><a href="/settings/billing">Billing controls</a> · <a href="/settings/team">Team membership</a></p>
            <h3>Organization changes</h3>
            <ul>{history.map((row) => <li key={String(row.seq)}>{String(row.action)} at {String(row.ts)}</li>)}</ul>
            <h3>Policy decisions</h3>
            <ul>{decisions.map((row) => <li key={String(row.id)}>{String(row.decision)} {String(row.permission)} on {String(row.resource)} — {String(row.reason)}</li>)}</ul>
          </section>
        </>
      )}
    </AppShell>
  );
}
