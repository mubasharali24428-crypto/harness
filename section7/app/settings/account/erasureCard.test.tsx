// Parts 31/58: the account erasure card. Static render in the node env, like the other settings cells.
import { describe, expect, it } from "vitest";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ErasureCard } from "./erasureCard";

const noop = () => {};
const card = (over: Partial<React.ComponentProps<typeof ErasureCard>> = {}) =>
  renderToStaticMarkup(
    <ErasureCard typed="" onType={noop} onSubmit={noop} busy={false} problem={null} done={false} {...over} />,
  );
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

describe("ErasureCard", () => {
  it("says what happens and what stays before asking for anything", () => {
    const t = text(card());
    expect(t).toMatch(/cannot be undone/i);
    expect(t).toMatch(/can no longer sign in/i);
    expect(t).toMatch(/audit and collaboration records keep an opaque id/i);
    expect(t).toMatch(/only admin or owner/i); // the refusals are announced up front
    expect(t).toMatch(/retention hold/i);
  });

  it("asks for the email in a labelled field and keeps the button off until something is typed", () => {
    const html = card();
    expect(html).toMatch(/<label[^>]*for="erase-email"[^>]*>Type your email address to confirm/);
    expect(html).toContain('id="erase-email"');
    expect(html).toMatch(/<button[^>]*disabled[^>]*data-testid="erase-submit"|<button[^>]*data-testid="erase-submit"[^>]*disabled/);
    expect(card({ typed: "me@a.io" })).not.toMatch(/data-testid="erase-submit"[^>]*disabled|disabled[^>]*data-testid="erase-submit"/);
  });

  it("disables the button while the request is running", () => {
    expect(card({ typed: "me@a.io", busy: true })).toMatch(/disabled[^>]*data-testid="erase-submit"|data-testid="erase-submit"[^>]*disabled/);
  });

  it("shows a problem as an alert tied to the field", () => {
    const html = card({ typed: "x@y.io", problem: "That is not the email address of this account. Nothing was changed." });
    expect(html).toMatch(/role="alert"[^>]*>[^<]*That is not the email address/);
    expect(html).toContain('aria-describedby="erase-problem"');
    expect(html).toContain('id="erase-problem"');
    expect(card()).not.toContain('role="alert"');
  });

  it("after erasure shows the result and a way back, and no form", () => {
    const html = card({ done: true });
    expect(text(html)).toMatch(/your account has been erased/i);
    expect(html).toContain('href="/login"');
    expect(html).not.toContain("erase-email");
    expect(html).not.toContain("erase-submit");
  });
});
