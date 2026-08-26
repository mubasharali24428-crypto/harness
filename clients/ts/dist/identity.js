/** Client for the harness-identity service (default http://localhost:8001). */
export class IdentityClient {
    http;
    baseUrl;
    /** @param http  shared transport; @param baseUrl resolved by Harness. */
    constructor(http, baseUrl = "") {
        this.http = http;
        this.baseUrl = baseUrl;
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
    replay_events(project_id, after_seq, limit) {
        const _path = `/api/v1/projects/${encodeURIComponent(project_id)}/events/replay`;
        const params = { "after_seq": after_seq, "limit": limit };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
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
    /** List Invites
     *  GET /api/v1/teams/{team_id}/invites */
    list_invites(team_id, status) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/invites`;
        const params = { "status": status };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Create Invite
     *  POST /api/v1/teams/{team_id}/invites */
    create_invite(team_id, body) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/invites`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Revoke Invite
     *  DELETE /api/v1/teams/{team_id}/invites/{invite_id} */
    revoke_invite(team_id, invite_id) {
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
    list_members(team_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/members`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Remove Member
     *  DELETE /api/v1/teams/{team_id}/members/{user_id} */
    remove_member(team_id, user_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/members/${encodeURIComponent(user_id)}`;
        return this.http.request(this.baseUrl, _path, "DELETE", undefined);
    }
    /** Change Member Role
     *  PATCH /api/v1/teams/{team_id}/members/{user_id} */
    change_member_role(team_id, user_id, body) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/members/${encodeURIComponent(user_id)}`;
        return this.http.request(this.baseUrl, _path, "PATCH", body);
    }
    /** Add Agent Seat
     *  POST /api/v1/teams/{team_id}/seats/agent */
    add_agent_seat(team_id) {
        const _path = `/api/v1/teams/${encodeURIComponent(team_id)}/seats/agent`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
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
    /** Issue Ws Ticket
     *  POST /api/v1/ws-ticket */
    issue_ws_ticket(body) {
        const _path = `/api/v1/ws-ticket`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Health
     *  GET /health */
    health(deep) {
        const _path = `/health`;
        const params = { "deep": deep };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
}
/* Module surface (mirrors __all__ in clients/python):
 * export class IdentityClient
 * export interface/type CreateProjectBody
 * export interface/type ErrorDetail
 * export interface/type ErrorEnvelope
 * export interface/type EventEnvelopeOut
 * export interface/type EventReplayResponse
 * export interface/type HTTPValidationError
 * export interface/type InviteCreateBody
 * export interface/type InviteRedeemBody
 * export interface/type KeyCreateBody
 * export interface/type KeyCreatedResponse
 * export interface/type KeyItemResponse
 * export interface/type KeyListResponse
 * export interface/type LoginRequest
 * export interface/type MemberItem
 * export interface/type MemberListResponse
 * export interface/type MemberRoleBody
 * export interface/type OkResponse
 * export interface/type PlanUpdateBody
 * export interface/type PrincipalResponse
 * export interface/type ProjectCreatedResponse
 * export interface/type ProjectListResponse
 * export interface/type ProjectOut
 * export interface/type RulesBody
 * export interface/type SessionResponse
 * export interface/type ThresholdsBody
 * export interface/type ValidationError
 * export interface/type VerifyOtpRequest
 * export interface/type WsTicketRequest
 * export interface/type WsTicketResponse
 */
