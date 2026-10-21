"""
Authorization model: Grants, Roles, and the Caller of a request.

A **Grant** permits one action on the targets matching a glob pattern. Actions:

- `call`: invoke methods of a namespace.
- `read`: see runs, run IO, and triggers of a namespace.
- `trigger`: fire, activate, and deactivate triggers of a namespace.
- `view`: open a UI; its targets are `ui:<name>`.
- `admin`: manage Users, Role assignments, and tokens. Its pattern is ignored.

No action implies another. Grants are written as `"<action> <pattern>"` strings
in `woodglue.yaml`; a bare `"admin"` needs no pattern:

```
auth:
  roles:
    operator: ["call reports", "read *", "trigger reports"]
    viewer: ["read *", "view ui:*"]
```

The `admin` Role is built in and holds every Grant.

A **Caller** is who a request runs as. It carries one or more Grant sets and is
allowed an action only if every set allows it, so a narrowed Personal Access
Token never exceeds its User's current Grants. `current_caller` holds the Caller
while a JSON-RPC call runs; it is unset for trusted in-process calls (tests,
`wgl run`), which `check` then allows.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from fnmatch import fnmatchcase

from pydantic import BaseModel, ConfigDict
from typing_extensions import override


class Action(StrEnum):
    call = "call"
    read = "read"
    trigger = "trigger"
    view = "view"
    admin = "admin"


NAMESPACE_ACTIONS = (Action.call, Action.read, Action.trigger)
"""Actions whose targets are namespaces; any of them makes a namespace visible."""


class Grant(BaseModel):
    """
    Permission to perform `action` on targets matching the glob `pattern`.

    >>> Grant.parse("call reports")
    Grant(action=<Action.call: 'call'>, pattern='reports')
    >>> str(Grant.parse("admin"))
    'admin *'
    """

    model_config: ConfigDict = ConfigDict(frozen=True)  # pyright: ignore[reportIncompatibleVariableOverride]

    action: Action
    pattern: str = "*"

    @classmethod
    def parse(cls, text: str) -> Grant:
        parts = text.split()
        if len(parts) == 1 and parts[0] == Action.admin:
            return cls(action=Action.admin)
        if len(parts) != 2:
            raise ValueError(f"Grant must be '<action> <pattern>', got {text!r}")
        try:
            action = Action(parts[0])
        except ValueError:
            valid = ", ".join(a.value for a in Action)
            raise ValueError(
                f"Unknown action {parts[0]!r} in grant {text!r}; use {valid}"
            ) from None
        return cls(action=action, pattern=parts[1])

    def matches(self, action: Action, target: str) -> bool:
        if action != self.action:
            return False
        return action == Action.admin or fnmatchcase(target, self.pattern)

    @override
    def __str__(self) -> str:
        return f"{self.action} {self.pattern}"


def allows(grants: Iterable[Grant], action: Action, target: str = "") -> bool:
    """
    Whether any of `grants` permits `action` on `target`.

    >>> gs = [Grant.parse("call rep*"), Grant.parse("view ui:*")]
    >>> allows(gs, Action.call, "reports"), allows(gs, Action.call, "billing")
    (True, False)
    >>> allows(gs, Action.read, "reports")
    False
    >>> allows(gs, Action.view, "ui:billing")
    True
    """
    return any(g.matches(action, target) for g in grants)


ADMIN_ROLE = "admin"
ALL_GRANTS: tuple[Grant, ...] = tuple(Grant(action=a) for a in Action)


def resolve_roles(roles: dict[str, list[str]]) -> dict[str, tuple[Grant, ...]]:
    """
    Parse Role definitions from config and add the built-in `admin` Role.

    >>> r = resolve_roles({"viewer": ["read *"]})
    >>> sorted(r), [str(g) for g in r["viewer"]]
    (['admin', 'viewer'], ['read *'])
    """
    if ADMIN_ROLE in roles:
        raise ValueError(f"Role '{ADMIN_ROLE}' is built in and cannot be redefined")
    resolved = {name: tuple(Grant.parse(g) for g in grants) for name, grants in roles.items()}
    resolved[ADMIN_ROLE] = ALL_GRANTS
    return resolved


@dataclass(frozen=True)
class Caller:
    """
    Who a request runs as. `user_id` is `None` only for the anonymous Caller
    used when auth is disabled. `credential_id` and `credential_kind` name the
    token or Session that authenticated the request.
    """

    user_id: str | None
    grant_sets: tuple[tuple[Grant, ...], ...]
    credential_id: str | None = None
    credential_kind: str | None = None
    name: str = ""
    email: str | None = None
    roles: tuple[str, ...] = field(default_factory=tuple)

    def can(self, action: Action, target: str = "") -> bool:
        return all(allows(gs, action, target) for gs in self.grant_sets)

    @property
    def narrowed(self) -> bool:
        return len(self.grant_sets) > 1

    @property
    def may_issue_credentials(self) -> bool:
        """
        Whether this Caller may create tokens or Sessions. Only an unnarrowed
        Personal Access Token or a Session may: a narrowed token could mint
        one broader than itself, and a Delegated Token one that outlives its
        Session.
        """
        return (
            self.user_id is not None
            and not self.narrowed
            and self.credential_kind
            in (
                "token",
                "session",
            )
        )

    def can_see(self, namespace: str) -> bool:
        """Whether `namespace` appears in discovery for this Caller."""
        return any(self.can(a, namespace) for a in NAMESPACE_ACTIONS)


ANONYMOUS = Caller(user_id=None, grant_sets=(ALL_GRANTS,), name="anonymous")
"""The Caller of every request when auth is disabled."""


current_caller: ContextVar[Caller | None] = ContextVar("current_caller", default=None)


class AccessDenied(Exception):
    """Raised by `check` when the current Caller lacks a Grant."""

    action: Action
    target: str

    def __init__(self, action: Action, target: str) -> None:
        self.action = action
        self.target = target
        where = f" on '{target}'" if target and action != Action.admin else ""
        super().__init__(f"Forbidden: requires '{action}'{where}")


def check(action: Action, target: str = "") -> None:
    """Raise `AccessDenied` unless the current Caller may perform `action` on `target`."""
    caller = current_caller.get()
    if caller is not None and not caller.can(action, target):
        raise AccessDenied(action, target)


def visible(names: Sequence[str]) -> list[str]:
    """Filter namespace names to those the current Caller can see."""
    caller = current_caller.get()
    if caller is None:
        return list(names)
    return [n for n in names if caller.can_see(n)]
