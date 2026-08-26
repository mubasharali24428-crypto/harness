/** Client for the harness-review service (default http://localhost:8008). */
export class ReviewClient {
    http;
    baseUrl;
    /** @param http  shared transport; @param baseUrl resolved by Harness. */
    constructor(http, baseUrl = "") {
        this.http = http;
        this.baseUrl = baseUrl;
    }
    /** Get Reviewer Threshold Settings
     *  GET /api/v1/reviews/settings/thresholds */
    get_reviewer_threshold_settings() {
        const _path = `/api/v1/reviews/settings/thresholds`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Get Review
     *  GET /api/v1/reviews/{pr_id} */
    get_review(pr_id, diff_text) {
        const _path = `/api/v1/reviews/${encodeURIComponent(pr_id)}`;
        const params = { "diff_text": diff_text };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Approve Pr
     *  POST /api/v1/reviews/{pr_id}/approve */
    approve_pr(pr_id, body) {
        const _path = `/api/v1/reviews/${encodeURIComponent(pr_id)}/approve`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Comments
     *  GET /api/v1/reviews/{pr_id}/comments */
    list_comments(pr_id) {
        const _path = `/api/v1/reviews/${encodeURIComponent(pr_id)}/comments`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Add Comment
     *  POST /api/v1/reviews/{pr_id}/comments */
    add_comment(pr_id, body) {
        const _path = `/api/v1/reviews/${encodeURIComponent(pr_id)}/comments`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Deep Link To Github
     *  GET /api/v1/reviews/{pr_id}/deep-link */
    deep_link_to_github(pr_id) {
        const _path = `/api/v1/reviews/${encodeURIComponent(pr_id)}/deep-link`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Request Changes
     *  POST /api/v1/reviews/{pr_id}/request-changes */
    request_changes(pr_id, body) {
        const _path = `/api/v1/reviews/${encodeURIComponent(pr_id)}/request-changes`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Get Risk Flags
     *  GET /api/v1/reviews/{pr_id}/risks */
    get_risk_flags(pr_id, diff_text) {
        const _path = `/api/v1/reviews/${encodeURIComponent(pr_id)}/risks`;
        const params = { "diff_text": diff_text };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Get Verdict
     *  GET /api/v1/reviews/{pr_id}/verdict */
    get_verdict(pr_id, diff_text) {
        const _path = `/api/v1/reviews/${encodeURIComponent(pr_id)}/verdict`;
        const params = { "diff_text": diff_text };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Health
     *  GET /health */
    health() {
        const _path = `/health`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
}
/* Module surface (mirrors __all__ in clients/python):
 * export class ReviewClient
 * export interface/type ApproveRequest
 * export interface/type ChangeStatus
 * export interface/type ChangedFile
 * export interface/type HTTPValidationError
 * export interface/type InlineCommentRequest
 * export interface/type RequestChangesRequest
 * export interface/type ReviewDataResponse
 * export interface/type RiskFlag
 * export interface/type RiskSeverity
 * export interface/type ValidationError
 */
