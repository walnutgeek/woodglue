# Host a project UI

A woodglue server can host your own web interface next to the Console, on the
same origin as `/rpc`. Users sign in once, and the UI calls the API with their
Session cookie: it never handles a token. Reference: `woodglue.config.UiEntry`
and `woodglue.apps.uis`.

## Build the app for its mount path

A UI named `reports` is served at `/ui/reports/`, so build it with that base.
For Vite (React, Vue, Svelte, or plain JS):

```js
// vite.config.js
import { defineConfig } from "vite";

export default defineConfig({
  base: "/ui/reports/",
  build: { outDir: "dist" },
});
```

Client-side routers need the same base, e.g.
`createBrowserRouter(routes, { basename: "/ui/reports" })` in React Router.

## Call the API

Send JSON-RPC to `/rpc` from the same origin. The browser attaches the Session
cookie; the request must be `application/json`, which `fetch` with a JSON body
already is:

```js
let id = 0;

export async function rpc(method, params = {}) {
  const resp = await fetch("/rpc", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", method, params, id: ++id }),
  });
  const data = await resp.json();
  if (data.error?.code === -32000) {
    // The Session ended; sign in again and come back here.
    location.assign(`/auth/login?next=${encodeURIComponent(location.pathname)}`);
  }
  if (data.error) throw new Error(data.error.message);
  return data.result;
}

const me = await rpc("system.whoami");
const rows = await rpc("reports.daily_totals", { day: "2026-10-01" });
```

`system.whoami` gives the signed-in User's name, email, and Roles, for
display. Authorization is still enforced by the server on every call.

## Configure the UI

Point `woodglue.yaml` at the build output (relative to the data dir):

```yaml
uis:
  reports:
    path: ../frontend/dist
    label: Reports
ui:
  default: reports        # `/` lands here instead of the Console
auth:
  roles:
    analyst: ["call reports", "view ui:reports"]
```

- Opening the UI needs `view` on `ui:reports`. Without a Session, a page load
  redirects to the login page and back.
- `spa_fallback` (on by default) serves `index.html` for paths like
  `/ui/reports/runs/42`, so client-side routes survive a reload. Paths with a
  file extension still 404.
- `public: true` serves the files without sign-in, e.g. for a status page. The
  API calls it makes still need a Caller.

The Console links to every UI the signed-in User may open
(`system.list_uis`).
