/**
 * Case-file telemetry tests (2026-10-12 plan): per-record view/dwell via
 * IntersectionObserver, aggregated scroll summaries, reorder methods, final
 * order snapshot, and share_attempt channels. jsdom lacks IntersectionObserver,
 * so a controllable stub is installed per test.
 */
import { cleanup, fireEvent, render, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/api/client";
import type { FileRecord } from "../src/api/types";
import { CaseFile } from "../src/components/CaseFile";
import type { SessionStore, StoreState } from "../src/state/store";
import { TelemetryQueue } from "../src/telemetry";

class MockIO {
  static instances: MockIO[] = [];

  readonly cb: IntersectionObserverCallback;

  /** Options passed to the constructor (`threshold`, `root`, `rootMargin`). */
  readonly options: IntersectionObserverInit;

  readonly observed: Element[] = [];

  constructor(cb: IntersectionObserverCallback, options?: IntersectionObserverInit) {
    this.cb = cb;
    this.options = options ?? {};
    MockIO.instances.push(this);
  }

  observe(el: Element) {
    this.observed.push(el);
  }

  unobserve() {}

  disconnect() {}

  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }

  /**
   * `ratio` defaults to 1 (fully visible). Boundary cases pass an explicit ratio;
   * do not treat the default as proof of the 0.6 threshold.
   */
  trigger(el: Element, isIntersecting: boolean, ratio = 1) {
    this.cb(
      [
        { isIntersecting, target: el, intersectionRatio: ratio } as IntersectionObserverEntry,
      ],
      this as unknown as IntersectionObserver,
    );
  }
}

const RECORDS: FileRecord[] = [
  {
    record_id: "r1",
    crate: 1,
    stamp: "Today, 8:55 AM",
    body: "record one",
    event_type: "arrival",
    source: "initial",
  },
  {
    record_id: "r2",
    crate: 3,
    stamp: "Yesterday, 6:10 PM",
    body: "record two",
    event_type: "transit",
    source: "initial",
  },
  {
    record_id: "r3",
    crate: 3,
    stamp: "3 days ago, 9:30 AM",
    body: "record three",
    event_type: "still_at",
    source: "added_from_team",
    shared_by: "JT",
  },
];

function makeState(overrides: Record<string, unknown> = {}): StoreState {
  return {
    file: RECORDS,
    fileOrder: RECORDS.map((r) => r.record_id),
    channels: ["file_work", "sharing", "structured_seeking", "social_reply"],
    taskClosed: false,
    sharedRecordIds: [],
    initials: null,
    ...overrides,
  } as unknown as StoreState;
}

function makeStore(): SessionStore {
  return {
    shareRecord: vi.fn().mockResolvedValue({ accepted: true }),
    reorderFile: vi.fn(),
  } as unknown as SessionStore;
}

function li(container: HTMLElement, recordId: string): Element {
  const el = container.querySelector(`[data-record-id="${recordId}"]`);
  if (!el) throw new Error(`no card for ${recordId}`);
  return el;
}

type FileListGeometry = {
  scrollHeight: number;
  clientHeight: number;
  listRect?: { top: number; bottom: number };
  childTops?: Map<string, number>;
};

/** `.files-list` metrics only, installed before render. `afterEach` restores descriptors.
 *  `scrollDepth` returns 1 when `scrollHeight <= 0`, so a stub applied after mount is too late. */
let restoreFileListGeometry: () => void = () => {};

function rectBox(top: number, bottom: number): DOMRect {
  const height = bottom - top;
  return {
    x: 0,
    y: top,
    left: 0,
    right: 0,
    top,
    bottom,
    width: 0,
    height,
    toJSON() {
      return { x: 0, y: top, left: 0, right: 0, top, bottom, width: 0, height };
    },
  };
}

function isFilesList(node: unknown): node is Element {
  return node instanceof Element && node.classList.contains("files-list");
}

