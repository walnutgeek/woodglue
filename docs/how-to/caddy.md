# Run woodglue behind Caddy

woodglue runs complete on its own. Put Caddy in front when you want automatic
HTTPS, a real domain, or static UI files served at the edge. Caddy never
decides who may do what: it asks woodglue (see `docs/adr/0004`).
Reference: `woodglue.caddy`.

## Generate the Caddyfile

```bash
wgl --data /srv/woodglue caddy config wg.example.com > /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

The generated site:

- gets a certificate for `wg.example.com` automatically;
- removes client-sent `X-Woodglue-User` and `X-Woodglue-Token` headers;
- serves each Static UI's files from disk. Before each request,
  `forward_auth` asks woodglue's `/auth/verify` whether the viewer may open the
  UI, so a visitor without a Session is sent to the login page and back, and
  one without `view` on `ui:<name>` gets 403;
- sends everything else to woodglue: `/rpc`, `/docs`, `/auth`, the Console,
  and Process-backed UIs such as Streamlit. woodglue runs those processes on
  ports it picks at start, and keeps their Session handling (cookie stripping,
  Delegated Tokens, closing websockets on sign-out) in one place.

Regenerate the file after adding or removing a Static UI.

## Configure woodglue for it

Keep woodglue on loopback so it is only reachable through Caddy, and tell it
the public address so sign-in callbacks point at Caddy:

```yaml
host: 127.0.0.1
port: 5321
auth:
  public_url: https://wg.example.com
```

## Put another app behind woodglue sign-in

`/auth/verify` works for any app Caddy proxies on the same site, since the
browser sends woodglue's Session cookie only to woodglue's own host. Name the
UI with `?ui=` and grant `view ui:<name>` to the Roles that may use it. Add a
block like this to the generated site, before its final `handle`:

```
	handle /grafana/* {
		forward_auth 127.0.0.1:5321 {
			uri /auth/verify?ui=grafana
			copy_headers X-Woodglue-User
		}
		reverse_proxy 127.0.0.1:3000
	}
```

The app receives `X-Woodglue-User`, but Caddy passes the woodglue Session
cookie through to it. For an app that should never see that cookie, configure
it as an `upstream:` UI instead and let woodglue proxy it.
