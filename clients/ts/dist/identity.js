/** Client for the harness-identity service (default http://localhost:8001). */
export class IdentityClient {
    http;
    baseUrl;
    /** @param http  shared transport; @param baseUrl resolved by Harness. */
    constructor(http, baseUrl = "") {
        this.http = http;
        this.baseUrl = baseUrl;
    }
    /** Erase Account
     *  POST /api/v1/account/erasure */
    erase_account(body) {
        const _path = `/api/v1/account/erasure`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Agents
     *  GET /api/v1/agents */
    list_agents(state, name) {
        const _path = `/api/v1/agents`;
        const params = { "state": state, "name": name };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Register
     *  POST /api/v1/agents */
    register(body) {
        const _path = `/api/v1/agents`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Select Agents
     *  POST /api/v1/agents/select */
    select_agents(body) {
        const _path = `/api/v1/agents/select`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Get Agent
     *  GET /api/v1/agents/{agent_id} */
    get_agent(agent_id) {
        const _path = `/api/v1/agents/${encodeURIComponent(agent_id)}`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** New Version
     *  PUT /api/v1/agents/{agent_id} */
    new_version(agent_id, body) {
        const _path = `/api/v1/agents/${encodeURIComponent(agent_id)}`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Approve
     *  POST /api/v1/agents/{agent_id}/approve */
    approve(agent_id) {
        const _path = `/api/v1/agents/${encodeURIComponent(agent_id)}/approve`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Retire
     *  POST /api/v1/agents/{agent_id}/retire */
    retire(agent_id, body) {
        const _path = `/api/v1/agents/${encodeURIComponent(agent_id)}/retire`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Audit
     *  GET /api/v1/audit */
    list_audit(limit) {
        const _path = `/api/v1/audit`;
        const params = { "limit": limit };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Export Audit
     *  GET /api/v1/audit/export */
    export_audit(stream, after_seq, limit, run_id, format) {
        const _path = `/api/v1/audit/export`;
        const params = { "stream": stream, "afterSeq": after_seq, "limit": limit, "runId": run_id, "format": format };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Verify Audit
     *  POST /api/v1/audit/verify */
    verify_audit(body) {
        const _path = `/api/v1/audit/verify`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Login
     *  POST /api/v1/auth/login */
    login(body) {
        const _path = `/api/v1/auth/login`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Logout
     *  POST /api/v1/auth/logout */
    logout() {
        const _path = `/api/v1/auth/logout`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Request Login
     *  POST /api/v1/auth/request-login */
    request_login(body) {
        const _path = `/api/v1/auth/request-login`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Sso Complete
     *  POST /api/v1/auth/sso/complete */
    sso_complete(body) {
        const _path = `/api/v1/auth/sso/complete`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Sso Start
     *  POST /api/v1/auth/sso/start */
    sso_start(body) {
        const _path = `/api/v1/auth/sso/start`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Verify Otp Route
     *  POST /api/v1/auth/verify-otp */
    verify_otp_route(body) {
        const _path = `/api/v1/auth/verify-otp`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Update Billing Plan
     *  PUT /api/v1/billing/plan */
    update_billing_plan(body) {
        const _path = `/api/v1/billing/plan`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Billing State
     *  GET /api/v1/billing/state */
    billing_state() {
        const _path = `/api/v1/billing/state`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Collect Csp Report
     *  POST /api/v1/csp-report */
    collect_csp_report() {
        const _path = `/api/v1/csp-report`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Redeem Invite
     *  POST /api/v1/invites/redeem */
    redeem_invite(body) {
        const _path = `/api/v1/invites/redeem`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Keys
     *  GET /api/v1/keys */
    list_keys(team_id) {
        const _path = `/api/v1/keys`;
        const params = { "teamId": team_id };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Mint Key
     *  POST /api/v1/keys */
    mint_key(body) {
        const _path = `/api/v1/keys`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Revoke Key
     *  DELETE /api/v1/keys/{key_id} */
    revoke_key(key_id) {
        const _path = `/api/v1/keys/${encodeURIComponent(key_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Me
     *  GET /api/v1/me */
    me() {
        const _path = `/api/v1/me`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** List Inbox
     *  GET /api/v1/notifications */
    list_inbox(unread, kind, state, limit, cursor) {
        const _path = `/api/v1/notifications`;
        const params = { "unread": unread, "kind": kind, "state": state, "limit": limit, "cursor": cursor };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Open Link
     *  GET /api/v1/notifications/link/{nid} */
    open_link(nid) {
        const _path = `/api/v1/notifications/link/${encodeURIComponent(nid)}`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Mute
     *  POST /api/v1/notifications/mute */
    mute(body) {
        const _path = `/api/v1/notifications/mute`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Unmute
     *  DELETE /api/v1/notifications/mute/{mute_id} */
    unmute(mute_id) {
        const _path = `/api/v1/notifications/mute/${encodeURIComponent(mute_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Get Preferences
     *  GET /api/v1/notifications/preferences */
    get_preferences() {
        const _path = `/api/v1/notifications/preferences`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Put Preferences
     *  PUT /api/v1/notifications/preferences */
    put_preferences(body) {
        const _path = `/api/v1/notifications/preferences`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Get Scope Preferences
     *  GET /api/v1/notifications/preferences/scopes/{scope_kind}/{scope_id} */
    get_scope_preferences(scope_kind, scope_id) {
        const _path = `/api/v1/notifications/preferences/scopes/${encodeURIComponent(scope_kind)}/${encodeURIComponent(scope_id)}`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Put Scope Preferences
     *  PUT /api/v1/notifications/preferences/scopes/{scope_kind}/{scope_id} */
    put_scope_preferences(scope_kind, scope_id, body) {
        const _path = `/api/v1/notifications/preferences/scopes/${encodeURIComponent(scope_kind)}/${encodeURIComponent(scope_id)}`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Read All
     *  POST /api/v1/notifications/read-all */
    read_all(body) {
        const _path = `/api/v1/notifications/read-all`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Unread Count
     *  GET /api/v1/notifications/unread-count */
    unread_count() {
        const _path = `/api/v1/notifications/unread-count`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Unsubscribe
     *  POST /api/v1/notifications/unsubscribe */
    unsubscribe(body) {
        const _path = `/api/v1/notifications/unsubscribe`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Dismiss
     *  POST /api/v1/notifications/{nid}/dismiss */
    dismiss(nid) {
        const _path = `/api/v1/notifications/${encodeURIComponent(nid)}/dismiss`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Mark Read
     *  POST /api/v1/notifications/{nid}/read */
    mark_read(nid) {
        const _path = `/api/v1/notifications/${encodeURIComponent(nid)}/read`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Snooze
     *  POST /api/v1/notifications/{nid}/snooze */
    snooze(nid, body) {
        const _path = `/api/v1/notifications/${encodeURIComponent(nid)}/snooze`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Orgs
     *  GET /api/v1/orgs */
    list_orgs() {
        const _path = `/api/v1/orgs`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Create Org
     *  POST /api/v1/orgs */
    create_org(body) {
        const _path = `/api/v1/orgs`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Accept Invite
     *  POST /api/v1/orgs/invites/accept */
    accept_invite(body) {
        const _path = `/api/v1/orgs/invites/accept`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Delete My Identity
     *  DELETE /api/v1/orgs/me/identity */
    delete_my_identity() {
        const _path = `/api/v1/orgs/me/identity`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Service Whoami
     *  GET /api/v1/orgs/service/whoami */
    service_whoami() {
        const _path = `/api/v1/orgs/service/whoami`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Get Org
     *  GET /api/v1/orgs/{org_id} */
    get_org(org_id) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Delete Org
     *  DELETE /api/v1/orgs/{org_id} */
    delete_org(org_id) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Patch Org
     *  PATCH /api/v1/orgs/{org_id} */
    patch_org(org_id, body) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}`;
        return this.http.request(this.baseUrl, _path, "PATCH", body);
    }
    /** Get Execution Control
     *  GET /api/v1/orgs/{org_id}/execution-control */
    get_execution_control(org_id) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}/execution-control`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Set Execution Control
     *  PUT /api/v1/orgs/{org_id}/execution-control */
    set_execution_control(org_id, body) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}/execution-control`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Org History
     *  GET /api/v1/orgs/{org_id}/history */
    org_history(org_id, limit) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}/history`;
        const params = { "limit": limit };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** List Invites
     *  GET /api/v1/orgs/{org_id}/invites */
    list_invites(org_id) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}/invites`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Create Invite
     *  POST /api/v1/orgs/{org_id}/invites */
    create_invite(org_id, body) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}/invites`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Revoke Invite
     *  DELETE /api/v1/orgs/{org_id}/invites/{invite_id} */
    revoke_invite(org_id, invite_id) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}/invites/${encodeURIComponent(invite_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** List Members
     *  GET /api/v1/orgs/{org_id}/members */
    list_members(org_id) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}/members`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Remove Member
     *  DELETE /api/v1/orgs/{org_id}/members/{user_id} */
    remove_member(org_id, user_id) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}/members/${encodeURIComponent(user_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Change Role
     *  PATCH /api/v1/orgs/{org_id}/members/{user_id} */
    change_role(org_id, user_id, body) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}/members/${encodeURIComponent(user_id)}`;
        return this.http.request(this.baseUrl, _path, "PATCH", body);
    }
    /** List Service Identities
     *  GET /api/v1/orgs/{org_id}/service-identities */
    list_service_identities(org_id) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}/service-identities`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Create Service Identity
     *  POST /api/v1/orgs/{org_id}/service-identities */
    create_service_identity(org_id, body) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}/service-identities`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Revoke Service Identity
     *  DELETE /api/v1/orgs/{org_id}/service-identities/{sid} */
    revoke_service_identity(org_id, sid) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}/service-identities/${encodeURIComponent(sid)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Link Team
     *  POST /api/v1/orgs/{org_id}/teams */
    link_team(org_id, body) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}/teams`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Remove From Team
     *  DELETE /api/v1/orgs/{org_id}/teams/{team_id}/members/{user_id} */
    remove_from_team(org_id, team_id, user_id) {
        const _path = `/api/v1/orgs/${encodeURIComponent(org_id)}/teams/${encodeURIComponent(team_id)}/members/${encodeURIComponent(user_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Assign Role
     *  POST /api/v1/policy/orgs/{org_id}/assignments */
    assign_role(org_id, body) {
        const _path = `/api/v1/policy/orgs/${encodeURIComponent(org_id)}/assignments`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Policy Change Requests
     *  GET /api/v1/policy/orgs/{org_id}/change-requests */
    list_policy_change_requests(org_id) {
        const _path = `/api/v1/policy/orgs/${encodeURIComponent(org_id)}/change-requests`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Approve Policy Change
     *  POST /api/v1/policy/orgs/{org_id}/change-requests/{request_id}/approve */
    approve_policy_change(org_id, request_id) {
        const _path = `/api/v1/policy/orgs/${encodeURIComponent(org_id)}/change-requests/${encodeURIComponent(request_id)}/approve`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Reject Policy Change
     *  POST /api/v1/policy/orgs/{org_id}/change-requests/{request_id}/reject */
    reject_policy_change(org_id, request_id) {
        const _path = `/api/v1/policy/orgs/${encodeURIComponent(org_id)}/change-requests/${encodeURIComponent(request_id)}/reject`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Check
     *  POST /api/v1/policy/orgs/{org_id}/check */
    check(org_id, body) {
        const _path = `/api/v1/policy/orgs/${encodeURIComponent(org_id)}/check`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Decisions
     *  GET /api/v1/policy/orgs/{org_id}/decisions */
    decisions(org_id, limit, since, until, subject, decision) {
        const _path = `/api/v1/policy/orgs/${encodeURIComponent(org_id)}/decisions`;
        const params = { "limit": limit, "since": since, "until": until, "subject": subject, "decision": decision };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** List Documents
     *  GET /api/v1/policy/orgs/{org_id}/documents */
    list_documents(org_id) {
        const _path = `/api/v1/policy/orgs/${encodeURIComponent(org_id)}/documents`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Put Document
     *  PUT /api/v1/policy/orgs/{org_id}/documents/{level}/{scope_id} */
    put_document(org_id, level, scope_id, body) {
        const _path = `/api/v1/policy/orgs/${encodeURIComponent(org_id)}/documents/${encodeURIComponent(level)}/${encodeURIComponent(scope_id)}`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Emergency List
     *  GET /api/v1/policy/orgs/{org_id}/emergency */
    emergency_list(org_id) {
        const _path = `/api/v1/policy/orgs/${encodeURIComponent(org_id)}/emergency`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Emergency Request
     *  POST /api/v1/policy/orgs/{org_id}/emergency */
    emergency_request(org_id, body) {
        const _path = `/api/v1/policy/orgs/${encodeURIComponent(org_id)}/emergency`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Emergency Approve
     *  POST /api/v1/policy/orgs/{org_id}/emergency/{gid}/approve */
    emergency_approve(org_id, gid) {
        const _path = `/api/v1/policy/orgs/${encodeURIComponent(org_id)}/emergency/${encodeURIComponent(gid)}/approve`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Emergency Revoke
     *  POST /api/v1/policy/orgs/{org_id}/emergency/{gid}/revoke */
    emergency_revoke(org_id, gid) {
        const _path = `/api/v1/policy/orgs/${encodeURIComponent(org_id)}/emergency/${encodeURIComponent(gid)}/revoke`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Preview
     *  POST /api/v1/policy/orgs/{org_id}/preview */
    preview(org_id, body) {
        const _path = `/api/v1/policy/orgs/${encodeURIComponent(org_id)}/preview`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Roles
     *  GET /api/v1/policy/orgs/{org_id}/roles */
    list_roles(org_id) {
        const _path = `/api/v1/policy/orgs/${encodeURIComponent(org_id)}/roles`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Put Role
     *  PUT /api/v1/policy/orgs/{org_id}/roles/{name} */
    put_role(org_id, name, body) {
        const _path = `/api/v1/policy/orgs/${encodeURIComponent(org_id)}/roles/${encodeURIComponent(name)}`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** List Projects
     *  GET /api/v1/projects */
    list_projects(limit) {
        const _path = `/api/v1/projects`;
        const params = { "limit": limit };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Create Project
     *  POST /api/v1/projects */
    create_project(body) {
        const _path = `/api/v1/projects`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Replay Events
     *  GET /api/v1/projects/{project_id}/events/replay */
    replay_events(project_id, after_seq, limit, before_seq) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/events/replay`;
        const params = { "after_seq": after_seq, "limit": limit, "before_seq": before_seq };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** List Memory
     *  GET /api/v1/projects/{project_id}/memory */
    list_memory(project_id) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/memory`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Create Memory
     *  POST /api/v1/projects/{project_id}/memory */
    create_memory(project_id, body) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/memory`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Delete Memory
     *  DELETE /api/v1/projects/{project_id}/memory/{entry_id} */
    delete_memory(project_id, entry_id) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/memory/${encodeURIComponent(entry_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Review Memory
     *  POST /api/v1/projects/{project_id}/memory/{entry_id}/review */
    review_memory(project_id, entry_id) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/memory/${encodeURIComponent(entry_id)}/review`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Get Rules
     *  GET /api/v1/projects/{project_id}/rules */
    get_rules(project_id) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/rules`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Save Rules
     *  PUT /api/v1/projects/{project_id}/rules */
    save_rules(project_id, body) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/rules`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Delete Rules
     *  DELETE /api/v1/projects/{project_id}/rules */
    delete_rules(project_id) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/rules`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Runner Heartbeat
     *  POST /api/v1/runner/heartbeat */
    runner_heartbeat(body) {
        const _path = `/api/v1/runner/heartbeat`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List My Jobs
     *  GET /api/v1/runner/jobs */
    list_my_jobs() {
        const _path = `/api/v1/runner/jobs`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Claim Job
     *  POST /api/v1/runner/jobs/{run_id}/claim */
    claim_job(run_id) {
        const _path = `/api/v1/runner/jobs/${encodeURIComponent(run_id)}/claim`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Post Job Events
     *  POST /api/v1/runner/jobs/{run_id}/events */
    post_job_events(run_id, body) {
        const _path = `/api/v1/runner/jobs/${encodeURIComponent(run_id)}/events`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Finish Job
     *  POST /api/v1/runner/jobs/{run_id}/finish */
    finish_job(run_id, body) {
        const _path = `/api/v1/runner/jobs/${encodeURIComponent(run_id)}/finish`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Heartbeat Job
     *  POST /api/v1/runner/jobs/{run_id}/heartbeat */
    heartbeat_job(run_id) {
        const _path = `/api/v1/runner/jobs/${encodeURIComponent(run_id)}/heartbeat`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Job Spec
     *  GET /api/v1/runner/jobs/{run_id}/spec */
    job_spec(run_id) {
        const _path = `/api/v1/runner/jobs/${encodeURIComponent(run_id)}/spec`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** List Sessions
     *  GET /api/v1/sessions */
    list_sessions(q, state, workspace_id, run_id, limit, cursor) {
        const _path = `/api/v1/sessions`;
        const params = { "q": q, "state": state, "workspaceId": workspace_id, "runId": run_id, "limit": limit, "cursor": cursor };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Create Session
     *  POST /api/v1/sessions */
    create_session(body) {
        const _path = `/api/v1/sessions`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Templates
     *  GET /api/v1/sessions/templates */
    list_templates() {
        const _path = `/api/v1/sessions/templates`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Save Template
     *  POST /api/v1/sessions/templates */
    save_template(body) {
        const _path = `/api/v1/sessions/templates`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Get Session
     *  GET /api/v1/sessions/{sid} */
    get_session(sid) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Update Session
     *  PATCH /api/v1/sessions/{sid} */
    update_session(sid, body) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}`;
        return this.http.request(this.baseUrl, _path, "PATCH", body);
    }
    /** Apply Template
     *  POST /api/v1/sessions/{sid}/apply-template */
    apply_template(sid, body) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/apply-template`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Archive Session
     *  POST /api/v1/sessions/{sid}/archive */
    archive_session(sid, body) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/archive`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Complete Session
     *  POST /api/v1/sessions/{sid}/complete */
    complete_session(sid, body) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/complete`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Delete Session
     *  POST /api/v1/sessions/{sid}/delete */
    delete_session(sid, body) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/delete`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Members
     *  GET /api/v1/sessions/{sid}/members */
    list_members_2(sid, status) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/members`;
        const params = { "status": status };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Add Member
     *  POST /api/v1/sessions/{sid}/members */
    add_member(sid, body) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/members`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Put Member
     *  PUT /api/v1/sessions/{sid}/members/{ref} */
    put_member(sid, ref, body) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/members/${encodeURIComponent(ref)}`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Remove Member
     *  DELETE /api/v1/sessions/{sid}/members/{ref} */
    remove_member_2(sid, ref) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/members/${encodeURIComponent(ref)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Transfer Owner
     *  POST /api/v1/sessions/{sid}/owner */
    transfer_owner(sid, body) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/owner`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Pause Session
     *  POST /api/v1/sessions/{sid}/pause */
    pause_session(sid, body) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/pause`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Purge Session
     *  POST /api/v1/sessions/{sid}/purge */
    purge_session(sid) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/purge`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Restore Session
     *  POST /api/v1/sessions/{sid}/restore */
    restore_session(sid, body) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/restore`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Resume Session
     *  GET /api/v1/sessions/{sid}/resume */
    resume_session(sid, limit) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/resume`;
        const params = { "limit": limit };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** List Runs
     *  GET /api/v1/sessions/{sid}/runs */
    list_runs(sid) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/runs`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Link Run
     *  POST /api/v1/sessions/{sid}/runs */
    link_run(sid, body) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/runs`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Unlink Run
     *  DELETE /api/v1/sessions/{sid}/runs/{run_id} */
    unlink_run(sid, run_id) {
        const _path = `/api/v1/sessions/${encodeURIComponent(sid)}/runs/${encodeURIComponent(run_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Stepup Verify
     *  POST /api/v1/stepup/verify */
    stepup_verify(body) {
        const _path = `/api/v1/stepup/verify`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Runners
     *  GET /api/v1/team-runners */
    list_runners() {
        const _path = `/api/v1/team-runners`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Register Runner
     *  POST /api/v1/team-runners */
    register_runner(body) {
        const _path = `/api/v1/team-runners`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Assign Job
     *  POST /api/v1/team-runners/{runner_id}/jobs */
    assign_job(runner_id, body) {
        const _path = `/api/v1/team-runners/${encodeURIComponent(runner_id)}/jobs`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Revoke Runner
     *  POST /api/v1/team-runners/{runner_id}/revoke */
    revoke_runner(runner_id) {
        const _path = `/api/v1/team-runners/${encodeURIComponent(runner_id)}/revoke`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Rotate Runner
     *  POST /api/v1/team-runners/{runner_id}/rotate */
    rotate_runner(runner_id) {
        const _path = `/api/v1/team-runners/${encodeURIComponent(runner_id)}/rotate`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** List Invites
     *  GET /api/v1/teams/{team_id}/invites */
    list_invites_2(team_id, status) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/invites`;
        const params = { "status": status };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Create Invite
     *  POST /api/v1/teams/{team_id}/invites */
    create_invite_2(team_id, body) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/invites`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Revoke Invite
     *  DELETE /api/v1/teams/{team_id}/invites/{invite_id} */
    revoke_invite_2(team_id, invite_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/invites/${encodeURIComponent(invite_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Resend Invite
     *  POST /api/v1/teams/{team_id}/invites/{invite_id}/resend */
    resend_invite(team_id, invite_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/invites/${encodeURIComponent(invite_id)}/resend`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** List Members
     *  GET /api/v1/teams/{team_id}/members */
    list_members_3(team_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/members`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Remove Member
     *  DELETE /api/v1/teams/{team_id}/members/{user_id} */
    remove_member_3(team_id, user_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/members/${encodeURIComponent(user_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Change Member Role
     *  PATCH /api/v1/teams/{team_id}/members/{user_id} */
    change_member_role(team_id, user_id, body) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/members/${encodeURIComponent(user_id)}`;
        return this.http.request(this.baseUrl, _path, "PATCH", body);
    }
    /** List Scim Tokens
     *  GET /api/v1/teams/{team_id}/scim/tokens */
    list_scim_tokens(team_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/scim/tokens`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Create Scim Token
     *  POST /api/v1/teams/{team_id}/scim/tokens */
    create_scim_token(team_id, body) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/scim/tokens`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Revoke Scim Token
     *  DELETE /api/v1/teams/{team_id}/scim/tokens/{token_id} */
    revoke_scim_token(team_id, token_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/scim/tokens/${encodeURIComponent(token_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Add Agent Seat
     *  POST /api/v1/teams/{team_id}/seats/agent */
    add_agent_seat(team_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/seats/agent`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** List Webhooks
     *  GET /api/v1/teams/{team_id}/settings/integrations/v1/webhooks */
    list_webhooks(team_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/settings/integrations/v1/webhooks`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Create Webhook
     *  POST /api/v1/teams/{team_id}/settings/integrations/v1/webhooks */
    create_webhook(team_id, body) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/settings/integrations/v1/webhooks`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Event Types
     *  GET /api/v1/teams/{team_id}/settings/integrations/v1/webhooks/event-types */
    list_event_types(team_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/settings/integrations/v1/webhooks/event-types`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Delete Webhook
     *  DELETE /api/v1/teams/{team_id}/settings/integrations/v1/webhooks/{webhook_id} */
    delete_webhook(team_id, webhook_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/settings/integrations/v1/webhooks/${encodeURIComponent(webhook_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Set Webhook Active
     *  PATCH /api/v1/teams/{team_id}/settings/integrations/v1/webhooks/{webhook_id} */
    set_webhook_active(team_id, webhook_id, body) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/settings/integrations/v1/webhooks/${encodeURIComponent(webhook_id)}`;
        return this.http.request(this.baseUrl, _path, "PATCH", body);
    }
    /** List Deliveries
     *  GET /api/v1/teams/{team_id}/settings/integrations/v1/webhooks/{webhook_id}/deliveries */
    list_deliveries(team_id, webhook_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/settings/integrations/v1/webhooks/${encodeURIComponent(webhook_id)}/deliveries`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Rotate Secret
     *  POST /api/v1/teams/{team_id}/settings/integrations/v1/webhooks/{webhook_id}/rotate-secret */
    rotate_secret(team_id, webhook_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/settings/integrations/v1/webhooks/${encodeURIComponent(webhook_id)}/rotate-secret`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Get Model Routing
     *  GET /api/v1/teams/{team_id}/settings/model-routing */
    get_model_routing(team_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/settings/model-routing`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Put Model Routing
     *  PUT /api/v1/teams/{team_id}/settings/model-routing */
    put_model_routing(team_id, body) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/settings/model-routing`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Delete Model Routing
     *  DELETE /api/v1/teams/{team_id}/settings/model-routing */
    delete_model_routing(team_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/settings/model-routing`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Get Reviewer Thresholds
     *  GET /api/v1/teams/{team_id}/settings/reviewer-thresholds */
    get_reviewer_thresholds(team_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/settings/reviewer-thresholds`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Put Reviewer Thresholds
     *  PUT /api/v1/teams/{team_id}/settings/reviewer-thresholds */
    put_reviewer_thresholds(team_id, body) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/settings/reviewer-thresholds`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Delete Reviewer Thresholds
     *  DELETE /api/v1/teams/{team_id}/settings/reviewer-thresholds */
    delete_reviewer_thresholds(team_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/settings/reviewer-thresholds`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** List Sinks
     *  GET /api/v1/teams/{team_id}/siem/sinks */
    list_sinks(team_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/siem/sinks`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Create Sink
     *  POST /api/v1/teams/{team_id}/siem/sinks */
    create_sink(team_id, body) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/siem/sinks`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Get Sink
     *  GET /api/v1/teams/{team_id}/siem/sinks/{sink_id} */
    get_sink(team_id, sink_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/siem/sinks/${encodeURIComponent(sink_id)}`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Delete Sink
     *  DELETE /api/v1/teams/{team_id}/siem/sinks/{sink_id} */
    delete_sink(team_id, sink_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/siem/sinks/${encodeURIComponent(sink_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Patch Sink
     *  PATCH /api/v1/teams/{team_id}/siem/sinks/{sink_id} */
    patch_sink(team_id, sink_id, body) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/siem/sinks/${encodeURIComponent(sink_id)}`;
        return this.http.request(this.baseUrl, _path, "PATCH", body);
    }
    /** List Skills
     *  GET /api/v1/teams/{team_id}/skills */
    list_skills(team_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/skills`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Create Skill
     *  POST /api/v1/teams/{team_id}/skills */
    create_skill(team_id, body) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/skills`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Remove Skill
     *  DELETE /api/v1/teams/{team_id}/skills/{name} */
    remove_skill(team_id, name) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/skills/${encodeURIComponent(name)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Disable Skill
     *  POST /api/v1/teams/{team_id}/skills/{name}/disable */
    disable_skill(team_id, name) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/skills/${encodeURIComponent(name)}/disable`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Enable Skill
     *  POST /api/v1/teams/{team_id}/skills/{name}/enable */
    enable_skill(team_id, name) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/skills/${encodeURIComponent(name)}/enable`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Review Skill
     *  POST /api/v1/teams/{team_id}/skills/{name}/review */
    review_skill(team_id, name) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/skills/${encodeURIComponent(name)}/review`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Get Sso Config
     *  GET /api/v1/teams/{team_id}/sso/config */
    get_sso_config(team_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/sso/config`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Put Sso Config
     *  PUT /api/v1/teams/{team_id}/sso/config */
    put_sso_config(team_id, body) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/sso/config`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Delete Sso Config
     *  DELETE /api/v1/teams/{team_id}/sso/config */
    delete_sso_config(team_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/sso/config`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Search Workspaces
     *  GET /api/v1/workspaces */
    search_workspaces(q, state, template, tag, limit, cursor) {
        const _path = `/api/v1/workspaces`;
        const params = { "q": q, "state": state, "template": template, "tag": tag, "limit": limit, "cursor": cursor };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Create Workspace
     *  POST /api/v1/workspaces */
    create_workspace(body) {
        const _path = `/api/v1/workspaces`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Allowlist
     *  GET /api/v1/workspaces/allowlist */
    list_allowlist() {
        const _path = `/api/v1/workspaces/allowlist`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Approve Binding
     *  POST /api/v1/workspaces/allowlist */
    approve_binding(body) {
        const _path = `/api/v1/workspaces/allowlist`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Revoke Allowlist
     *  DELETE /api/v1/workspaces/allowlist/{entry_id} */
    revoke_allowlist(entry_id) {
        const _path = `/api/v1/workspaces/allowlist/${encodeURIComponent(entry_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** List Templates
     *  GET /api/v1/workspaces/templates */
    list_templates_2() {
        const _path = `/api/v1/workspaces/templates`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Save Template
     *  POST /api/v1/workspaces/templates */
    save_template_2(body) {
        const _path = `/api/v1/workspaces/templates`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Get Workspace
     *  GET /api/v1/workspaces/{ws_id} */
    get_workspace(ws_id) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Update Workspace
     *  PATCH /api/v1/workspaces/{ws_id} */
    update_workspace(ws_id, body) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}`;
        return this.http.request(this.baseUrl, _path, "PATCH", body);
    }
    /** Apply Template
     *  POST /api/v1/workspaces/{ws_id}/apply-template */
    apply_template_2(ws_id, body) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/apply-template`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Archive Workspace
     *  POST /api/v1/workspaces/{ws_id}/archive */
    archive_workspace(ws_id, body) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/archive`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Bindings
     *  GET /api/v1/workspaces/{ws_id}/bindings */
    list_bindings(ws_id) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/bindings`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Add Binding
     *  POST /api/v1/workspaces/{ws_id}/bindings */
    add_binding(ws_id, body) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/bindings`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Remove Binding
     *  DELETE /api/v1/workspaces/{ws_id}/bindings/{binding_id} */
    remove_binding(ws_id, binding_id) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/bindings/${encodeURIComponent(binding_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Delete Workspace
     *  POST /api/v1/workspaces/{ws_id}/delete */
    delete_workspace(ws_id, body) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/delete`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Members
     *  GET /api/v1/workspaces/{ws_id}/members */
    list_members_4(ws_id) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/members`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Put Member
     *  PUT /api/v1/workspaces/{ws_id}/members/{user_id} */
    put_member_2(ws_id, user_id, body) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/members/${encodeURIComponent(user_id)}`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Remove Member
     *  DELETE /api/v1/workspaces/{ws_id}/members/{user_id} */
    remove_member_4(ws_id, user_id) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/members/${encodeURIComponent(user_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Pause Workspace
     *  POST /api/v1/workspaces/{ws_id}/pause */
    pause_workspace(ws_id, body) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/pause`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Projects
     *  GET /api/v1/workspaces/{ws_id}/projects */
    list_projects_2(ws_id) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/projects`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Link Project
     *  POST /api/v1/workspaces/{ws_id}/projects */
    link_project(ws_id, body) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/projects`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Unlink Project
     *  DELETE /api/v1/workspaces/{ws_id}/projects/{project_id} */
    unlink_project(ws_id, project_id) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/projects/${encodeURIComponent(project_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Restore Workspace
     *  POST /api/v1/workspaces/{ws_id}/restore */
    restore_workspace(ws_id, body) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/restore`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Get Settings
     *  GET /api/v1/workspaces/{ws_id}/settings */
    get_settings(ws_id) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/settings`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Put Settings
     *  PUT /api/v1/workspaces/{ws_id}/settings */
    put_settings(ws_id, body) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/settings`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Settings History
     *  GET /api/v1/workspaces/{ws_id}/settings/history */
    settings_history(ws_id, limit) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/settings/history`;
        const params = { "limit": limit };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Revert Settings
     *  POST /api/v1/workspaces/{ws_id}/settings/revert */
    revert_settings(ws_id, body) {
        const _path = `/api/v1/workspaces/${encodeURIComponent(ws_id)}/settings/revert`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Issue Ws Ticket
     *  POST /api/v1/ws-ticket */
    issue_ws_ticket(body) {
        const _path = `/api/v1/ws-ticket`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Health
     *  GET /health */
    health() {
        const _path = `/health`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Ready
     *  GET /ready */
    ready_ready_get() {
        const _path = `/ready`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** List Users
     *  GET /scim/v2/Users */
    list_users_scim_v2_users_get(filter, start_index, count) {
        const _path = `/scim/v2/Users`;
        const params = { "filter": filter, "startIndex": start_index, "count": count };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Get User
     *  GET /scim/v2/Users/{user_id} */
    get_user_scim_v2_users_user_id_get(user_id) {
        const _path = `/scim/v2/Users/${encodeURIComponent(user_id)}`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Put User
     *  PUT /scim/v2/Users/{user_id} */
    put_user_scim_v2_users_user_id_put(user_id, body) {
        const _path = `/scim/v2/Users/${encodeURIComponent(user_id)}`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Patch User
     *  PATCH /scim/v2/Users/{user_id} */
    patch_user_scim_v2_users_user_id_patch(user_id, body) {
        const _path = `/scim/v2/Users/${encodeURIComponent(user_id)}`;
        return this.http.request(this.baseUrl, _path, "PATCH", body);
    }
}
/* Module surface (mirrors __all__ in clients/python):
 * export class IdentityClient
 * export interface/type AcceptBody
 * export interface/type ApplyBody
 * export interface/type AttestBody
 * export interface/type AuditVerifyRequest
 * export interface/type BindingBody
 * export interface/type ChainNode
 * export interface/type CheckBody
 * export interface/type CreateProjectBody
 * export interface/type DefBody
 * export interface/type DeleteBody
 * export interface/type DocBody
 * export interface/type Draft
 * export interface/type EmergencyBody
 * export interface/type ErasureBody
 * export interface/type ErrorDetail
 * export interface/type ErrorEnvelope
 * export interface/type EventEnvelopeOut
 * export interface/type EventReplayResponse
 * export interface/type EventsBody
 * export interface/type ExecutionControlBody
 * export interface/type FinishBody
 * export interface/type GlobalPrefsBody
 * export interface/type HTTPValidationError
 * export interface/type InviteBody
 * export interface/type InviteCreateBody
 * export interface/type InviteRedeemBody
 * export interface/type KeyCreateBody
 * export interface/type KeyCreatedResponse
 * export interface/type KeyItemResponse
 * export interface/type KeyListResponse
 * export interface/type LinkBody
 * export interface/type LinkRunBody
 * export interface/type LinkTeamBody
 * export interface/type LoginRequest
 * export interface/type MemberItem
 * export interface/type MemberListResponse
 * export interface/type MemberRoleBody
 * export interface/type MemoryBody
 * export interface/type MuteBody
 * export interface/type OkResponse
 * export interface/type OrgCreateBody
 * export interface/type OrgPatchBody
 * export interface/type OwnerTransferBody
 * export interface/type PatchOp
 * export interface/type PlanUpdateBody
 * export interface/type PolicyDoc
 * export interface/type PreviewBody
 * export interface/type PrincipalResponse
 * export interface/type ProjectCreatedResponse
 * export interface/type ProjectListResponse
 * export interface/type ProjectOut
 * export interface/type PutBody
 * export interface/type ReadAllBody
 * export interface/type RevertBody
 * export interface/type RoutingBody
 * export interface/type Rule
 * export interface/type RulesBody
 * export interface/type RunnerBody
 * export interface/type RunnerEvent
 * export interface/type ScopePrefsBody
 * export interface/type SelectBody
 * export interface/type ServiceIdentityBody
 * export interface/type SessionResponse
 * export interface/type SettingsBody
 * export interface/type SinkCreateBody
 * export interface/type SinkPatchBody
 * export interface/type SkillBody
 * export interface/type SnoozeBody
 * export interface/type SsoCompleteBody
 * export interface/type SsoConfigBody
 * export interface/type SsoStartBody
 * export interface/type StepUpBody
 * export interface/type TemplateBody
 * export interface/type ThresholdsBody
 * export interface/type TokenBody
 * export interface/type UnsubscribeBody
 * export interface/type ValidationError
 * export interface/type VerifyOtpRequest
 * export interface/type WebhookCreateBody
 * export interface/type WebhookCreated
 * export interface/type WebhookPatchBody
 * export interface/type WsTicketRequest
 * export interface/type WsTicketResponse
 * export interface/type _DigestBody
 * export interface/type _QuietBody
 * export interface/type harness_identity__agents__ReasonBody
 * export interface/type harness_identity__orgs__RoleBody
 * export interface/type harness_identity__policy__AssignBody
 * export interface/type harness_identity__policy__RoleBody
 * export interface/type harness_identity__runner_api__AssignBody
 * export interface/type harness_identity__scim__PatchBody
 * export interface/type harness_identity__sessions__CreateBody
 * export interface/type harness_identity__sessions__MemberBody
 * export interface/type harness_identity__sessions__PatchBody
 * export interface/type harness_identity__sessions__ReasonBody
 * export interface/type harness_identity__sessions__RoleBody
 * export interface/type harness_identity__workspaces__CreateBody
 * export interface/type harness_identity__workspaces__MemberBody
 * export interface/type harness_identity__workspaces__PatchBody
 * export interface/type harness_identity__workspaces__ReasonBody
 */
