/**
 * Case file pane (INV-0802/0803): the participant's record list with a crate filter,
 * drag reorder plus click and keyboard equivalents (the grip marks each card as
 * movable), and a per-record Share action (the click/keyboard alternative to
 * drag-to-chat). Records added from the team chat get a distinct accent (UX round 3).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "../api/client";
import type { FileRecord } from "../api/types";
import type { SessionStore, StoreState } from "../state/store";
import type { TelemetryQueue } from "../telemetry";
import { displaySeat } from "../state/names";
import { orderedFile } from "../state/stamps";

function recordLabel(r: FileRecord): string {
  return `Crate ${r.crate} — ${r.stamp} — ${r.body}`;
}

/** Continuous-view qualification: a card counts as "viewed" only after this
 * long continuously inside the viewport — filters scroll-by blur. */
const VIEW_QUALIFY_MS = 300;
/** A view qualifies only at this fraction of the card on screen. The observer
 * threshold schedules the callback; the ratio itself is enforced on every
 * entry (a downward crossing below the threshold closes the segment). */
const VIEW_THRESHOLD = 0.6;
/** Scroll-summary emission cadence — aggregate client-side; raw scroll events
 * would exhaust the bounded telemetry queue (MAX_QUEUED). */
const SCROLL_EMIT_MS = 5000;
/** Deadband for direction-reversal detection (px) — ignores micro-jitter.
 * Distance itself accumulates every genuine movement; only reversals use the
 * deadband. */
const REVERSAL_DEADBAND_PX = 8;

/** Bottom of the visible region for the file list — the list's own clip bottom
 * when it scrolls itself (wide layout), else the viewport bottom (≤900 px
 * stacked layout page-scrolls the whole workbench). */
function visibleBottom(el: HTMLElement): number {
  const listBottom = el.getBoundingClientRect().bottom;
  const viewportBottom =
    typeof window !== "undefined" ? window.innerHeight : listBottom;
  return Math.min(listBottom, viewportBottom);
}

function farthestIndex(el: HTMLElement): number {
  const bottom = visibleBottom(el);
  let idx = 0;
  for (let i = 0; i < el.children.length; i += 1) {
    const li = el.children[i] as HTMLElement;
    if (li.getBoundingClientRect().top < bottom) idx = i;
    else break;
  }
  return idx;
}

/** Scroll position metric covering both layouts: the list's own scroll offset
 * plus page scroll (only one moves in each layout, so their sum tracks
 * movement in either). */
function scrollPos(el: HTMLElement): number {
  const page = typeof window !== "undefined" ? window.scrollY : 0;
  return el.scrollTop + page;
}

/** Fraction of the file list's content currently reachable on screen — the
 * scrollport's position for a scrollable list, else the portion of the
 * (page-scrolling) list inside the viewport. */
function scrollDepth(el: HTMLElement): number {
  if (el.scrollHeight <= 0) return 1;
  if (el.scrollHeight > el.clientHeight + 1) {
    return Math.min(1, (el.scrollTop + el.clientHeight) / el.scrollHeight);
  }
  const rect = el.getBoundingClientRect();
  const viewport = typeof window !== "undefined" ? window.innerHeight : rect.height;
  const height = rect.height || el.scrollHeight;
  const visible = Math.min(rect.bottom, viewport) - Math.max(rect.top, 0);
  return Math.max(0, Math.min(1, visible / height));
}

function round3(v: number): number {
  return Math.round(v * 1000) / 1000;
}

