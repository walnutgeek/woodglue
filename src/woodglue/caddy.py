"""
Caddyfile for running woodglue behind Caddy (see `docs/adr/0004`).

`wgl caddy config <site>` prints a Caddyfile that:

- serves `<site>` with Caddy's automatic HTTPS;
- removes client-sent `X-Woodglue-*` headers, which only woodglue may set;
- serves each Static UI's files from disk, after asking woodglue's
  `/auth/verify` through `forward_auth` whether the viewer may open it;
- sends everything else to woodglue: the API, docs, login, the Console, and
  Process-backed UIs, whose processes woodglue runs on ports it picks at
  start and whose Session handling (cookie stripping, Delegated Tokens,
  closing websockets on logout) stays in one place.

The operator installs it and reloads Caddy. Caddy holds no Users, Sessions,
or Grants; woodglue stays the only place that decides who may do what.
"""

from __future__ import annotations

from woodglue.config import WoodglueConfig


def woodglue_address(config: WoodglueConfig) -> str:
    """
    Where Caddy reaches woodglue.

    >>> woodglue_address(WoodglueConfig(namespaces={}, host="0.0.0.0", port=8000))
    '127.0.0.1:8000'
    """
    host = "127.0.0.1" if config.host in ("0.0.0.0", "::", "") else config.host
    return f"{host}:{config.port}"


def caddyfile(config: WoodglueConfig, site: str) -> str:
    """The Caddyfile for `site`. UI paths in `config` must already be absolute."""
    upstream = woodglue_address(config)
    lines = [
        f"{site} {{",
        "\tencode zstd gzip",
        "",
        "\t# Identity headers come only from woodglue.",
        "\trequest_header -X-Woodglue-User",
        "\trequest_header -X-Woodglue-Token",
    ]
    for name, entry in sorted(config.uis.items()):
        if not entry.is_static:
            continue
        lines += [
            "",
            f"\t# Static UI '{name}': files from disk, access checked by woodglue.",
            f"\tredir /ui/{name} /ui/{name}/ 308",
            f"\thandle /ui/{name}/* {{",
            # `route` keeps this order; Caddy would otherwise strip the prefix
            # before forward_auth, which then reports the wrong path to woodglue.
            "\t\troute {",
        ]
        if not entry.public:
            lines += [
                f"\t\t\tforward_auth {upstream} {{",
                f"\t\t\t\turi /auth/verify?ui={name}",
                "\t\t\t}",
            ]
        lines += [
            f"\t\t\turi strip_prefix /ui/{name}",
            f"\t\t\troot * {entry.path}",
        ]
        if entry.spa_fallback:
            # Like woodglue's own spa_fallback: client routes get index.html, a
            # missing file with an extension stays a 404.
            lines += [
                "\t\t\t@client_route {",
                "\t\t\t\tnot path_regexp \\.[^/]+$",
                "\t\t\t\tnot file",
                "\t\t\t}",
                "\t\t\trewrite @client_route /index.html",
            ]
        lines += [
            "\t\t\tfile_server",
            "\t\t}",
            "\t}",
        ]
    lines += [
        "",
        "\t# The API, docs, login, the Console, and Process-backed UIs.",
        "\thandle {",
        f"\t\treverse_proxy {upstream}",
        "\t}",
        "}",
    ]
    return "\n".join(lines) + "\n"
