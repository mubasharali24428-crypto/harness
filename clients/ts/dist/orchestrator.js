/** Client for the harness-orchestrator service (default http://localhost:8004). */
export class OrchestratorClient {
    http;
    baseUrl;
    /** @param http  shared transport; @param baseUrl resolved by Harness. */
    constructor(http, baseUrl = "") {
        this.http = http;
        this.baseUrl = baseUrl;
    }
    /** Deletion List
     *  GET /api/v1/orchestrator/deletion-requests */
    deletion_list(state, limit) {
        const _path = `/api/v1/orchestrator/deletion-requests`;
        const params = { "state": state, "limit": limit };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Deletion Create
     *  POST /api/v1/orchestrator/deletion-requests */
    deletion_create(body) {
        const _path = `/api/v1/orchestrator/deletion-requests`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Deletion Approve
     *  POST /api/v1/orchestrator/deletion-requests/{requestId}/approve */
    deletion_approve(request_id, body) {
        const _path = `/api/v1/orchestrator/deletion-requests/${encodeURIComponent(request_id)}/approve`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Deletion Reject
     *  POST /api/v1/orchestrator/deletion-requests/{requestId}/reject */
    deletion_reject(request_id, body) {
        const _path = `/api/v1/orchestrator/deletion-requests/${encodeURIComponent(request_id)}/reject`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Seed Demo
     *  POST /api/v1/orchestrator/demo/seed */
    seed_demo() {
        const _path = `/api/v1/orchestrator/demo/seed`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Discussion Settings Get
     *  GET /api/v1/orchestrator/discussion-settings */
    discussion_settings_get() {
        const _path = `/api/v1/orchestrator/discussion-settings`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Discussion Settings Put
     *  PUT /api/v1/orchestrator/discussion-settings */
    discussion_settings_put(body) {
        const _path = `/api/v1/orchestrator/discussion-settings`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Discussions Search
     *  GET /api/v1/orchestrator/discussions/search */
    discussions_search(q, scope_kind, limit, before) {
        const _path = `/api/v1/orchestrator/discussions/search`;
        const params = { "q": q, "scopeKind": scope_kind, "limit": limit, "before": before };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Exports List
     *  GET /api/v1/orchestrator/exports */
    exports_list(limit) {
        const _path = `/api/v1/orchestrator/exports`;
        const params = { "limit": limit };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Exports Create
     *  POST /api/v1/orchestrator/exports */
    exports_create(body) {
        const _path = `/api/v1/orchestrator/exports`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Exports Get
     *  GET /api/v1/orchestrator/exports/{exportId} */
    exports_get(export_id) {
        const _path = `/api/v1/orchestrator/exports/${encodeURIComponent(export_id)}`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Exports Download
     *  GET /api/v1/orchestrator/exports/{exportId}/download */
    exports_download(export_id) {
        const _path = `/api/v1/orchestrator/exports/${encodeURIComponent(export_id)}/download`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Propose Plan
     *  POST /api/v1/orchestrator/plans */
    propose_plan(body) {
        const _path = `/api/v1/orchestrator/plans`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Approve Plan
     *  POST /api/v1/orchestrator/plans/{planId}/approve */
    approve_plan(plan_id) {
        const _path = `/api/v1/orchestrator/plans/${encodeURIComponent(plan_id)}/approve`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Reject Plan
     *  POST /api/v1/orchestrator/plans/{planId}/reject */
    reject_plan(plan_id) {
        const _path = `/api/v1/orchestrator/plans/${encodeURIComponent(plan_id)}/reject`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Get Settings
     *  GET /api/v1/orchestrator/presence-settings */
    get_settings() {
        const _path = `/api/v1/orchestrator/presence-settings`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Put Settings
     *  PUT /api/v1/orchestrator/presence-settings */
    put_settings(body) {
        const _path = `/api/v1/orchestrator/presence-settings`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** List Runs
     *  GET /api/v1/orchestrator/runs */
    list_runs(project_id, status, limit, offset, cursor) {
        const _path = `/api/v1/orchestrator/runs`;
        const params = { "projectId": project_id, "status": status, "limit": limit, "offset": offset, "cursor": cursor };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Get Run
     *  GET /api/v1/orchestrator/runs/{runId} */
    get_run(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Abandon Route
     *  POST /api/v1/orchestrator/runs/{runId}/abandon */
    abandon_route(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/abandon`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Agents
     *  GET /api/v1/orchestrator/runs/{runId}/agents */
    list_agents(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/agents`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Spawn
     *  POST /api/v1/orchestrator/runs/{runId}/agents/spawn */
    spawn(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/agents/spawn`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Cancel
     *  POST /api/v1/orchestrator/runs/{runId}/agents/{assignmentId}/cancel */
    cancel(run_id, assignment_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/agents/${encodeURIComponent(assignment_id)}/cancel`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Pause
     *  POST /api/v1/orchestrator/runs/{runId}/agents/{assignmentId}/pause */
    pause(run_id, assignment_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/agents/${encodeURIComponent(assignment_id)}/pause`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Reassign
     *  POST /api/v1/orchestrator/runs/{runId}/agents/{assignmentId}/reassign */
    reassign(run_id, assignment_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/agents/${encodeURIComponent(assignment_id)}/reassign`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Resume
     *  POST /api/v1/orchestrator/runs/{runId}/agents/{assignmentId}/resume */
    resume(run_id, assignment_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/agents/${encodeURIComponent(assignment_id)}/resume`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Record Tool Call
     *  POST /api/v1/orchestrator/runs/{runId}/agents/{assignmentId}/tool-calls */
    record_tool_call(run_id, assignment_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/agents/${encodeURIComponent(assignment_id)}/tool-calls`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Collab Approval List
     *  GET /api/v1/orchestrator/runs/{runId}/approvals */
    collab_approval_list(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/approvals`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Collab Approval Request
     *  POST /api/v1/orchestrator/runs/{runId}/approvals */
    collab_approval_request(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/approvals`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Collab Approval Amend
     *  PUT /api/v1/orchestrator/runs/{runId}/approvals/{approvalId} */
    collab_approval_amend(run_id, approval_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/approvals/${encodeURIComponent(approval_id)}`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Collab Apply Staged
     *  POST /api/v1/orchestrator/runs/{runId}/approvals/{approvalId}/apply */
    collab_apply_staged(run_id, approval_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/approvals/${encodeURIComponent(approval_id)}/apply`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Collab Approval Claim
     *  POST /api/v1/orchestrator/runs/{runId}/approvals/{approvalId}/claim */
    collab_approval_claim(run_id, approval_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/approvals/${encodeURIComponent(approval_id)}/claim`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Collab Approval Complete
     *  POST /api/v1/orchestrator/runs/{runId}/approvals/{approvalId}/complete */
    collab_approval_complete(run_id, approval_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/approvals/${encodeURIComponent(approval_id)}/complete`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Collab Approval Decide
     *  POST /api/v1/orchestrator/runs/{runId}/approvals/{approvalId}/decision */
    collab_approval_decide(run_id, approval_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/approvals/${encodeURIComponent(approval_id)}/decision`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Artifacts
     *  GET /api/v1/orchestrator/runs/{runId}/artifacts */
    list_artifacts(run_id, state, kind, limit, cursor) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/artifacts`;
        const params = { "state": state, "kind": kind, "limit": limit, "cursor": cursor };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Upload Artifact
     *  POST /api/v1/orchestrator/runs/{runId}/artifacts */
    upload_artifact(run_id, name, kind, request_id, retention_days, derived_from, change_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/artifacts`;
        const params = { "name": name, "kind": kind, "requestId": request_id, "retentionDays": retention_days, "derivedFrom": derived_from, "changeId": change_id };
        return this.http.request(this.baseUrl, _path, "POST", undefined, params);
    }
    /** Get Artifact
     *  GET /api/v1/orchestrator/runs/{runId}/artifacts/{artifactId} */
    get_artifact(run_id, artifact_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/artifacts/${encodeURIComponent(artifact_id)}`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Delete Artifact
     *  DELETE /api/v1/orchestrator/runs/{runId}/artifacts/{artifactId} */
    delete_artifact(run_id, artifact_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/artifacts/${encodeURIComponent(artifact_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Artifact Content
     *  GET /api/v1/orchestrator/runs/{runId}/artifacts/{artifactId}/content */
    artifact_content(run_id, artifact_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/artifacts/${encodeURIComponent(artifact_id)}/content`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Artifact Preview
     *  GET /api/v1/orchestrator/runs/{runId}/artifacts/{artifactId}/preview */
    artifact_preview(run_id, artifact_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/artifacts/${encodeURIComponent(artifact_id)}/preview`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** List Changes
     *  GET /api/v1/orchestrator/runs/{runId}/changes */
    list_changes(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/changes`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Review Change
     *  POST /api/v1/orchestrator/runs/{runId}/changes/{changeId}/review */
    review_change(run_id, change_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/changes/${encodeURIComponent(change_id)}/review`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Collab History
     *  GET /api/v1/orchestrator/runs/{runId}/collab-history */
    collab_history(run_id, after, limit, schema_version) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/collab-history`;
        const params = { "after": after, "limit": limit, "schemaVersion": schema_version };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Completion Read
     *  GET /api/v1/orchestrator/runs/{runId}/completion */
    completion_read(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/completion`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Completion Accept
     *  POST /api/v1/orchestrator/runs/{runId}/completion/accept */
    completion_accept(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/completion/accept`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Completion Reject
     *  POST /api/v1/orchestrator/runs/{runId}/completion/reject */
    completion_reject(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/completion/reject`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Completion Report Route
     *  POST /api/v1/orchestrator/runs/{runId}/completion/report */
    completion_report_route(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/completion/report`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Control View
     *  GET /api/v1/orchestrator/runs/{runId}/control */
    control_view(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/control`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Control Acquire
     *  POST /api/v1/orchestrator/runs/{runId}/control/acquire */
    control_acquire(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/control/acquire`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Control Decide
     *  POST /api/v1/orchestrator/runs/{runId}/control/decide */
    control_decide(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/control/decide`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Control Release
     *  POST /api/v1/orchestrator/runs/{runId}/control/release */
    control_release(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/control/release`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Control Renew
     *  POST /api/v1/orchestrator/runs/{runId}/control/renew */
    control_renew(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/control/renew`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Control Request
     *  POST /api/v1/orchestrator/runs/{runId}/control/request */
    control_request(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/control/request`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Control Revoke
     *  POST /api/v1/orchestrator/runs/{runId}/control/revoke */
    control_revoke(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/control/revoke`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Control Transfer
     *  POST /api/v1/orchestrator/runs/{runId}/control/transfer */
    control_transfer(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/control/transfer`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Run Diff
     *  GET /api/v1/orchestrator/runs/{runId}/diff */
    run_diff(run_id, limit, offset) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/diff`;
        const params = { "limit": limit, "offset": offset };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Signal Done
     *  POST /api/v1/orchestrator/runs/{runId}/done-signal */
    signal_done(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/done-signal`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Get Engine Result
     *  GET /api/v1/orchestrator/runs/{runId}/engine */
    get_engine_result(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/engine`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Execution Limits Get
     *  GET /api/v1/orchestrator/runs/{runId}/execution-limits */
    execution_limits_get(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/execution-limits`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Execution Limits Put
     *  PUT /api/v1/orchestrator/runs/{runId}/execution-limits */
    execution_limits_put(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/execution-limits`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Execution Status
     *  GET /api/v1/orchestrator/runs/{runId}/execution-status */
    execution_status(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/execution-status`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Git Permissions
     *  GET /api/v1/orchestrator/runs/{runId}/git-permissions */
    git_permissions(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/git-permissions`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Get Git State
     *  GET /api/v1/orchestrator/runs/{runId}/git-state */
    get_git_state(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/git-state`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Put Git State
     *  PUT /api/v1/orchestrator/runs/{runId}/git-state */
    put_git_state(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/git-state`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Collab Handoff
     *  POST /api/v1/orchestrator/runs/{runId}/handoff */
    collab_handoff(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/handoff`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Collab Handoff Decide
     *  POST /api/v1/orchestrator/runs/{runId}/handoff/{handoffId} */
    collab_handoff_decide(run_id, handoff_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/handoff/${encodeURIComponent(handoff_id)}`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Submit Human Edit
     *  POST /api/v1/orchestrator/runs/{runId}/human-edit */
    submit_human_edit(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/human-edit`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Intent Get
     *  GET /api/v1/orchestrator/runs/{runId}/intent */
    intent_get(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/intent`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Intent Put
     *  PUT /api/v1/orchestrator/runs/{runId}/intent */
    intent_put(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/intent`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Intent Conflicts
     *  GET /api/v1/orchestrator/runs/{runId}/intent/conflicts */
    intent_conflicts(run_id, state) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/intent/conflicts`;
        const params = { "state": state };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Intent Resolve
     *  POST /api/v1/orchestrator/runs/{runId}/intent/conflicts/{conflictId}/resolve */
    intent_resolve(run_id, conflict_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/intent/conflicts/${encodeURIComponent(conflict_id)}/resolve`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Collab Join
     *  POST /api/v1/orchestrator/runs/{runId}/join */
    collab_join(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/join`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Collab Claim Leader
     *  POST /api/v1/orchestrator/runs/{runId}/leader */
    collab_claim_leader(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/leader`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Lineage
     *  GET /api/v1/orchestrator/runs/{runId}/lineage */
    lineage(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/lineage`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Merge Approval
     *  POST /api/v1/orchestrator/runs/{runId}/merge-approval */
    merge_approval(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/merge-approval`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Collab Roster
     *  GET /api/v1/orchestrator/runs/{runId}/participants */
    collab_roster(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/participants`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Leave
     *  POST /api/v1/orchestrator/runs/{runId}/participation/leave */
    leave(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/participation/leave`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Mute
     *  POST /api/v1/orchestrator/runs/{runId}/participation/mute */
    mute(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/participation/mute`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Observe
     *  POST /api/v1/orchestrator/runs/{runId}/participation/observe */
    observe(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/participation/observe`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Participate
     *  POST /api/v1/orchestrator/runs/{runId}/participation/participate */
    participate(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/participation/participate`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Get Prefs
     *  GET /api/v1/orchestrator/runs/{runId}/participation/prefs */
    get_prefs(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/participation/prefs`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Put Prefs
     *  PUT /api/v1/orchestrator/runs/{runId}/participation/prefs */
    put_prefs(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/participation/prefs`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Rejoin
     *  POST /api/v1/orchestrator/runs/{runId}/participation/rejoin */
    rejoin(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/participation/rejoin`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Unmute
     *  POST /api/v1/orchestrator/runs/{runId}/participation/unmute */
    unmute(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/participation/unmute`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Pause Agent
     *  POST /api/v1/orchestrator/runs/{runId}/pause */
    pause_agent(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/pause`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Get Plan
     *  GET /api/v1/orchestrator/runs/{runId}/plan */
    get_plan(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/plan`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Propose Plan
     *  POST /api/v1/orchestrator/runs/{runId}/plan */
    propose_plan_2(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/plan`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Approve Plan
     *  POST /api/v1/orchestrator/runs/{runId}/plan/approve */
    approve_plan_2(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/plan/approve`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Reject Plan
     *  POST /api/v1/orchestrator/runs/{runId}/plan/reject */
    reject_plan_2(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/plan/reject`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Plans Versions List
     *  GET /api/v1/orchestrator/runs/{runId}/plan/versions */
    plans_versions_list(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/plan/versions`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Plans Versions Create
     *  POST /api/v1/orchestrator/runs/{runId}/plan/versions */
    plans_versions_create(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/plan/versions`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Plans Version Get
     *  GET /api/v1/orchestrator/runs/{runId}/plan/versions/{n} */
    plans_version_get(run_id, n) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/plan/versions/${encodeURIComponent(n)}`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Plans Version Approve
     *  POST /api/v1/orchestrator/runs/{runId}/plan/versions/{n}/approve */
    plans_version_approve(run_id, n) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/plan/versions/${encodeURIComponent(n)}/approve`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Plans Comments List
     *  GET /api/v1/orchestrator/runs/{runId}/plan/versions/{n}/comments */
    plans_comments_list(run_id, n, limit, after) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/plan/versions/${encodeURIComponent(n)}/comments`;
        const params = { "limit": limit, "after": after };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Plans Comments Create
     *  POST /api/v1/orchestrator/runs/{runId}/plan/versions/{n}/comments */
    plans_comments_create(run_id, n, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/plan/versions/${encodeURIComponent(n)}/comments`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Plans Version Diff
     *  GET /api/v1/orchestrator/runs/{runId}/plan/versions/{n}/diff */
    plans_version_diff(run_id, n, against) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/plan/versions/${encodeURIComponent(n)}/diff`;
        const params = { "against": against };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Plans Edits List
     *  GET /api/v1/orchestrator/runs/{runId}/plan/versions/{n}/edits */
    plans_edits_list(run_id, n) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/plan/versions/${encodeURIComponent(n)}/edits`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Plans Edits Propose
     *  POST /api/v1/orchestrator/runs/{runId}/plan/versions/{n}/edits */
    plans_edits_propose(run_id, n, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/plan/versions/${encodeURIComponent(n)}/edits`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Plans Edits Decide
     *  POST /api/v1/orchestrator/runs/{runId}/plan/versions/{n}/edits/{editId}/decide */
    plans_edits_decide(run_id, n, edit_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/plan/versions/${encodeURIComponent(n)}/edits/${encodeURIComponent(edit_id)}/decide`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Get Presence
     *  GET /api/v1/orchestrator/runs/{runId}/presence */
    get_presence(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/presence`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Heartbeat
     *  POST /api/v1/orchestrator/runs/{runId}/presence/heartbeat */
    heartbeat(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/presence/heartbeat`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Presence Leave
     *  POST /api/v1/orchestrator/runs/{runId}/presence/leave */
    presence_leave(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/presence/leave`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Collab Redirect
     *  POST /api/v1/orchestrator/runs/{runId}/redirect */
    collab_redirect(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/redirect`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Resume Agent
     *  POST /api/v1/orchestrator/runs/{runId}/resume */
    resume_agent(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/resume`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Retry Route
     *  POST /api/v1/orchestrator/runs/{runId}/retry */
    retry_route(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/retry`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Collab Staged Approvals
     *  POST /api/v1/orchestrator/runs/{runId}/staged/approvals */
    collab_staged_approvals(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/staged/approvals`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Start Run
     *  POST /api/v1/orchestrator/runs/{runId}/start */
    start_run(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/start`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Get Run Status
     *  GET /api/v1/orchestrator/runs/{runId}/status */
    get_run_status(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/status`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Stop Agent
     *  POST /api/v1/orchestrator/runs/{runId}/stop */
    stop_agent(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/stop`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Subtasks Get
     *  GET /api/v1/orchestrator/runs/{runId}/subtasks */
    subtasks_get(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/subtasks`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Subtasks Post
     *  POST /api/v1/orchestrator/runs/{runId}/subtasks */
    subtasks_post(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/subtasks`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Subtasks Patch
     *  PATCH /api/v1/orchestrator/runs/{runId}/subtasks/{subtaskId} */
    subtasks_patch(run_id, subtask_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/subtasks/${encodeURIComponent(subtask_id)}`;
        return this.http.request(this.baseUrl, _path, "PATCH", body);
    }
    /** Subtasks Block
     *  POST /api/v1/orchestrator/runs/{runId}/subtasks/{subtaskId}/block */
    subtasks_block(run_id, subtask_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/subtasks/${encodeURIComponent(subtask_id)}/block`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Subtasks Complete
     *  POST /api/v1/orchestrator/runs/{runId}/subtasks/{subtaskId}/complete */
    subtasks_complete(run_id, subtask_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/subtasks/${encodeURIComponent(subtask_id)}/complete`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Subtasks Evidence
     *  POST /api/v1/orchestrator/runs/{runId}/subtasks/{subtaskId}/evidence */
    subtasks_evidence(run_id, subtask_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/subtasks/${encodeURIComponent(subtask_id)}/evidence`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Subtasks Unblock
     *  POST /api/v1/orchestrator/runs/{runId}/subtasks/{subtaskId}/unblock */
    subtasks_unblock(run_id, subtask_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/subtasks/${encodeURIComponent(subtask_id)}/unblock`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Plans Task Get
     *  GET /api/v1/orchestrator/runs/{runId}/task */
    plans_task_get(run_id, version) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/task`;
        const params = { "version": version };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Plans Task Put
     *  PUT /api/v1/orchestrator/runs/{runId}/task */
    plans_task_put(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/task`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Run Comments List
     *  GET /api/v1/orchestrator/runs/{scopeId}/comments */
    run_comments_list(scope_id, limit, after) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(scope_id)}/comments`;
        const params = { "limit": limit, "after": after };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Run Comments Create
     *  POST /api/v1/orchestrator/runs/{scopeId}/comments */
    run_comments_create(scope_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(scope_id)}/comments`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Run Comments Export
     *  GET /api/v1/orchestrator/runs/{scopeId}/comments/export */
    run_comments_export(scope_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(scope_id)}/comments/export`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Run Comments Delete
     *  DELETE /api/v1/orchestrator/runs/{scopeId}/comments/{commentId} */
    run_comments_delete(scope_id, comment_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(scope_id)}/comments/${encodeURIComponent(comment_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Run Comments Edit
     *  PATCH /api/v1/orchestrator/runs/{scopeId}/comments/{commentId} */
    run_comments_edit(scope_id, comment_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(scope_id)}/comments/${encodeURIComponent(comment_id)}`;
        return this.http.request(this.baseUrl, _path, "PATCH", body);
    }
    /** Run Comments History
     *  GET /api/v1/orchestrator/runs/{scopeId}/comments/{commentId}/history */
    run_comments_history(scope_id, comment_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(scope_id)}/comments/${encodeURIComponent(comment_id)}/history`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Run Comments Moderate
     *  POST /api/v1/orchestrator/runs/{scopeId}/comments/{commentId}/moderate */
    run_comments_moderate(scope_id, comment_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(scope_id)}/comments/${encodeURIComponent(comment_id)}/moderate`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Run Comments Promote
     *  POST /api/v1/orchestrator/runs/{scopeId}/comments/{commentId}/promote */
    run_comments_promote(scope_id, comment_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(scope_id)}/comments/${encodeURIComponent(comment_id)}/promote`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Run Comments React
     *  POST /api/v1/orchestrator/runs/{scopeId}/comments/{commentId}/reactions */
    run_comments_react(scope_id, comment_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(scope_id)}/comments/${encodeURIComponent(comment_id)}/reactions`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Search Route
     *  GET /api/v1/orchestrator/search */
    search_route(q, kind, project_id, participant_id, status, approval_state, from_, to, limit, cursor) {
        const _path = `/api/v1/orchestrator/search`;
        const params = { "q": q, "kind": kind, "projectId": project_id, "participantId": participant_id, "status": status, "approvalState": approval_state, "from": from_, "to": to, "limit": limit, "cursor": cursor };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Search Status Route
     *  GET /api/v1/orchestrator/search/status */
    search_status_route() {
        const _path = `/api/v1/orchestrator/search/status`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Session Comments List
     *  GET /api/v1/orchestrator/sessions/{scopeId}/comments */
    session_comments_list(scope_id, limit, after) {
        const _path = `/api/v1/orchestrator/sessions/${encodeURIComponent(scope_id)}/comments`;
        const params = { "limit": limit, "after": after };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Session Comments Create
     *  POST /api/v1/orchestrator/sessions/{scopeId}/comments */
    session_comments_create(scope_id, body) {
        const _path = `/api/v1/orchestrator/sessions/${encodeURIComponent(scope_id)}/comments`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Session Comments Export
     *  GET /api/v1/orchestrator/sessions/{scopeId}/comments/export */
    session_comments_export(scope_id) {
        const _path = `/api/v1/orchestrator/sessions/${encodeURIComponent(scope_id)}/comments/export`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Session Comments Delete
     *  DELETE /api/v1/orchestrator/sessions/{scopeId}/comments/{commentId} */
    session_comments_delete(scope_id, comment_id) {
        const _path = `/api/v1/orchestrator/sessions/${encodeURIComponent(scope_id)}/comments/${encodeURIComponent(comment_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Session Comments Edit
     *  PATCH /api/v1/orchestrator/sessions/{scopeId}/comments/{commentId} */
    session_comments_edit(scope_id, comment_id, body) {
        const _path = `/api/v1/orchestrator/sessions/${encodeURIComponent(scope_id)}/comments/${encodeURIComponent(comment_id)}`;
        return this.http.request(this.baseUrl, _path, "PATCH", body);
    }
    /** Session Comments History
     *  GET /api/v1/orchestrator/sessions/{scopeId}/comments/{commentId}/history */
    session_comments_history(scope_id, comment_id) {
        const _path = `/api/v1/orchestrator/sessions/${encodeURIComponent(scope_id)}/comments/${encodeURIComponent(comment_id)}/history`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Session Comments Moderate
     *  POST /api/v1/orchestrator/sessions/{scopeId}/comments/{commentId}/moderate */
    session_comments_moderate(scope_id, comment_id, body) {
        const _path = `/api/v1/orchestrator/sessions/${encodeURIComponent(scope_id)}/comments/${encodeURIComponent(comment_id)}/moderate`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Session Comments Promote
     *  POST /api/v1/orchestrator/sessions/{scopeId}/comments/{commentId}/promote */
    session_comments_promote(scope_id, comment_id, body) {
        const _path = `/api/v1/orchestrator/sessions/${encodeURIComponent(scope_id)}/comments/${encodeURIComponent(comment_id)}/promote`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Session Comments React
     *  POST /api/v1/orchestrator/sessions/{scopeId}/comments/{commentId}/reactions */
    session_comments_react(scope_id, comment_id, body) {
        const _path = `/api/v1/orchestrator/sessions/${encodeURIComponent(scope_id)}/comments/${encodeURIComponent(comment_id)}/reactions`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
}
/* Module surface (mirrors __all__ in clients/python):
 * export class OrchestratorClient
 * export interface/type AbandonBody
 * export interface/type AggregateStepOut
 * export interface/type ApprovalDecision
 * export interface/type ApprovalDigest
 * export interface/type ApprovalOutcome
 * export interface/type ApprovalRequest
 * export interface/type BlockBody
 * export interface/type BudgetDegradedOut
 * export interface/type CommentBody
 * export interface/type CompleteBody
 * export interface/type CreateBody
 * export interface/type DecisionBody
 * export interface/type DeletionBody
 * export interface/type DemoSeedResponse
 * export interface/type DoneSignalRequest
 * export interface/type DoneSignalResponse
 * export interface/type EditBody
 * export interface/type ErrorDetail
 * export interface/type ErrorEnvelope
 * export interface/type EvidenceBody
 * export interface/type ExportBody
 * export interface/type GitStateBody
 * export interface/type HTTPValidationError
 * export interface/type HandoffDecision
 * export interface/type HandoffRequest
 * export interface/type HeartbeatRequest
 * export interface/type HumanEditRequest
 * export interface/type IntentBody
 * export interface/type LeaseBody
 * export interface/type LeaveRequest
 * export interface/type LimitsBody
 * export interface/type MergeBody
 * export interface/type MergedIntent
 * export interface/type ModerateBody
 * export interface/type OutputRef
 * export interface/type PatchOp
 * export interface/type PlanActionRequest
 * export interface/type PlanActionResponse
 * export interface/type PlanContent
 * export interface/type PlanDecisionResponse
 * export interface/type PlanOut
 * export interface/type PlanProposedResponse
 * export interface/type PlanRejectedResponse
 * export interface/type PrefsRequest
 * export interface/type ProgressBody
 * export interface/type PromoteBody
 * export interface/type ProposeBody
 * export interface/type ProposePlanGateRequest
 * export interface/type ProposePlanRequest
 * export interface/type ProposePlanResponse
 * export interface/type ReactBody
 * export interface/type ReasonBody
 * export interface/type ReassignBody
 * export interface/type RedirectRequest
 * export interface/type RejectBody
 * export interface/type ReportBody
 * export interface/type ResolveBody
 * export interface/type RetryBody
 * export interface/type ReviewBody
 * export interface/type ReviewFindingsOut
 * export interface/type RevokeBody
 * export interface/type RunAggregateResponse
 * export interface/type RunIdStatusResponse
 * export interface/type RunItem
 * export interface/type RunStateResponse
 * export interface/type RunStatusResponse
 * export interface/type RunsPage
 * export interface/type ScopeBody
 * export interface/type SettingsBody
 * export interface/type SettingsRequest
 * export interface/type SpawnBody
 * export interface/type StartRunResponse
 * export interface/type SubtaskCreate
 * export interface/type SubtaskPatch
 * export interface/type TaskBody
 * export interface/type ToolCallBody
 * export interface/type TransferBody
 * export interface/type UnblockBody
 * export interface/type UpgradeCtaOut
 * export interface/type ValidationError
 * export interface/type VersionBody
 * export interface/type _ErrorDetail
 * export interface/type _ErrorEnvelope
 * export interface/type harness_orchestrator__control__DecideBody
 * export interface/type harness_orchestrator__execution__DecideBody
 * export interface/type harness_orchestrator__plans__DecideBody
 */
