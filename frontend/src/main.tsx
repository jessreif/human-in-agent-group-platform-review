import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { BASE_PATH } from "./config";
import "./styles.css";

// Admin surface (INV-1301): only the /investigators/admin route loads the admin bundle —
// it stays a separate lazy chunk so the participant path never ships admin code.
const AdminApp = lazy(() =>
  import("./admin/AdminApp").then((m) => ({ default: m.AdminApp })),
);

const isAdminRoute = window.location.pathname.startsWith(`${BASE_PATH}admin`);

const root = document.getElementById("root");
if (!root) {
  throw new Error("missing #root element");
}
createRoot(root).render(
  <StrictMode>
    {isAdminRoute ? (
      <Suspense fallback={<main>Loading admin…</main>}>
        <AdminApp />
      </Suspense>
    ) : (
      <App />
    )}
  </StrictMode>,
);
