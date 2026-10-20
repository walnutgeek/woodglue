# Host a Streamlit app (or another Python web app)

woodglue can run a Streamlit app, or any web server, and serve it at
`/ui/<name>/` next to the Console. People sign in to woodglue once; the app
learns who is viewing and calls the woodglue API as them, with exactly their
Grants. Reference: `woodglue.config.UiEntry`, `woodglue.apps.ui_process`,
`woodglue.apps.ui_proxy`, and `woodglue.ui_support`.

## Streamlit

Install Streamlit in the same environment as woodglue, then configure the app
(paths are relative to the data dir):

```yaml
uis:
  dash:
    kind: streamlit
    app: dashboards/app.py
    label: Dashboards
auth:
  roles:
    analyst: ["call reports", "view ui:dash"]
```

`wgl start` runs `streamlit run` on a free loopback port with
`--server.baseUrlPath=ui/dash`, waits for `/_stcore/health`, and restarts the
app if it exits. Set `dev: true` to reload the app when its files change.

In the app, ask woodglue who is viewing and call the API as them:

```python
import asyncio

import streamlit as st

from woodglue.ui_support import current_client, current_user

me = current_user()
st.write(f"Hello, {me.name}")


async def load():
    return await current_client().call("reports.daily_totals", day="2026-10-01")


st.dataframe(asyncio.run(load()))
```

A complete example is in `examples/streamlit/`:

```bash
uv run --with streamlit wgl --data examples/streamlit start
```

## Any other web server

Give the command line instead; `{port}` and `{base_path}` are filled in. The
server must listen on `127.0.0.1:{port}` and serve everything under
`{base_path}` (`/ui/<name>`), since woodglue forwards paths unchanged:

```yaml
uis:
  board:
    command: [python, -m, myboard, --port, "{port}", --root-path, "{base_path}"]
    ready_path: /ui/board/healthz      # default: /ui/board/
    env: {BOARD_THEME: dark}
```

For a server you run yourself, give its address instead; woodglue only
proxies to it:

```yaml
uis:
  grafana:
    upstream: http://127.0.0.1:3000   # serving under /ui/grafana
```

## What the app receives

Every request and websocket woodglue forwards carries:

- `X-Woodglue-User`: JSON with the viewer's `id`, `name`, `email`, and
  `roles`.
- `X-Woodglue-Token`: a Delegated Token for the viewer's browser Session. It
  calls the API with their Grants and stops working when they sign out or
  their Session expires.

woodglue removes its own Session cookie and any client-sent `X-Woodglue-*`
header first, and keeps the app's own cookies. Websockets close when the
viewer signs out. The process gets `WOODGLUE_URL`, the API's loopback
address, in its environment.

Only trust these headers when the app is reachable through woodglue alone:
keep it on loopback, as woodglue does for the processes it runs.

The Console lists each UI with its state while the process is starting or
restarting; the proxy answers 503 until it is ready.
