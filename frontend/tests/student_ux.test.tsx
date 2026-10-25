import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { ApiError } from "../src/api/client";
import { AssignmentRule, ConnectingScreen, actionErrorMessage } from "../src/components/StageScreens";
import { SessionStore, type StoreApi, type TaskShape } from "../src/state/store";

afterEach(cleanup);

it.each([
  ["independent", 8, null, "privately assigned four of the eight crates", "Assignments can overlap."],
  ["none", 12, null, "assigned four of the twelve crates", "no overlaps in assignment."],
  ["none", 12, 16, "assigned four of the twelve crates", "no overlaps in assignment."],
  ["some", 8, null, "assigned four of the eight crates", "some overlap in your assignments."],
  ["some", 8, 16, "assigned four of the eight crates", "some overlap in your assignments."],
  ["full", 4, null, "assigned to work on all four crates", ""],
  ["full", 4, 16, "assigned to work on all four crates", ""],
] as const)("repeats the exact %s / %s / %s assignment rule", (overlap, n, budget, assignment, guarantee) => {
  const shape: TaskShape = { nCrates: n, nAssigned: 4, crateOverlap: overlap, intelBudget: budget };
  const { container } = render(<AssignmentRule shape={shape} />);
  const text = container.textContent ?? "";
  expect(text).toContain(assignment);
  expect(text).toContain(guarantee);
  if (overlap !== "independent") expect(text).not.toContain("can overlap");
  cleanup();
  const store = new SessionStore({ api: {} as StoreApi });
  const connecting = render(<ConnectingScreen state={{ ...store.getSnapshot(), taskShape: shape }} store={store} />);
  expect(connecting.container.textContent).toContain(text);
});

it.each([
  ["instrument_incomplete", "answer all questions"],
  ["comprehension_required", "comprehension questions correctly"],
  ["practice_share_required", "share one intelligence record"],
  ["writer_lease_held", "another tab"],
  ["stale_revision", "session is syncing"],
  ["batch_at_capacity", "places are currently reserved"],
  ["batch_closed", "no longer accepting"],
  ["quota_configuration_changed", "temporarily unavailable"],
])("gives actionable feedback for %s rather than blaming answers or connectivity", (kind, expected) => {
  expect(actionErrorMessage(new ApiError(409, "private detail", kind), "fallback")).toContain(expected);
});

it("keeps unexpected save failures distinct from incorrect comprehension answers", () => {
  expect(actionErrorMessage(new ApiError(503, "private detail"), "Couldn't submit. Try again."))
    .toBe("Couldn't submit. Try again.");
});
