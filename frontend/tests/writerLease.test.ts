/**
 * Per-tab writer-lease identity (INV-0407): sessionStorage claims survive a same-tab
 * reload but die with the tab; the localStorage heartbeat lets a second tab detect a
 * live writer and go read-only without ever calling ``resume``.
 */
import { beforeEach, describe, expect, it } from "vitest";
import {
  CLAIM_STALE_MS,
  adoptWriter,
  clearClaim,
  clearWriter,
  foreignClaimActive,
  storedWriter,
  tabId,
  writeClaim,
} from "../src/state/writerLease";

describe("writerLease (per-tab identity)", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  it("keeps a stable tab id within the tab and a claimable writer id", () => {
    expect(tabId(() => "tab-a")).toBe("tab-a");
    expect(tabId()).toBe("tab-a");
    expect(storedWriter()).toBeNull();
    adoptWriter("writer-1");
    expect(storedWriter()).toBe("writer-1");
    clearWriter();
    expect(storedWriter()).toBeNull();
  });

  it("detects a live foreign claim (second tab goes read-only)", () => {
    const now = Date.now();
    writeClaim("sess-1", "tab-a", now);
    expect(foreignClaimActive("sess-1", "tab-b", now + 1_000)).toBe(true);
    // my own claim is not foreign (same-tab reload reclaims it)
    expect(foreignClaimActive("sess-1", "tab-a", now + 1_000)).toBe(false);
    // a different session's claim does not block this session
    expect(foreignClaimActive("sess-2", "tab-b", now + 1_000)).toBe(false);
  });

  it("stale claims stop blocking (dead writer's tab can be replaced)", () => {
    const now = Date.now();
    writeClaim("sess-1", "tab-a", now);
    expect(foreignClaimActive("sess-1", "tab-b", now + CLAIM_STALE_MS + 1)).toBe(false);
  });

  it("clears only my own claim, never another tab's", () => {
    writeClaim("sess-1", "tab-a");
    clearClaim("sess-1", "tab-b");
    expect(foreignClaimActive("sess-1", "tab-c")).toBe(true);
    clearClaim("sess-1", "tab-a");
    expect(foreignClaimActive("sess-1", "tab-c")).toBe(false);
  });
});
