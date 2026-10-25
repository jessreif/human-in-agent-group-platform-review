/**
 * Participant/teammate display names (UX round 2, item 10): wherever the UI names the
 * participant it uses the server-stored initials ("You" before they're entered), and
 * teammate seats render through ``displaySeat`` — when the participant's initials
 * collide with a seat label (JT/PM), that seat displays as "AR" so the participant
 * stays distinguishable, matching the design demo's teammateName rule.
 */

export function youLabel(initials: string | null): string {
  return initials ?? "You";
}

export function displaySeat(seat: string, initials: string | null): string {
  return initials !== null && initials === seat ? "AR" : seat;
}
