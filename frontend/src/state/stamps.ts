/** Case-file display order: the participant's reorder surface (INV-0803) applied over
 * the server file; any file records missing from the order append at the end. */
export function orderedFile<T extends { record_id: string }>(
  file: T[],
  order: string[],
): T[] {
  const byId = new Map(file.map((r) => [r.record_id, r]));
  const base = order
    .map((id) => byId.get(id))
    .filter((r): r is T => r !== undefined);
  for (const r of file) if (!base.includes(r)) base.push(r);
  return base;
}
