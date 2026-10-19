"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { BreadcrumbTrail } from "@/components/Breadcrumbs";
import { getToken, loadSession } from "@/lib/auth";
import {
  OrganizationDto,
  OrganizationMemberDto,
  PolicyDocumentDto,
  decideEmergencyGrant,
  getOrganizationHistory,
  getOrganization,
  getPolicyDecisions,
  inviteOrganizationMember,
  listEmergencyGrants,
  listOrganizationMembers,
  listOrganizations,
  listPolicyDocuments,
  previewPolicy,
  requestEmergencyGrant,
  savePolicyDocument,
  updateOrganizationMember,
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
      const [org, roster, documents, orgHistory, policyDecisions, emergency] = await Promise.all([
        getOrganization(orgId, token),
        listOrganizationMembers(orgId, token),
        listPolicyDocuments(orgId, token),
        getOrganizationHistory(orgId, token),
        getPolicyDecisions(orgId, token),
        listEmergencyGrants(orgId, token),
      ]);
      setOrganizations((current) => current.map((item) => item.id === org.id ? { ...item, ...org } : item));
      setMembers(roster.items);
      setTargetUserId((current) => current || roster.items[0]?.userId || "");
      setHistory(orgHistory.items);
      setDecisions(policyDecisions.decisions);
      setGrants(emergency.grants);
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
      await savePolicyDocument(orgId, draft, token);
      setPreviewChanges(null);
      setNotice("Policy saved with a version check and audit record.");
      await refreshOrg();
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
            <p>Simulate a draft for one member before saving. Saving is immediate for owner/admin and requires step-up when configured.</p>
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
            <p>Policy changes apply immediately after save. Regulated policy approval workflows and organization-wide retention settings are not enabled in this pilot.</p>
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
