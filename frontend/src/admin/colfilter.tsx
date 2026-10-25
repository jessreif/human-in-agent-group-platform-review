/**
 * Column-heading category filters — shared by the sessions table and the
 * dashboard timing tables. Click a filterable heading to open a small
 * popover listing every distinct value in the loaded rows; unchecking a
 * value hides those rows. All filtering is client-side over the fetched
 * result set — it narrows the view, never the query.
 */
import { useEffect, useRef, useState } from "react";

/** col index → set of values currently hidden. Missing column = show all. */
export type ColFilters = Record<number, Set<string>>;

export function useColFilters() {
  const [hidden, setHidden] = useState<ColFilters>({});
  const toggle = (col: number, val: string) =>
    setHidden((h) => {
      const s = new Set(h[col] ?? []);
      if (s.has(val)) s.delete(val);
      else s.add(val);
      return { ...h, [col]: s };
    });
  const setCol = (col: number, vals: string[], hide: boolean) =>
    setHidden((h) => ({ ...h, [col]: hide ? new Set(vals) : new Set<string>() }));
  /** vals[i] is the category value for filterable column i ("" for none). */
  const pass = (vals: string[]) => vals.every((v, i) => !hidden[i]?.has(v));
  const nHidden = (col: number) => hidden[col]?.size ?? 0;
  return { hidden, toggle, setCol, pass, nHidden };
}

export function FilterTh({
  col,
  label,
  tip,
  values,
  hidden,
  onToggle,
  onSet,
}: {
  col: number;
  label: string;
  /** plain-English meaning shown on hover, ahead of the filter hint. */
  tip?: string;
  /** distinct values present in the current rows (already sorted). */
  values: string[];
  hidden: Set<string> | undefined;
  onToggle: (col: number, val: string) => void;
  onSet: (col: number, vals: string[], hide: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLTableCellElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const nHidden = hidden?.size ?? 0;
  return (
    <th ref={ref} className="col-filter-th">
      <button
        type="button"
        className={`col-filter-btn${nHidden ? " filtering" : ""}`}
        title={`${tip ? `${tip} — ` : ""}Filter ${label} — ${values.length} values${nHidden ? `, ${nHidden} hidden` : ""}`}
        onClick={() => setOpen((o) => !o)}
      >
        {label} <span aria-hidden="true">▾</span>
        {nHidden > 0 && <span className="col-filter-n">{nHidden} off</span>}
      </button>
      {open && (
        <div className="col-filter-pop" role="menu">
          <div className="col-filter-actions">
            <button type="button" onClick={() => onSet(col, values, false)}>
              all
            </button>
            <button type="button" onClick={() => onSet(col, values, true)}>
              none
            </button>
          </div>
          {values.map((v) => (
            <label key={v || "(blank)"} className="col-filter-item">
              <input
                type="checkbox"
                checked={!hidden?.has(v)}
                onChange={() => onToggle(col, v)}
              />
              {v || "(blank)"}
            </label>
          ))}
        </div>
      )}
    </th>
  );
}

/** Sorted distinct values for one column across the current rows. */
export function distinct<T>(rows: T[], pick: (r: T) => string): string[] {
  return [...new Set(rows.map(pick))].sort();
}
