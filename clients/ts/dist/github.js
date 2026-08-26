/** Client for the harness-github service (default http://localhost:8002). */
export class GithubClient {
    http;
    baseUrl;
    /** @param http  shared transport; @param baseUrl resolved by Harness. */
    constructor(http, baseUrl = "") {
        this.http = http;
        this.baseUrl = baseUrl;
    }
    /** Connection Status
     *  GET /api/v1/github/connection */
    connection_status() {
        const _path = `/api/v1/github/connection`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Oauth Callback
     *  GET /api/v1/github/oauth/callback */
    oauth_callback(code, state) {
        const _path = `/api/v1/github/oauth/callback`;
        const params = { "code": code, "state": state };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Oauth Start
     *  GET /api/v1/github/oauth/start */
    oauth_start(ticket) {
        const _path = `/api/v1/github/oauth/start`;
        const params = { "ticket": ticket };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Oauth Start Ticket
     *  POST /api/v1/github/oauth/start-ticket */
    oauth_start_ticket() {
        const _path = `/api/v1/github/oauth/start-ticket`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Health Extended
     *  GET /api/v1/health-extended */
    health_extended() {
        const _path = `/api/v1/health-extended`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Create Branch
     *  POST /api/v1/projects/{project_id}/branches */
    create_branch(project_id, base_ref, new_branch) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/branches`;
        const params = { "baseRef": base_ref, "newBranch": new_branch };
        return this.http.request(this.baseUrl, _path, "POST", undefined, params);
    }
    /** Commit Files
     *  POST /api/v1/projects/{project_id}/commits */
    commit_files(project_id, body) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/commits`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Read File
     *  GET /api/v1/projects/{project_id}/file */
    read_file(project_id, ref, path) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/file`;
        const params = { "ref": ref, "path": path };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Open Draft Pr
     *  POST /api/v1/projects/{project_id}/pulls/draft */
    open_draft_pr(project_id, body) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/pulls/draft`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Pr Status
     *  GET /api/v1/projects/{project_id}/pulls/{pr_number} */
    pr_status(project_id, pr_number) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/pulls/${encodeURIComponent(pr_number)}`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Pr Diff
     *  GET /api/v1/projects/{project_id}/pulls/{pr_number}/diff */
    pr_diff(project_id, pr_number) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/pulls/${encodeURIComponent(pr_number)}/diff`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Connect Repo
     *  POST /api/v1/projects/{project_id}/repo/connect */
    connect_repo(project_id, body) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/repo/connect`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Run Pr Id
     *  GET /api/v1/projects/{project_id}/runs/{run_id}/pr */
    run_pr_id(project_id, run_id) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/runs/${encodeURIComponent(run_id)}/pr`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Read Tree
     *  GET /api/v1/projects/{project_id}/tree */
    read_tree(project_id, ref) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/tree`;
        const params = { "ref": ref };
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
 * export class GithubClient
 * export interface/type CommitBody
 * export interface/type ConnectBody
 * export interface/type DraftPRBody
 * export interface/type HTTPValidationError
 * export interface/type ValidationError
 */