function installFileListGeometry(spec: FileListGeometry): void {
  restoreFileListGeometry();
  const proto = Element.prototype;
  const scrollHeight = Object.getOwnPropertyDescriptor(proto, "scrollHeight");
  const clientHeight = Object.getOwnPropertyDescriptor(proto, "clientHeight");
  const getBoundingClientRect = Object.getOwnPropertyDescriptor(proto, "getBoundingClientRect");
  if (
    scrollHeight === undefined ||
    scrollHeight.get === undefined ||
    clientHeight === undefined ||
    clientHeight.get === undefined ||
    getBoundingClientRect === undefined ||
    typeof getBoundingClientRect.value !== "function"
  ) {
    throw new Error("jsdom geometry descriptors are missing");
  }
  const readScroll = scrollHeight.get;
  const readClient = clientHeight.get;
  const readRect = getBoundingClientRect.value as (this: Element) => DOMRect;
  restoreFileListGeometry = () => {
    Object.defineProperty(proto, "scrollHeight", scrollHeight);
    Object.defineProperty(proto, "clientHeight", clientHeight);
    Object.defineProperty(proto, "getBoundingClientRect", getBoundingClientRect);
    restoreFileListGeometry = () => {};
  };
  try {
    Object.defineProperty(proto, "scrollHeight", {
      configurable: true,
      enumerable: scrollHeight.enumerable ?? false,
      get(this: Element) {
        if (isFilesList(this)) return spec.scrollHeight;
        return readScroll.call(this);
      },
    });
    Object.defineProperty(proto, "clientHeight", {
      configurable: true,
      enumerable: clientHeight.enumerable ?? false,
      get(this: Element) {
        if (isFilesList(this)) return spec.clientHeight;
        return readClient.call(this);
      },
    });
    Object.defineProperty(proto, "getBoundingClientRect", {
      configurable: true,
      enumerable: getBoundingClientRect.enumerable ?? false,
      writable: true,
      value(this: Element) {
        if (isFilesList(this) && spec.listRect) {
          return rectBox(spec.listRect.top, spec.listRect.bottom);
        }
        const id = this.getAttribute("data-record-id");
        const top = id !== null ? spec.childTops?.get(id) : undefined;
        if (top !== undefined) return rectBox(top, top + 60);
        return readRect.call(this);
      },
    });
  } catch (err) {
    restoreFileListGeometry();
    throw err;
  }
}

