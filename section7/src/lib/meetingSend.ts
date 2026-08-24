// Mission 12 lane E1 — single-call @ai send flow (H-002 client half).
//
// S6 replies server-side: POST /messages with "@ai …" content is ALL the
// client needs. The old second invokeAi POST was a duplicate that raced the
// real reply and double-posted; it is gone. While S6 works (key decrypt +
// model call), the caller shows a thinking indicator via onThinking.

import { getMessages, postMessage, type MeetingMsg } from "@/lib/api";

export interface SendWithAiArgs {
  meetingId: string;
  userId: string;
  /** Raw draft text (no @ai prefix); it is prefixed here exactly once. */
  content: string;
  token: string;
  onMessages: (messages: MeetingMsg[]) => void;
  onThinking: (thinking: boolean) => void;
}

/** One @ai send = one network call (+ transcript refresh), thinking-wrapped. */
export async function sendWithAi({
  meetingId,
  userId,
  content,
  token,
  onMessages,
  onThinking,
}: SendWithAiArgs): Promise<void> {
  onThinking(true);
  try {
    // Single invocation: S6 detects "@ai" on the message and answers itself
    // (real model via team BYO key, or honest fallback when none stored).
    await postMessage(meetingId, userId, `@ai ${content}`, token);
    await getMessages(meetingId, token)
      .then(onMessages)
      .catch(() => {});
  } finally {
    onThinking(false);
  }
}