export function CaseFile({
  state,
  store,
  telemetry,
  disabled,
  occluded = false,
}: {
  state: StoreState;
  store: SessionStore;
  telemetry: TelemetryQueue;
  disabled: boolean;
  /** A genuinely occluding overlay (check-in) is covering the pane — distinct
   * from ``disabled`` (unwritable controls while still visible). Exposure
   * suspends while occluded: open dwell segments close and pending view
   * timers cancel until the overlay lifts. */
  occluded?: boolean;
}) {
  const [dragId, setDragId] = useState<string | null>(null);
  // BA-38: click alternative to dragging — a clicked grip "grabs" its card; the next
  // grip click drops it at that position (aria-pressed + live announcements).
  const [grabId, setGrabId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");

  const filtered = orderedFile(state.file, state.fileOrder);
  const orderKey = filtered.map((r) => r.record_id).join(",");
  const canShare =
    !disabled && state.channels.includes("sharing") && !state.taskClosed;
  const canDrag = !disabled && !state.taskClosed;

  // ---- Case-file telemetry (2026-10-12 plan): view/dwell per record, aggregated
  // scroll, reorder method, final order. Ids/indexes/timings only — no content.
  const listRef = useRef<HTMLUListElement>(null);
  const fileById = useMemo(
    () => new Map(state.file.map((r) => [r.record_id, r] as const)),
    [state.file],
  );
  const filteredRef = useRef(filtered);
  filteredRef.current = filtered;
  const viewRef = useRef(
    new Map<
      string,
      {
        segStart: number | null;
        pending: ReturnType<typeof setTimeout> | null;
        /** Last qualified-geometry result (ratio ≥ threshold AND intersecting)
         * — survives observer rebuilds so suspend/resume knows which cards
         * were on screen before the interruption. */
        seen: boolean;
      }
    >(),
  );
  const viewedRef = useRef(new Set<string>());
  const finalRef = useRef(false);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const occludedRef = useRef(occluded);
  occludedRef.current = occluded;
  const scrollAcc = useRef({
    distance: 0,
    maxDepth: 0,
    farthest: 0,
    reversals: 0,
    lastPos: 0,
    lastDir: 0,
    dirDelta: 0,
    dirty: false,
  });

  const emitScrollSummary = useCallback(() => {
    const el = listRef.current;
    const acc = scrollAcc.current;
    // Recompute geometry at emit time (F08): a scroll inside the throttle
    // window right before emission must be reflected, not the stale walk.
    // Guard on scrollHeight: a zero-height reading (pre-layout, transiently
    // hidden pane) would poison the max with a bogus depth of 1.
    if (el && el.scrollHeight > 0) {
      acc.maxDepth = Math.max(acc.maxDepth, scrollDepth(el));
      acc.farthest = Math.max(acc.farthest, farthestIndex(el));
    }
    telemetry.emit("file_scroll_summary", {
      depth_frac: el ? round3(scrollDepth(el)) : 1,
      max_depth_frac: round3(acc.maxDepth),
      farthest_index: acc.farthest,
      n_items: filteredRef.current.length,
      direction_reversals: acc.reversals,
      distance_px: Math.round(acc.distance),
    });
    acc.dirty = false;
  }, [telemetry]);

  // Teardown: open dwell segments close with final:true, a terminal scroll
  // summary and the final file order go out once — on task close, unmount or
  // pagehide, whichever comes first. Observer + timers are stopped so no
  // record event can follow the terminal ones (F04).
  const flushFinal = useCallback(() => {
    if (finalRef.current) return;
    finalRef.current = true;
    observerRef.current?.disconnect();
    observerRef.current = null;
    const now = performance.now();
    for (const [rid, st] of viewRef.current) {
      if (st.pending !== null) clearTimeout(st.pending);
      st.pending = null;
      if (st.segStart !== null) {
        telemetry.emit("file_record_dwell", {
          record_id: rid,
          dwell_ms: Math.round(now - st.segStart),
          final: true,
        });
        st.segStart = null;
      }
    }
    emitScrollSummary();
    telemetry.emit("file_order_final", {
      order: filteredRef.current.map((r) => r.record_id),
      n_items: filteredRef.current.length,
    });
  }, [telemetry, emitScrollSummary]);

  useEffect(() => {
    if (state.taskClosed) flushFinal();
  }, [state.taskClosed, flushFinal]);
  useEffect(() => flushFinal, [flushFinal]);

  // Real browser departure (reload/tab close/navigation): React unmount does
  // not run on that path, so finalize explicitly and push the queued tail
  // through a keepalive fetch (F05). Idempotent — React cleanup may repeat it.
  useEffect(() => {
    const onDepart = () => {
      flushFinal();
      telemetry.depart?.();
    };
    window.addEventListener("pagehide", onDepart);
    return () => window.removeEventListener("pagehide", onDepart);
  }, [flushFinal, telemetry]);

  // Suspension: a hidden tab or an occluding overlay means no card is actually
  // on screen — pending qualifications cancel, open dwell segments close
  // (emitted normally, not final). Resume re-qualifies cards whose last known
  // geometry was visible (a fresh 300 ms — honest re-entry, not carry-over).
  const suspendExposure = useCallback(() => {
    const now = performance.now();
    for (const [rid, st] of viewRef.current) {
      if (st.pending !== null) {
        clearTimeout(st.pending);
        st.pending = null;
      }
      if (st.segStart !== null) {
        telemetry.emit("file_record_dwell", {
          record_id: rid,
          dwell_ms: Math.round(now - st.segStart),
        });
        st.segStart = null;
      }
    }
  }, [telemetry]);

  const armPending = useCallback(
    (rid: string, st: { segStart: number | null; pending: ReturnType<typeof setTimeout> | null; seen: boolean }) => {
      if (st.seen && st.segStart === null && st.pending === null) {
        st.pending = setTimeout(() => {
          st.pending = null;
          // Re-check live state at fire time: hidden/occluded/finalized or a
          // downward crossing since arming must not qualify (F01/F02/F04).
          if (finalRef.current || !st.seen) return;
          if (
            occludedRef.current ||
            (typeof document !== "undefined" && document.hidden)
          ) {
            return;
          }
          if (!viewedRef.current.has(rid)) {
            viewedRef.current.add(rid);
            const rec = fileById.get(rid);
            telemetry.emit("file_record_view", {
              record_id: rid,
              crate: rec?.crate,
              source: rec?.source,
            });
          }
          st.segStart = performance.now();
        }, VIEW_QUALIFY_MS);
      }
    },
    [fileById, telemetry],
  );

  const resumeExposure = useCallback(() => {
    if (finalRef.current) return;
    for (const [rid, st] of viewRef.current) armPending(rid, st);
  }, [armPending]);

  // Occlusion (check-in overlay) and tab visibility both suspend exposure.
  useEffect(() => {
    if (finalRef.current) return;
    if (occluded) suspendExposure();
    else resumeExposure();
    // Intentionally keyed on `occluded` only: suspend/resume are stable refs-backed
    // callbacks, and re-running on their identity would re-arm exposure timers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [occluded]);
  useEffect(() => {
    const onVisibility = () => {
      if (finalRef.current) return;
      if (document.hidden || occludedRef.current) suspendExposure();
      else resumeExposure();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [suspendExposure, resumeExposure]);

  // Per-record viewport exposure. Root is the browser viewport — the observer
  // already applies the ancestor scrollport's clipping, so the ratio is the
  // fraction of the card actually on screen in BOTH layouts (wide scrollport
  // and the ≤900 px page-scrolling stack). Rebuilt when the visible order
  // changes so teammate-added cards get observed too; view/dwell state
  // persists across rebuilds via viewRef.
  useEffect(() => {
    const el = listRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (finalRef.current) return;
        if (typeof document !== "undefined" && document.hidden) return;
        if (occludedRef.current) return;
        const now = performance.now();
        for (const e of entries) {
          const rid = (e.target as HTMLElement).dataset.recordId;
          if (!rid) continue;
          let st = viewRef.current.get(rid);
          if (!st) {
            st = { segStart: null, pending: null, seen: false };
            viewRef.current.set(rid, st);
          }
          // Qualified geometry = intersecting AND ≥60% of the card on screen.
          // A downward crossing below the threshold is an exit, even while the
          // card still partially intersects (F01).
          const qualified = e.isIntersecting && e.intersectionRatio >= VIEW_THRESHOLD;
          st.seen = qualified;
          if (qualified) {
            armPending(rid, st);
          } else {
            if (st.pending !== null) {
              clearTimeout(st.pending);
              st.pending = null;
            }
            if (st.segStart !== null) {
              telemetry.emit("file_record_dwell", {
                record_id: rid,
                dwell_ms: Math.round(now - st.segStart),
              });
              st.segStart = null;
            }
          }
        }
      },
      { threshold: VIEW_THRESHOLD },
    );
    observerRef.current = observer;
    for (const li of Array.from(el.children)) observer.observe(li);
    return () => {
      observer.disconnect();
      if (observerRef.current === observer) observerRef.current = null;
    };
    // orderKey captures membership/order changes without a per-render rebuild.
  }, [orderKey, fileById, telemetry, armPending]);

  // Instrumentation marker (once per mount): proves the collector ran so the
  // measure layer can tell "instrumented, zero views" from "telemetry absent".
  useEffect(() => {
    telemetry.emit("file_meta", {
      io_supported: typeof IntersectionObserver !== "undefined",
      n_items: filteredRef.current.length,
    });
  }, [telemetry]);

  // Seed scroll accumulators from the mounted list (re-seed when it grows).
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const acc = scrollAcc.current;
    acc.lastPos = scrollPos(el);
    if (el.scrollHeight > 0) {
      acc.maxDepth = Math.max(acc.maxDepth, scrollDepth(el));
      acc.farthest = Math.max(acc.farthest, farthestIndex(el));
    }
  }, [orderKey]);

  // Emit accumulated scroll state on a fixed cadence, only when changed.
  useEffect(() => {
    const t = setInterval(() => {
      if (scrollAcc.current.dirty && !finalRef.current) emitScrollSummary();
    }, SCROLL_EMIT_MS);
    return () => clearInterval(t);
  }, [emitScrollSummary]);

  function noteScroll() {
    const el = listRef.current;
    if (!el || finalRef.current) return;
    const acc = scrollAcc.current;
    const pos = scrollPos(el);
    const delta = pos - acc.lastPos;
    acc.lastPos = pos;
    const ad = Math.abs(delta);
    // Distance counts every genuine movement (F07); the deadband applies only
    // to direction-reversal detection via a separate accumulator.
    acc.distance += ad;
    acc.dirDelta += delta;
    if (Math.abs(acc.dirDelta) >= REVERSAL_DEADBAND_PX) {
      const dir = acc.dirDelta > 0 ? 1 : -1;
      if (acc.lastDir !== 0 && dir !== acc.lastDir) acc.reversals += 1;
      acc.lastDir = dir;
      acc.dirDelta = 0;
    }
    if (el.scrollHeight > 0) acc.maxDepth = Math.max(acc.maxDepth, scrollDepth(el));
    acc.dirty = true;
  }

  // The ≤900 px layout page-scrolls the whole workbench — the list itself never
  // scrolls — so window scroll/resize move the same accumulators (F03).
  useEffect(() => {
    window.addEventListener("scroll", noteScroll, { passive: true });
    window.addEventListener("resize", noteScroll);
    return () => {
      window.removeEventListener("scroll", noteScroll);
      window.removeEventListener("resize", noteScroll);
    };
  }, []);

  function onDragStart(e: React.DragEvent, recordId: string) {
    setDragId(recordId);
    e.dataTransfer.setData("text/record-id", recordId);
    e.dataTransfer.effectAllowed = "copyMove";
  }

  function onDropOnRecord(e: React.DragEvent, targetId: string) {
    e.preventDefault();
    if (!dragId || dragId === targetId) return;
    const order = filtered.map((r) => r.record_id);
    const from = order.indexOf(dragId);
    const to = order.indexOf(targetId);
    if (from < 0 || to < 0) return;
    order.splice(to, 0, order.splice(from, 1)[0] as string);
    store.reorderFile(order);
    telemetry.emit("file_reorder", {
      record_id: dragId,
      from_index: from,
      to_index: to,
      method: "drag",
    });
    setDragId(null);
  }

  function label(r: FileRecord): string {
    return `Crate ${r.crate} entry, ${r.stamp}`;
  }

  function moveTo(fromId: string, targetId: string) {
    const order = filtered.map((x) => x.record_id);
    const from = order.indexOf(fromId);
    const to = order.indexOf(targetId);
    if (from < 0 || to < 0) return;
    order.splice(to, 0, order.splice(from, 1)[0] as string);
    store.reorderFile(order);
    telemetry.emit("file_reorder", {
      record_id: fromId,
      from_index: from,
      to_index: to,
      method: "grip",
    });
  }

  function onGripClick(r: FileRecord) {
    if (!canDrag) return;
    if (grabId === null) {
      setGrabId(r.record_id);
      setAnnounce(
        `Grabbed ${label(r)}. Click another entry's grip to move it there, or press Escape to let go.`,
      );
      return;
    }
    if (grabId === r.record_id) {
      setGrabId(null);
      setAnnounce(`Let go of ${label(r)} without moving it.`);
      return;
    }
    const grabbed = filtered.find((x) => x.record_id === grabId);
    moveTo(grabId, r.record_id);
    const to = filtered.map((x) => x.record_id).indexOf(r.record_id);
    setAnnounce(
      `Moved ${grabbed ? label(grabbed) : "the grabbed entry"} to position ${to + 1} of ${filtered.length}.`,
    );
    setGrabId(null);
  }

  // BA-38: keyboard equivalent of drag reorder — the grip is a real focusable
  // control; ArrowUp/ArrowDown move the card one position in the visible order
  // and an aria-live region announces the result. Escape releases a grabbed card.
  function moveBy(r: FileRecord, delta: -1 | 1) {
    const order = filtered.map((x) => x.record_id);
    const from = order.indexOf(r.record_id);
    const to = from + delta;
    if (to < 0 || to >= order.length) {
      setAnnounce(`${label(r)} is already ${to < 0 ? "first" : "last"} in the list.`);
      return;
    }
    order.splice(to, 0, order.splice(from, 1)[0] as string);
    store.reorderFile(order);
    telemetry.emit("file_reorder", {
      record_id: r.record_id,
      from_index: from,
      to_index: to,
      method: "arrow",
    });
    setAnnounce(`Moved ${label(r)} to position ${to + 1} of ${order.length}.`);
  }

  function onGripKeyDown(e: React.KeyboardEvent, r: FileRecord) {
    if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
      e.preventDefault();
      moveBy(r, -1);
    } else if (e.key === "ArrowDown" || e.key === "ArrowRight") {
      e.preventDefault();
      moveBy(r, 1);
    } else if (e.key === "Escape" && grabId !== null) {
      e.preventDefault();
      const grabbed = filtered.find((x) => x.record_id === grabId);
      setGrabId(null);
      setAnnounce(
        `Let go of ${grabbed ? label(grabbed) : "the grabbed entry"} without moving it.`,
      );
    }
  }

  async function share(recordId: string) {
    setBusy(recordId);
    try {
      await store.shareRecord(recordId);
      telemetry.emit("share_attempt", {
        record_id: recordId,
        via: "button",
        result: "sent",
      });
    } catch (err) {
      telemetry.emit("share_attempt", {
        record_id: recordId,
        via: "button",
        result: err instanceof ApiError ? `rejected:${err.kind}` : "error",
      });
      /* rejection surfaces via connection/lease state */
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="pane" aria-label="Case file" data-pane="case-file">
      <header className="pane-head">
        <h2>Case file</h2>
        <span className="count">{state.file.length} entries</span>
      </header>
      <ul
        className="files-list"
        aria-label="Case file records"
        ref={listRef}
        onScroll={noteScroll}
      >
        {filtered.map((r) => (
          <li
            key={r.record_id}
            // UX round 3, item 8: teammate-added intel is visually distinct from
            // the participant's own file (participant-safe ``source`` only).
            className={[
              "file",
              r.source !== "initial" ? "from-team" : "",
              dragId === r.record_id ? "dragging" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            data-record-id={r.record_id}
            data-crate={r.crate}
            data-source={r.source}
            draggable={canDrag}
            onDragStart={(e) => onDragStart(e, r.record_id)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => onDropOnRecord(e, r.record_id)}
            aria-label={recordLabel(r)}
          >
            <button
              type="button"
              className={
                grabId === r.record_id ? "drag-grip grabbed" : "drag-grip"
              }
              disabled={!canDrag}
              aria-label={
                grabId === r.record_id
                  ? `${recordLabel(r)} is grabbed — click another entry's grip to move it there, or press Escape to let go`
                  : `Reorder ${recordLabel(r)} — click to pick it up, then click another entry's grip to move it there; arrow keys move it one place`
              }
              aria-pressed={grabId === r.record_id}
              title={
                r.source === "initial"
                  ? "Drag to reorder, click to pick up and drop on another entry, or focus and press arrow keys; drag into the team chat to share"
                  : "Drag to reorder, click to pick up and drop on another entry, or focus and press arrow keys"
              }
              onClick={() => onGripClick(r)}
              onKeyDown={(e) => onGripKeyDown(e, r)}
            >
              ✥
            </button>
            <div className="file-main">
              <div className="file-head">
                <span className="file-stamp">{r.stamp}</span>
                {/* 2026-10-10: visible crate badge restored (reverses UX round 3,
                    item 7) — stamping intel with its crate eases the task.
                    O-4: shared cards name the teammate who shared them. */}
                <span className="file-crate">Crate {r.crate}</span>
                {r.source !== "initial" ? (
                  <span className="file-source">
                    {r.shared_by
                      ? `Shared by ${displaySeat(r.shared_by, state.initials)}`
                      : "From team chat"}
                  </span>
                ) : null}
              </div>
              <div className="file-body">{r.body}</div>
            </div>
            <div className="file-actions">
              {state.sharedRecordIds.includes(r.record_id) ? (
                <span className="shared-tag">Shared in team chat</span>
              ) : r.source === "initial" ? (
                // UX round 5, item 3: no Share action on teammate-added intel —
                // it already reached the file from the chat, so re-sharing it
                // back would only post a confusing duplicate.
                <button
                  type="button"
                  disabled={!canShare || busy === r.record_id}
                  onClick={() => void share(r.record_id)}
                >
                  {busy === r.record_id ? "Sharing…" : "Share"}
                </button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      <p className="sr-only" role="status" aria-live="polite">
        {announce}
      </p>
    </section>
  );
}
