import { useSyncExternalStore } from "react";

import type { SessionStore, StoreState } from "./store";

/** React binding for the session store (tear-free reads via useSyncExternalStore). */
export function useStoreState(store: SessionStore): StoreState {
  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}
