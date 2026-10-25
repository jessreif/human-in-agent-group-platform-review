/**
 * Per-tab writer-lease identity (INV-0407).
 *
 * The ``inv_writer`` cookie is shared by every tab in the browser profile, so the
 * server cannot tell "this tab holds the lease" from "a second tab just opened".
 * Each tab keeps a stable tab id and its claimed writer id in ``sessionStorage``
 * (survives a same-tab reload, dies with the tab) and sends it in ``x-writer-id``.
 * A heartbeat claim in ``localStorage`` lets a second tab go read-only without
 * ever attempting the claim; an explicit takeover overwrites the claim and the
 * superseded tab drops to read-only on its next beat (or on the server's
 * ``writer_lease_held`` / ``writer_takeover`` signal, whichever lands first).
 *
 * The residual race — two tabs booting in the same heartbeat window with no prior
 * claim — degrades to both presenting the same writer id, which the server treats
 * as one writer; command ids keep submissions idempotent.
 */

const TAB_KEY = "inv_writer_tab";
const WRITER_KEY = "inv_writer_id";
const CLAIM_PREFIX = "inv_writer_claim:";
export const CLAIM_STALE_MS = 30_000;
export const CLAIM_HEARTBEAT_MS = 10_000;

interface Claim {
  tabId: string;
  ts: number;
}

function storage(area: "session" | "local"): Storage | null {
  try {
    return area === "session" ? window.sessionStorage : window.localStorage;
  } catch {
    return null;
  }
}

/** Stable per-tab id (persists across same-tab reloads). */
export function tabId(uuid: () => string = crypto.randomUUID.bind(crypto)): string {
  const s = storage("session");
  const existing = s?.getItem(TAB_KEY);
  if (existing) return existing;
  const id = uuid();
  s?.setItem(TAB_KEY, id);
  return id;
}

/** The writer id this tab last held, or null when the tab never held the lease. */
export function storedWriter(): string | null {
  return storage("session")?.getItem(WRITER_KEY) ?? null;
}

/** Record a writer id this tab holds (after resume/takeover succeeds). */
export function adoptWriter(writer: string): void {
  storage("session")?.setItem(WRITER_KEY, writer);
}

/** Drop the tab's writer id when the lease is lost or superseded. */
export function clearWriter(): void {
  storage("session")?.removeItem(WRITER_KEY);
}

function claimKey(sessionId: string): string {
  return CLAIM_PREFIX + sessionId;
}

function readClaim(sessionId: string): Claim | null {
  const raw = storage("local")?.getItem(claimKey(sessionId));
  if (!raw) return null;
  try {
    const claim = JSON.parse(raw) as Claim;
    return typeof claim.tabId === "string" && typeof claim.ts === "number" ? claim : null;
  } catch {
    return null;
  }
}

/** Publish/refresh this tab's ownership claim. */
export function writeClaim(sessionId: string, myTabId: string, now = Date.now()): void {
  const claim: Claim = { tabId: myTabId, ts: now };
  storage("local")?.setItem(claimKey(sessionId), JSON.stringify(claim));
}

/** Remove the claim only when it is still mine (never clear another tab's). */
export function clearClaim(sessionId: string, myTabId: string): void {
  if (readClaim(sessionId)?.tabId === myTabId) {
    storage("local")?.removeItem(claimKey(sessionId));
  }
}

/** A different tab holds a fresh claim — go read-only without touching the lease. */
export function foreignClaimActive(
  sessionId: string,
  myTabId: string,
  now = Date.now(),
): boolean {
  const claim = readClaim(sessionId);
  return claim !== null && claim.tabId !== myTabId && now - claim.ts < CLAIM_STALE_MS;
}