describe("CaseFile telemetry", () => {
  let emit: ReturnType<typeof vi.fn>;
  let telemetry: TelemetryQueue;
  let store: SessionStore;

  let depart: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    MockIO.instances = [];
    emit = vi.fn();
    depart = vi.fn();
    telemetry = { emit, depart } as unknown as TelemetryQueue;
    store = makeStore();
    vi.stubGlobal("IntersectionObserver", MockIO);
  });

  afterEach(() => {
    restoreFileListGeometry();
    Reflect.deleteProperty(document, "hidden");
    cleanup();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  function emitCalls(kind: string): unknown[][] {
    return emit.mock.calls.filter((c) => c[0] === kind);
  }

  it("emits file_record_view after 300ms qualified intersection, dwell on exit", () => {
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    const card = li(container, "r1");

    act(() => {
      io.trigger(card, true);
      vi.advanceTimersByTime(350);
    });
    expect(emitCalls("file_record_view")).toEqual([
      [
        "file_record_view",
        { record_id: "r1", crate: 1, source: "initial" },
      ],
    ]);

    act(() => {
      vi.advanceTimersByTime(1200);
      io.trigger(card, false);
    });
    const dwell = emitCalls("file_record_dwell");
    expect(dwell).toHaveLength(1);
    expect((dwell[0] as unknown[])[1]).toMatchObject({
      record_id: "r1",
      dwell_ms: expect.any(Number),
    });
    expect(
      ((dwell[0] as unknown[])[1] as { dwell_ms: number }).dwell_ms,
    ).toBeGreaterThan(1000);
  });

  it("does not emit a view for a quick pass-through", () => {
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    const card = li(container, "r2");
    const armedAt = vi.getTimerCount();
    act(() => {
      io.trigger(card, true, 0.6);
    });
    // Ratio ≥ 0.6 arms the qualification timer; a below-threshold entry must not.
    expect(vi.getTimerCount()).toBe(armedAt + 1);
    act(() => {
      vi.advanceTimersByTime(100);
      io.trigger(card, false, 0);
    });
    expect(vi.getTimerCount()).toBe(armedAt);
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(emitCalls("file_record_view")).toHaveLength(0);
    expect(emitCalls("file_record_dwell")).toHaveLength(0);
  });

  it("dedupes repeat views of the same record", () => {
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    const card = li(container, "r1");
    act(() => {
      io.trigger(card, true, 1);
      vi.advanceTimersByTime(300);
      io.trigger(card, false, 0);
      io.trigger(card, true, 1);
      vi.advanceTimersByTime(300);
      io.trigger(card, false, 0);
    });
    expect(emitCalls("file_record_view")).toHaveLength(1);
    // Exit and re-entry: one deduped view, two closed dwell segments.
    expect(emitCalls("file_record_dwell")).toHaveLength(2);
  });

  it("emits an aggregated file_scroll_summary on the 5s cadence", () => {
    installFileListGeometry({ scrollHeight: 4800, clientHeight: 400 });
    const { container, rerender } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const list = container.querySelector(".files-list") as HTMLElement;
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    // Unchanged scroll state does not emit on the cadence.
    expect(emitCalls("file_scroll_summary")).toHaveLength(0);
    act(() => {
      list.scrollTop = 2000;
      fireEvent.scroll(list);
      list.scrollTop = 500;
      fireEvent.scroll(list);
      vi.advanceTimersByTime(5000);
    });
    const summaries = emitCalls("file_scroll_summary");
    expect(summaries).toHaveLength(1);
    const payload = (summaries[0] as unknown[])[1] as Record<string, number>;
    expect(payload.depth_frac).toBeCloseTo(0.188, 2);
    expect(payload.max_depth_frac).toBeCloseTo(0.5, 2);
    expect(payload.direction_reversals).toBe(1);
    expect(payload.distance_px).toBeGreaterThanOrEqual(3000);
    expect(payload.n_items).toBe(3);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(emitCalls("file_scroll_summary")).toHaveLength(1);
    act(() => {
      rerender(
        <CaseFile
          state={makeState({ taskClosed: true })}
          store={store}
          telemetry={telemetry}
          disabled={false}
        />,
      );
    });
    const finals = emitCalls("file_scroll_summary");
    expect(finals).toHaveLength(2);
    const finalCall = finals[1];
    if (!finalCall) throw new Error("missing final scroll summary");
    const finalPayload = finalCall[1] as Record<string, number>;
    expect(finalPayload.depth_frac).toBeCloseTo(0.188, 2);
    expect(finalPayload.max_depth_frac).toBeCloseTo(0.5, 2);
    expect(finalPayload.direction_reversals).toBe(1);
    expect(finalPayload.distance_px).toBeGreaterThanOrEqual(3000);
    expect(finalPayload.n_items).toBe(3);
  });

  it("emits file_reorder with method=arrow on keyboard moves", () => {
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const grip = li(container, "r1").querySelector("button") as HTMLElement;
    act(() => {
      grip.focus();
      fireEvent.keyDown(grip, { key: "ArrowDown" });
    });
    expect(store.reorderFile).toHaveBeenCalledWith(["r2", "r1", "r3"]);
    expect(emitCalls("file_reorder")).toEqual([
      [
        "file_reorder",
        { record_id: "r1", from_index: 0, to_index: 1, method: "arrow" },
      ],
    ]);
  });

  it("emits file_reorder with method=grip on click pick-up + drop", () => {
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    act(() => {
      fireEvent.click(li(container, "r1").querySelector("button") as HTMLElement);
    });
    act(() => {
      fireEvent.click(li(container, "r3").querySelector("button") as HTMLElement);
    });
    expect(store.reorderFile).toHaveBeenCalledWith(["r2", "r3", "r1"]);
    expect(emitCalls("file_reorder")).toEqual([
      [
        "file_reorder",
        { record_id: "r1", from_index: 0, to_index: 2, method: "grip" },
      ],
    ]);
  });

  it("emits share_attempt via button on success and rejection", async () => {
    const { container, rerender } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const shareBtn = li(container, "r1").querySelector(".file-actions button") as HTMLElement;
    await act(async () => {
      fireEvent.click(shareBtn);
    });
    expect(emitCalls("share_attempt")).toEqual([
      ["share_attempt", { record_id: "r1", via: "button", result: "sent" }],
    ]);

    const rejecting = {
      shareRecord: vi
        .fn()
        .mockRejectedValue(new ApiError(422, "no", "record_already_shared")),
      reorderFile: vi.fn(),
    } as unknown as SessionStore;
    rerender(
      <CaseFile state={makeState()} store={rejecting} telemetry={telemetry} disabled={false} />,
    );
    await act(async () => {
      fireEvent.click(li(container, "r2").querySelector(".file-actions button") as HTMLElement);
    });
    const attempts = emitCalls("share_attempt");
    expect((attempts[1] as unknown[])[1]).toEqual({
      record_id: "r2",
      via: "button",
      result: "rejected:record_already_shared",
    });
  });

  it("emits final dwell, scroll summary and file_order_final on task close", () => {
    const state = makeState();
    const { container, rerender } = render(
      <CaseFile state={state} store={store} telemetry={telemetry} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    act(() => {
      io.trigger(li(container, "r1"), true);
      vi.advanceTimersByTime(350);
    });
    emit.mockClear();
    act(() => {
      rerender(
        <CaseFile
          state={makeState({ taskClosed: true })}
          store={store}
          telemetry={telemetry}
          disabled={false}
        />,
      );
    });
    const orders = emitCalls("file_order_final");
    expect(orders).toHaveLength(1);
    expect((orders[0] as unknown[])[1]).toEqual({
      order: ["r1", "r2", "r3"],
      n_items: 3,
    });
    const finalDwells = emitCalls("file_record_dwell").filter(
      (c) => ((c[1] as Record<string, unknown>).final as boolean) === true,
    );
    expect(finalDwells).toHaveLength(1);
    expect(emitCalls("file_scroll_summary").length).toBeGreaterThan(0);
  });

  it("emits file_order_final on unmount when the task never closed", () => {
    const { unmount } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    act(() => unmount());
    expect(emitCalls("file_order_final")).toHaveLength(1);
  });

  it("does not double-emit the final order on close-then-unmount", () => {
    const { unmount, rerender } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    act(() => {
      rerender(
        <CaseFile
          state={makeState({ taskClosed: true })}
          store={store}
          telemetry={telemetry}
          disabled={false}
        />,
      );
      unmount();
    });
    expect(emitCalls("file_order_final")).toHaveLength(1);
  });

  it("emits a file_meta marker once on mount", () => {
    render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    expect(emitCalls("file_meta")).toEqual([
      ["file_meta", { io_supported: true, n_items: 3 }],
    ]);
  });

  it("treats a downward crossing below 60% as an exit while still intersecting", () => {
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    const card = li(container, "r1");
    act(() => {
      io.trigger(card, true, 0.9);
      vi.advanceTimersByTime(350);
    });
    expect(emitCalls("file_record_view")).toHaveLength(1);

    act(() => {
      vi.advanceTimersByTime(800);
      // Still intersecting but only 59% on screen — below 0.6, so the segment ends.
      io.trigger(card, true, 0.59);
    });
    expect(emitCalls("file_record_dwell")).toHaveLength(1);
  });

  it("cancels a pending qualification on a downward ratio crossing", () => {
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    const card = li(container, "r1");
    const armedAt = vi.getTimerCount();
    act(() => {
      io.trigger(card, true, 1);
    });
    expect(vi.getTimerCount()).toBe(armedAt + 1);
    act(() => {
      vi.advanceTimersByTime(100);
      // Still intersecting, but 0.59 is below the 0.6 line — the pending timer cancels.
      io.trigger(card, true, 0.59);
    });
    expect(vi.getTimerCount()).toBe(armedAt);
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(emitCalls("file_record_view")).toHaveLength(0);
    expect(emitCalls("file_record_dwell")).toHaveLength(0);
  });

  it("suspends exposure while the tab is hidden and re-qualifies on return", () => {
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    const card = li(container, "r1");
    // jsdom's document.hidden is a prototype accessor — shadow it on the
    // instance so the component reads the simulated visibility state.
    let hidden = false;
    Object.defineProperty(document, "hidden", {
      get: () => hidden,
      configurable: true,
    });
    try {
      act(() => {
        io.trigger(card, true);
        vi.advanceTimersByTime(350);
      });
      expect(emitCalls("file_record_view")).toHaveLength(1);

      act(() => {
        vi.advanceTimersByTime(500);
        hidden = true;
        document.dispatchEvent(new Event("visibilitychange"));
      });
      // The open segment closed — unobserved time never counts as dwell.
      expect(emitCalls("file_record_dwell")).toHaveLength(1);

      act(() => {
        vi.advanceTimersByTime(2000);
        hidden = false;
        document.dispatchEvent(new Event("visibilitychange"));
        vi.advanceTimersByTime(350);
      });
      // No second view (deduped), but a fresh segment opened after a new
      // 300 ms qualification — the second dwell lands on the next exit.
      expect(emitCalls("file_record_view")).toHaveLength(1);
      act(() => {
        io.trigger(card, false);
      });
      expect(emitCalls("file_record_dwell")).toHaveLength(2);
    } finally {
      Reflect.deleteProperty(document, "hidden");
    }
  });

  it("suspends exposure while an occluding check-in overlay covers the pane", () => {
    const { container, rerender } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    const card = li(container, "r1");
    act(() => {
      io.trigger(card, true);
      vi.advanceTimersByTime(350);
      vi.advanceTimersByTime(400);
    });
    act(() => {
      rerender(
        <CaseFile
          state={makeState()}
          store={store}
          telemetry={telemetry}
          disabled={false}
          occluded={true}
        />,
      );
    });
    // Occlusion closed the open segment.
    expect(emitCalls("file_record_dwell")).toHaveLength(1);

    // Observer entries arriving while occluded are ignored entirely.
    act(() => {
      io.trigger(card, true);
      vi.advanceTimersByTime(500);
    });
    expect(emitCalls("file_record_view")).toHaveLength(1);
    expect(emitCalls("file_record_dwell")).toHaveLength(1);

    act(() => {
      rerender(
        <CaseFile
          state={makeState()}
          store={store}
          telemetry={telemetry}
          disabled={false}
          occluded={false}
        />,
      );
    });
    // Effects flush at act end — resume armed a fresh pending qualification in
    // the rerender above; the 300 ms must advance in a following act.
    act(() => {
      vi.advanceTimersByTime(350);
      io.trigger(card, false);
    });
    // Resume re-armed a fresh qualification; the second segment closed on exit.
    expect(emitCalls("file_record_dwell")).toHaveLength(2);
  });

  it("ignores exposure and scroll activity after finalization", () => {
    const state = makeState();
    const { container, rerender } = render(
      <CaseFile state={state} store={store} telemetry={telemetry} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    act(() => {
      rerender(
        <CaseFile
          state={makeState({ taskClosed: true })}
          store={store}
          telemetry={telemetry}
          disabled={false}
        />,
      );
    });
    emit.mockClear();
    act(() => {
      io.trigger(li(container, "r1"), true);
      vi.advanceTimersByTime(500);
      const list = container.querySelector(".files-list") as HTMLElement;
      fireEvent.scroll(list);
      window.dispatchEvent(new Event("scroll"));
      vi.advanceTimersByTime(5000);
    });
    expect(emitCalls("file_record_view")).toHaveLength(0);
    expect(emitCalls("file_record_dwell")).toHaveLength(0);
    expect(emitCalls("file_scroll_summary")).toHaveLength(0);
  });

  it("rebuilds the observer when a teammate-added record arrives", () => {
    const { container, rerender } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    expect(MockIO.instances).toHaveLength(1);
    expect(MockIO.instances[0]?.observed).toHaveLength(3);
    const first = MockIO.instances[0] as MockIO;
    act(() => {
      first.trigger(li(container, "r1"), true, 1);
      vi.advanceTimersByTime(300);
      first.trigger(li(container, "r1"), false, 0);
    });
    expect(emitCalls("file_record_view")).toEqual([
      ["file_record_view", { record_id: "r1", crate: 1, source: "initial" }],
    ]);

    const added: FileRecord = {
      record_id: "r4",
      crate: 2,
      stamp: "Today, 9:10 AM",
      body: "record four",
      event_type: "arrival",
      source: "added_from_team",
      shared_by: "PM",
    };
    act(() => {
      rerender(
        <CaseFile
          state={makeState({
            file: [...RECORDS, added],
            fileOrder: [...RECORDS.map((r) => r.record_id), "r4"],
          })}
          store={store}
          telemetry={telemetry}
          disabled={false}
        />,
      );
    });
    expect(MockIO.instances).toHaveLength(2);
    const rebuilt = MockIO.instances[1] as MockIO;
    expect(rebuilt.options.threshold).toBe(0.6);
    expect(rebuilt.options.root == null).toBe(true);
    expect(rebuilt.observed).toHaveLength(4);
    expect(rebuilt.observed).toContain(li(container, "r4"));
    expect(rebuilt.observed).toContain(li(container, "r1"));

    // The new card is observed; r1's dedupe survives the rebuild.
    act(() => {
      rebuilt.trigger(li(container, "r1"), true, 1);
      vi.advanceTimersByTime(300);
      rebuilt.trigger(li(container, "r4"), true, 0.6);
      vi.advanceTimersByTime(300);
    });
    expect(emitCalls("file_record_view")).toEqual([
      ["file_record_view", { record_id: "r1", crate: 1, source: "initial" }],
      [
        "file_record_view",
        { record_id: "r4", crate: 2, source: "added_from_team" },
      ],
    ]);
  });

  it("counts sub-deadband movement in scroll distance", () => {
    installFileListGeometry({ scrollHeight: 4800, clientHeight: 400 });
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const list = container.querySelector(".files-list") as HTMLElement;
    act(() => {
      // Three 3 px nudges — under the 8 px reversal deadband individually, but
      // distance must still accumulate every genuine movement (F07).
      list.scrollTop = 3;
      fireEvent.scroll(list);
      list.scrollTop = 6;
      fireEvent.scroll(list);
      list.scrollTop = 9;
      fireEvent.scroll(list);
      vi.advanceTimersByTime(5000);
    });
    const summaries = emitCalls("file_scroll_summary");
    expect(summaries.length).toBeGreaterThan(0);
    const payload = (summaries[0] as unknown[])[1] as Record<string, number>;
    expect(payload.distance_px).toBe(9);
    expect(payload.direction_reversals).toBe(0);
  });

  it("recomputes farthest_index at emit time, not at scroll time", () => {
    const childTops = new Map<string, number>([
      ["r1", 0],
      ["r2", 2000],
      ["r3", 4000],
    ]);
    installFileListGeometry({
      scrollHeight: 4800,
      clientHeight: 400,
      listRect: { top: 0, bottom: 5000 },
      childTops,
    });
    const { container, rerender } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const list = container.querySelector(".files-list") as HTMLElement;
    // At scroll time only the first card is inside the viewport (jsdom
    // innerHeight = 768); by emit time all three tops are within it — the
    // payload must reflect emit-time geometry (F08), not the stale walk.
    act(() => {
      list.scrollTop = 50;
      fireEvent.scroll(list);
      childTops.set("r1", 0);
      childTops.set("r2", 100);
      childTops.set("r3", 200);
      vi.advanceTimersByTime(5000);
    });
    const summaries = emitCalls("file_scroll_summary");
    expect(summaries.length).toBeGreaterThan(0);
    const first = summaries[0];
    if (!first) throw new Error("missing scroll summary");
    const payload = first[1] as Record<string, number>;
    expect(payload.farthest_index).toBe(2);
    act(() => {
      rerender(
        <CaseFile
          state={makeState({ taskClosed: true })}
          store={store}
          telemetry={telemetry}
          disabled={false}
        />,
      );
    });
    const finals = emitCalls("file_scroll_summary");
    const finalCall = finals[finals.length - 1];
    if (!finalCall) throw new Error("missing final scroll summary");
    expect(finals.length).toBe(summaries.length + 1);
    expect((finalCall[1] as Record<string, number>).farthest_index).toBe(2);
  });

  // Drag share_attempt is not implemented on CaseFile; drop-to-share lives on the chat pane.
  it("emits file_reorder with method=drag on drop-on-record", () => {
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const dataTransfer = {
      setData: vi.fn(),
      getData: (t: string) => (t === "text/record-id" ? "r1" : ""),
      effectAllowed: "",
      types: ["text/record-id"],
    };
    act(() => {
      fireEvent.dragStart(li(container, "r1"), { dataTransfer });
    });
    act(() => {
      fireEvent.drop(li(container, "r3"), { dataTransfer });
    });
    expect(store.reorderFile).toHaveBeenCalledWith(["r2", "r3", "r1"]);
    expect(emitCalls("file_reorder")).toEqual([
      [
        "file_reorder",
        { record_id: "r1", from_index: 0, to_index: 2, method: "drag" },
      ],
    ]);
  });

  it("finalizes on pagehide and drains the queue via depart()", () => {
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    act(() => {
      io.trigger(li(container, "r1"), true);
      vi.advanceTimersByTime(350);
      vi.advanceTimersByTime(500);
    });
    emit.mockClear();
    act(() => {
      window.dispatchEvent(new Event("pagehide"));
    });
    expect(emitCalls("file_order_final")).toHaveLength(1);
    const finalDwells = emitCalls("file_record_dwell").filter(
      (c) => ((c[1] as Record<string, unknown>).final as boolean) === true,
    );
    expect(finalDwells).toHaveLength(1);
    expect(depart).toHaveBeenCalled();
  });

  it("creates the observer with threshold 0.6 and no element root", () => {
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    expect(io.options.threshold).toBe(0.6);
    expect(io.options.root == null).toBe(true);
    expect(io.options.root).not.toBe(container.querySelector(".files-list"));
  });

  it("does not arm a view for ratios 0, 0.01, or 0.59", () => {
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    const before = vi.getTimerCount();
    act(() => {
      io.trigger(li(container, "r1"), true, 0);
      io.trigger(li(container, "r2"), true, 0.01);
      io.trigger(li(container, "r3"), true, 0.59);
    });
    expect(vi.getTimerCount()).toBe(before);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(emitCalls("file_record_view")).toHaveLength(0);
    expect(emitCalls("file_record_dwell")).toHaveLength(0);
  });

  it("emits one view at 300ms for ratios 0.6 and 1, and nothing at 299ms", () => {
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    const before = vi.getTimerCount();
    act(() => {
      io.trigger(li(container, "r1"), true, 0.6);
      vi.advanceTimersByTime(299);
    });
    expect(vi.getTimerCount()).toBe(before + 1);
    expect(emitCalls("file_record_view")).toHaveLength(0);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(emitCalls("file_record_view")).toEqual([
      ["file_record_view", { record_id: "r1", crate: 1, source: "initial" }],
    ]);

    act(() => {
      io.trigger(li(container, "r2"), true, 1);
      vi.advanceTimersByTime(299);
    });
    expect(emitCalls("file_record_view")).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(emitCalls("file_record_view")).toEqual([
      ["file_record_view", { record_id: "r1", crate: 1, source: "initial" }],
      ["file_record_view", { record_id: "r2", crate: 3, source: "initial" }],
    ]);
  });

  it("cancels an armed view timer when the document hides and emits no late view", () => {
    let hidden = false;
    Object.defineProperty(document, "hidden", {
      get: () => hidden,
      configurable: true,
    });
    try {
      const { container } = render(
        <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
      );
      const io = MockIO.instances[0] as MockIO;
      const armedAt = vi.getTimerCount();
      act(() => {
        io.trigger(li(container, "r1"), true, 0.6);
        vi.advanceTimersByTime(299);
      });
      expect(vi.getTimerCount()).toBe(armedAt + 1);
      expect(emitCalls("file_record_view")).toHaveLength(0);
      act(() => {
        hidden = true;
        document.dispatchEvent(new Event("visibilitychange"));
      });
      expect(vi.getTimerCount()).toBe(armedAt);
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(emitCalls("file_record_view")).toHaveLength(0);
      expect(emitCalls("file_record_dwell")).toHaveLength(0);
    } finally {
      Reflect.deleteProperty(document, "hidden");
    }
  });

  it("cancels an armed view timer when occluded and emits no late view", () => {
    const { container, rerender } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    const armedAt = vi.getTimerCount();
    act(() => {
      io.trigger(li(container, "r1"), true, 1);
      vi.advanceTimersByTime(299);
    });
    expect(vi.getTimerCount()).toBe(armedAt + 1);
    expect(emitCalls("file_record_view")).toHaveLength(0);
    act(() => {
      rerender(
        <CaseFile
          state={makeState()}
          store={store}
          telemetry={telemetry}
          disabled={false}
          occluded={true}
        />,
      );
    });
    expect(vi.getTimerCount()).toBe(armedAt);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(emitCalls("file_record_view")).toHaveLength(0);
    expect(emitCalls("file_record_dwell")).toHaveLength(0);
  });

  it("cancels an armed view timer when the task closes and emits no late view", () => {
    const { container, rerender } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    const armedAt = vi.getTimerCount();
    act(() => {
      io.trigger(li(container, "r1"), true, 0.6);
      vi.advanceTimersByTime(200);
    });
    expect(vi.getTimerCount()).toBe(armedAt + 1);
    expect(emitCalls("file_record_view")).toHaveLength(0);
    act(() => {
      rerender(
        <CaseFile
          state={makeState({ taskClosed: true })}
          store={store}
          telemetry={telemetry}
          disabled={false}
        />,
      );
    });
    expect(vi.getTimerCount()).toBe(armedAt);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(emitCalls("file_record_view")).toHaveLength(0);
    expect(emitCalls("file_record_dwell")).toHaveLength(0);
  });

  it("seeds scroll depth from geometry installed before mount", () => {
    installFileListGeometry({ scrollHeight: 4800, clientHeight: 400 });
    const { container, rerender } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const list = container.querySelector(".files-list") as HTMLElement;
    // The mount effect must already see this geometry (scrollHeight <= 0 would read as depth 1).
    expect(list.scrollHeight).toBe(4800);
    expect(list.clientHeight).toBe(400);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(emitCalls("file_scroll_summary")).toHaveLength(0);
    act(() => {
      rerender(
        <CaseFile
          state={makeState({ taskClosed: true })}
          store={store}
          telemetry={telemetry}
          disabled={false}
        />,
      );
    });
    const summaries = emitCalls("file_scroll_summary");
    expect(summaries).toHaveLength(1);
    const call = summaries[0];
    if (!call) throw new Error("missing final scroll summary");
    const payload = call[1] as Record<string, number>;
    // (scrollTop 0 + clientHeight 400) / scrollHeight 4800, not the zero-height sentinel 1.
    expect(payload.depth_frac).toBeCloseTo(0.083, 3);
    expect(payload.max_depth_frac).toBeCloseTo(0.083, 3);
    expect(payload.direction_reversals).toBe(0);
    expect(payload.distance_px).toBe(0);
    expect(payload.n_items).toBe(3);
  });

  it("does not count a reversal inside the 8px deadband", () => {
    installFileListGeometry({ scrollHeight: 4800, clientHeight: 400 });
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    const list = container.querySelector(".files-list") as HTMLElement;
    act(() => {
      list.scrollTop = 4;
      fireEvent.scroll(list);
      list.scrollTop = 0;
      fireEvent.scroll(list);
      vi.advanceTimersByTime(5000);
    });
    const summaries = emitCalls("file_scroll_summary");
    expect(summaries).toHaveLength(1);
    const call = summaries[0];
    if (!call) throw new Error("missing scroll summary");
    const payload = call[1] as Record<string, number>;
    expect(payload.distance_px).toBe(8);
    expect(payload.direction_reversals).toBe(0);
    expect(payload.max_depth_frac).toBeCloseTo(404 / 4800, 3);
    expect(payload.depth_frac).toBeCloseTo(400 / 4800, 3);
  });

  it("posts file_record_view through a real TelemetryQueue", async () => {
    const fetchImpl = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    }));
    const queue = new TelemetryQueue({ fetchImpl: fetchImpl as unknown as typeof fetch });
    const { container } = render(
      <CaseFile state={makeState()} store={store} telemetry={queue} disabled={false} />,
    );
    const io = MockIO.instances[0] as MockIO;
    act(() => {
      io.trigger(li(container, "r1"), true, 1);
      vi.advanceTimersByTime(300);
    });
    await act(async () => {
      await queue.flush();
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const call = fetchImpl.mock.calls[0];
    if (!call) throw new Error("telemetry POST was not sent");
    const [url, init] = call as unknown as [string, RequestInit];
    expect(String(url)).toBe("/investigators/api/telemetry");
    expect(init.method).toBe("POST");
    const body = JSON.parse(String(init.body)) as {
      kind: string;
      data: { events: { kind: string; data?: Record<string, unknown> }[] };
    };
    expect(body.kind).toBe("batch");
    const view = body.data.events.find((event) => event.kind === "file_record_view");
    expect(view?.data).toEqual({ record_id: "r1", crate: 1, source: "initial" });
  });
});
