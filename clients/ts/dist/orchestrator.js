/** Client for the harness-orchestrator service (default http://localhost:8004). */
export class OrchestratorClient {
    http;
    baseUrl;
    /** @param http  shared transport; @param baseUrl resolved by Harness. */
    constructor(http, baseUrl = "") {
        this.http = http;
        this.baseUrl = baseUrl;
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
    /** Submit Human Edit
     *  POST /api/v1/orchestrator/runs/{runId}/human-edit */
    submit_human_edit(run_id, body) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/human-edit`;
        return this.http.request(this.baseUrl, _path, "POST", body);
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
    /** Resume Agent
     *  POST /api/v1/orchestrator/runs/{runId}/resume */
    resume_agent(run_id) {
        const _path = `/api/v1/orchestrator/runs/${encodeURIComponent(run_id)}/resume`;
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
    /** Health
     *  GET /health */
    health() {
        const _path = `/health`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
}
/* Module surface (mirrors __all__ in clients/python):
 * export class OrchestratorClient
 * export interface/type AggregateStepOut
 * export interface/type DoneSignalRequest
 * export interface/type DoneSignalResponse
 * export interface/type ErrorDetail
 * export interface/type ErrorEnvelope
 * export interface/type HTTPValidationError
 * export interface/type HumanEditRequest
 * export interface/type PlanActionRequest
 * export interface/type PlanActionResponse
 * export interface/type PlanDecisionResponse
 * export interface/type PlanOut
 * export interface/type PlanProposedResponse
 * export interface/type PlanRejectedResponse
 * export interface/type ProposePlanGateRequest
 * export interface/type ProposePlanRequest
 * export interface/type ProposePlanResponse
 * export interface/type ReviewFindingsOut
 * export interface/type RunAggregateResponse
 * export interface/type RunIdStatusResponse
 * export interface/type RunItem
 * export interface/type RunStateResponse
 * export interface/type RunStatusResponse
 * export interface/type RunsPage
 * export interface/type StartRunResponse
 * export interface/type ValidationError
 * export interface/type _ErrorDetail
 * export interface/type _ErrorEnvelope
 */
