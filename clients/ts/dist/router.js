/** Client for the harness-router service (default http://localhost:8003). */
export class RouterClient {
    http;
    baseUrl;
    /** @param http  shared transport; @param baseUrl resolved by Harness. */
    constructor(http, baseUrl = "") {
        this.http = http;
        this.baseUrl = baseUrl;
    }
    /** List Models
     *  GET /api/v1/models */
    list_models() {
        const _path = `/api/v1/models`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Call Model
     *  POST /api/v1/models/call */
    call_model(body) {
        const _path = `/api/v1/models/call`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Provider Catalog
     *  GET /api/v1/models/catalog */
    provider_catalog() {
        const _path = `/api/v1/models/catalog`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Configure Role Model
     *  POST /api/v1/models/config */
    configure_role_model(body) {
        const _path = `/api/v1/models/config`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Estimate Cost
     *  POST /api/v1/models/estimate-cost */
    estimate_cost(body) {
        const _path = `/api/v1/models/estimate-cost`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Prometheus Metrics
     *  GET /api/v1/models/metrics */
    prometheus_metrics() {
        const _path = `/api/v1/models/metrics`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Get Model Prefs
     *  GET /api/v1/models/prefs */
    get_model_prefs() {
        const _path = `/api/v1/models/prefs`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Put Model Prefs
     *  PUT /api/v1/models/prefs */
    put_model_prefs(body) {
        const _path = `/api/v1/models/prefs`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** Model Spend
     *  GET /api/v1/models/spend */
    model_spend(team_id, project_id, since_days) {
        const _path = `/api/v1/models/spend`;
        const params = { "teamId": team_id, "projectId": project_id, "sinceDays": since_days };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
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
}
/* Module surface (mirrors __all__ in clients/python):
 * export class RouterClient
 * export interface/type CallModelRequest
 * export interface/type ConfigureRoleModelRequest
 * export interface/type EstimateCostRequest
 * export interface/type HTTPValidationError
 * export interface/type ModelCallOpts
 * export interface/type ModelPrefsBody
 * export interface/type ModelResult
 * export interface/type ModelRole
 * export interface/type SpendByDay
 * export interface/type SpendByModel
 * export interface/type SpendErrorDetail
 * export interface/type SpendErrorEnvelope
 * export interface/type SpendSummaryResponse
 * export interface/type SpendTotals
 * export interface/type UnattributedSpend
 * export interface/type ValidationError
 */
