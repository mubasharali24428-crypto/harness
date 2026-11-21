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
    /** Disconnect Github
     *  POST /api/v1/github/disconnect */
    disconnect_github() {
        const _path = `/api/v1/github/disconnect`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
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
    /** Get Github Writes
     *  GET /api/v1/projects/{project_id}/github/writes */
    get_github_writes(project_id) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/github/writes`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Set Github Writes
     *  PUT /api/v1/projects/{project_id}/github/writes */
    set_github_writes(project_id, body) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/github/writes`;
        return this.http.request(this.baseUrl, _path, "PUT", body);
    }
    /** List Issues
     *  GET /api/v1/projects/{project_id}/issues */
    list_issues(project_id, state) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/issues`;
        const params = { "state": state };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Create Issue
     *  POST /api/v1/projects/{project_id}/issues */
    create_issue(project_id, body) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/issues`;
        return this.http.request(this.baseUrl, _path, "POST", body);
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
    /** Post Verdict Comment
     *  POST /api/v1/projects/{project_id}/pulls/{pr_number}/verdict-comment */
    post_verdict_comment(project_id, pr_number, body) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/pulls/${encodeURIComponent(pr_number)}/verdict-comment`;
        return this.http.request(this.baseUrl, _path, "POST", body);
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
    /** Ready
     *  GET /ready */
    ready_ready_get() {
        const _path = `/ready`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
}
/* Module surface (mirrors __all__ in clients/python):
 * export class GithubClient
 * export interface/type CommitBody
 * export interface/type ConnectBody
 * export interface/type DraftPRBody
 * export interface/type HTTPValidationError
 * export interface/type IssueBody
 * export interface/type ValidationError
 * export interface/type VerdictCommentBody
 * export interface/type WritesBody
 */