// ----- duplicate-tab handshake (BA-27) --------------------------------------------------
//
// Browser "Duplicate Tab" clones sessionStorage wholesale, so the clone boots with the
// incumbent's tab id AND writer id — every storage-based check sees it as the same
// writer. The one thing a clone cannot copy is the incumbent's live JS heap: keep a
// per-document nonce here (never in any storage) and run a BroadcastChannel handshake
// at boot. The incumbent answers a ``hello`` naming its tab id with ``alive`` + its own
// nonce; the clone concludes it is a clone and mints a fresh tab id. A same-tab reload
// is safe by construction — the old document is gone, nobody answers, and the new
// document legitimately keeps its stable tab id.

const TAB_CHANNEL_PREFIX = "inv_tab:";
/** How long a boot waits for an incumbent's ``alive`` answer. */
export const CLONE_DETECT_MS = 250;

interface TabMessage {
  type: string;
  tabId: string;
  nonce: string;
}

function isTabMessage(value: unknown): value is TabMessage {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.type === "string" && typeof v.tabId === "string" && typeof v.nonce === "string"
  );
}

/** This document's nonce — module-scoped on purpose (clones share sessionStorage, not heaps). */
let docNonce: string | null = null;
let tabChannel: BroadcastChannel | null = null;
let tabChannelSession: string | null = null;

/**
 * The per-session BroadcastChannel, created once and kept open for the document's
 * lifetime so the incumbent half of the handshake can answer later clones.
 */
function ensureTabChannel(sessionId: string): BroadcastChannel | null {
  if (typeof BroadcastChannel === "undefined") return null;
  docNonce ??= crypto.randomUUID();
  if (tabChannel !== null && tabChannelSession === sessionId) return tabChannel;
  tabChannel?.close();
  const channel = new BroadcastChannel(TAB_CHANNEL_PREFIX + sessionId);
  channel.onmessage = (ev: MessageEvent) => {
    const msg: unknown = ev.data;
    // Incumbent duty: only a clone can send a ``hello`` naming this document's tab id
    // with a nonce that is not ours (a genuinely new tab mints a fresh tab id; a
    // reload's sender no longer exists). Answer so the clone can disambiguate itself.
    if (
      isTabMessage(msg) &&
      msg.type === "hello" &&
      msg.tabId === tabId() &&
      msg.nonce !== docNonce
    ) {
      channel.postMessage({ type: "alive", tabId: msg.tabId, nonce: docNonce });
    }
  };
  tabChannel = channel;
  tabChannelSession = sessionId;
  return channel;
}

/** Mint a fresh tab id — used when this document turns out to be a duplicate clone. */
export function resetTabId(uuid: () => string = crypto.randomUUID.bind(crypto)): string {
  const id = uuid();
  storage("session")?.setItem(TAB_KEY, id);
  return id;
}

/**
 * Ask whether another live document already owns ``myTabId``. Resolves ``true`` only if
 * an incumbent answers our ``hello`` with an ``alive`` for our tab id carrying a
 * different document nonce — meaning this tab is a duplicate clone that must not reuse
 * the identity. Without BroadcastChannel the handshake is skipped (resolves ``false``;
 * the localStorage claim check remains the fallback). The channel stays open either
 * way so this document answers any later clone's hello as the incumbent.
 */
export function detectClone(
  sessionId: string,
  myTabId: string,
  timeoutMs = CLONE_DETECT_MS,
): Promise<boolean> {
  const channel = ensureTabChannel(sessionId);
  if (channel === null) return Promise.resolve(false);
  const myNonce = docNonce as string;
  return new Promise<boolean>((resolve) => {
    let settled = false;
    const finish = (clone: boolean) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      channel.removeEventListener("message", onMessage);
      resolve(clone);
    };
    const onMessage = (ev: MessageEvent) => {
      const msg: unknown = ev.data;
      if (
        isTabMessage(msg) &&
        msg.type === "alive" &&
        msg.tabId === myTabId &&
        msg.nonce !== myNonce
      ) {
        finish(true);
      }
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    channel.addEventListener("message", onMessage);
    channel.postMessage({ type: "hello", tabId: myTabId, nonce: myNonce });
  });
}

/** Close the handshake channel (controller dispose / document teardown). */
export function closeTabChannel(): void {
  tabChannel?.close();
  tabChannel = null;
  tabChannelSession = null;
}
