/** Client for the harness-meeting service (default http://localhost:8006). */
export class MeetingClient {
    http;
    baseUrl;
    /** @param http  shared transport; @param baseUrl resolved by Harness. */
    constructor(http, baseUrl = "") {
        this.http = http;
        this.baseUrl = baseUrl;
    }
    /** Create Meeting
     *  POST /api/v1/meetings */
    create_meeting(body) {
        const _path = `/api/v1/meetings`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Get Meeting
     *  GET /api/v1/meetings/{meetingId} */
    get_meeting(meeting_id) {
        const _path = `/api/v1/meetings/${encodeURIComponent(meeting_id)}`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Invoke Ai
     *  POST /api/v1/meetings/{meetingId}/ai */
    invoke_ai(meeting_id, body) {
        const _path = `/api/v1/meetings/${encodeURIComponent(meeting_id)}/ai`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Get Brief
     *  GET /api/v1/meetings/{meetingId}/brief */
    get_brief(meeting_id) {
        const _path = `/api/v1/meetings/${encodeURIComponent(meeting_id)}/brief`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Generate Brief
     *  POST /api/v1/meetings/{meetingId}/brief */
    generate_brief(meeting_id) {
        const _path = `/api/v1/meetings/${encodeURIComponent(meeting_id)}/brief`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Approve Brief
     *  POST /api/v1/meetings/{meetingId}/brief/{briefId}/approve */
    approve_brief(meeting_id, brief_id, body) {
        const _path = `/api/v1/meetings/${encodeURIComponent(meeting_id)}/brief/${encodeURIComponent(brief_id)}/approve`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Reject Brief
     *  POST /api/v1/meetings/{meetingId}/brief/{briefId}/reject */
    reject_brief(meeting_id, brief_id, body) {
        const _path = `/api/v1/meetings/${encodeURIComponent(meeting_id)}/brief/${encodeURIComponent(brief_id)}/reject`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Close Meeting
     *  POST /api/v1/meetings/{meetingId}/close */
    close_meeting(meeting_id) {
        const _path = `/api/v1/meetings/${encodeURIComponent(meeting_id)}/close`;
        return this.http.request(this.baseUrl, _path, "POST", undefined);
    }
    /** Get Messages
     *  GET /api/v1/meetings/{meetingId}/messages */
    get_messages(meeting_id, limit, before) {
        const _path = `/api/v1/meetings/${encodeURIComponent(meeting_id)}/messages`;
        const params = { "limit": limit, "before": before };
        return this.http.request(this.baseUrl, _path, "GET", undefined, params);
    }
    /** Post Message
     *  POST /api/v1/meetings/{meetingId}/messages */
    post_message(meeting_id, body) {
        const _path = `/api/v1/meetings/${encodeURIComponent(meeting_id)}/messages`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** List Prompts
     *  GET /api/v1/meetings/{meetingId}/prompts */
    list_prompts(meeting_id) {
        const _path = `/api/v1/meetings/${encodeURIComponent(meeting_id)}/prompts`;
        return this.http.request(this.baseUrl, _path, "GET", undefined);
    }
    /** Create Prompt
     *  POST /api/v1/meetings/{meetingId}/prompts */
    create_prompt(meeting_id, body) {
        const _path = `/api/v1/meetings/${encodeURIComponent(meeting_id)}/prompts`;
        return this.http.request(this.baseUrl, _path, "POST", body);
    }
    /** Generate Summary
     *  POST /api/v1/meetings/{meetingId}/summary */
    generate_summary(meeting_id) {
        const _path = `/api/v1/meetings/${encodeURIComponent(meeting_id)}/summary`;
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
 * export class MeetingClient
 * export interface/type AiInvokeResponse
 * export interface/type ApproveBriefRequest
 * export interface/type BriefOut
 * export interface/type ChatMessageOut
 * export interface/type CreateMeetingRequest
 * export interface/type CreatePromptRequest
 * export interface/type HTTPValidationError
 * export interface/type InvokeAIRequest
 * export interface/type MeetingOut
 * export interface/type MessagePageOut
 * export interface/type NotInvokedResponse
 * export interface/type PagedChatMessageOut
 * export interface/type PostMessageRequest
 * export interface/type PromptOut
 * export interface/type SummaryOut
 * export interface/type ValidationError
 * export interface/type _ErrorDetail
 * export interface/type _ErrorEnvelope
 */
