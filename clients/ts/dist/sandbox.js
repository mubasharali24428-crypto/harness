/** Client for the harness-sandbox service (default http://localhost:8005). */
export class SandboxClient {
    http;
    baseUrl;
    /** @param http  shared transport; @param baseUrl resolved by Harness. */
    constructor(http, baseUrl = "") {
        this.http = http;
        this.baseUrl = baseUrl;
    }
    /** Create Task
     *  POST /api/v1/sandbox/tasks */
    create_task(body) {
        const _path = `/api/v1/sandbox/tasks`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Destroy Task
     *  POST /api/v1/sandbox/tasks/{taskId}/destroy */
    destroy_task(task_id) {
        const _path = `/api/v1/sandbox/tasks/${encodeURIComponent(task_id)}/destroy`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Apply Edit
     *  POST /api/v1/sandbox/tasks/{taskId}/edits */
    apply_edit(task_id, body) {
        const _path = `/api/v1/sandbox/tasks/${encodeURIComponent(task_id)}/edits`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Exec Command
     *  POST /api/v1/sandbox/tasks/{taskId}/exec */
    exec_command(task_id, body) {
        const _path = `/api/v1/sandbox/tasks/${encodeURIComponent(task_id)}/exec`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Checkpoints
     *  GET /api/v1/sandbox/{projectId}/checkpoints */
    list_checkpoints(project_id) {
        const _path = `/api/v1/sandbox/${encodeURIComponent(project_id)}/checkpoints`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Checkpoint Diff
     *  GET /api/v1/sandbox/{projectId}/checkpoints/{sha}/diff */
    checkpoint_diff(project_id, sha) {
        const _path = `/api/v1/sandbox/${encodeURIComponent(project_id)}/checkpoints/${encodeURIComponent(sha)}/diff`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Get Project Tree
     *  GET /api/v1/sandbox/{projectId}/tree */
    get_project_tree(project_id, path) {
        const _path = `/api/v1/sandbox/${encodeURIComponent(project_id)}/tree`;
        const params = { "path": path };
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
 * export class SandboxClient
 * export interface/type ApplyEditRequest
 * export interface/type CreateTaskRequest
 * export interface/type ExecCommandRequest
 * export interface/type HTTPValidationError
 * export interface/type StructuredExecResult
 * export interface/type TaskHandle
 * export interface/type TreeEntry
 * export interface/type TreeListing
 * export interface/type ValidationError
 */
