"""
Authorization model: Grants, Roles, and the Caller of a request.

A **Grant** permits one action on the targets matching a glob pattern. Actions:

- `call`: invoke methods of a namespace.
- `read`: see runs, run IO, and triggers of a namespace.
- `trigger`: fire, activate, and deactivate triggers of a namespace.
- `view`: open a UI; its targets are `ui:<name>`.
- `admin`: manage Users, Role assignments, and tokens. Its pattern is ignored.

A namespace may also declare its own actions, such as `write`: a node that
declares `requires="write"` (see `required_action`) runs only for a Caller
with a Grant for `write` on its namespace. The other built-in actions are
reserved, and declared names are lowercase names.

No action implies another, so `call *` never reaches a `write` node. Grants are
written as `"<action> <pattern>"` strings in `woodglue.yaml`; a bare `"admin"`
needs no pattern:

```
auth:
  roles:
    operator: ["call reports", "read *", "trigger reports"]
    viewer: ["read *", "view ui:*"]
    writer: ["call cointoss", "write cointoss"]
```

The `admin` Role is built in and holds every Grant, including every declared
action. There are no wildcard actions in config.

A **Caller** is who a request runs as. It carries one or more Grant sets and is
allowed an action only if every set allows it, so a narrowed Personal Access
Token never exceeds its User's current Grants. `current_caller` holds the Caller
while a JSON-RPC call runs; it is unset for trusted in-process calls (tests,
`wgl run`), which `check` then allows.
"""

from __future__ import annotations

import re
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
"""Built-in actions whose targets are namespaces; any of them makes a namespace visible."""

RESERVED_ACTIONS = (Action.read, Action.trigger, Action.view, Action.admin)
"""Built-in actions a node cannot declare as its Required Action."""

ANY_ACTION = "*"
"""
Action of the one Grant in the built-in `admin` Role that reaches every action,
including namespace-declared ones. It cannot be written in config.
"""

_ACTION_NAME = re.compile(r"[a-z][a-z0-9_]*")


def is_declared_action(action: Action | str) -> bool:
    """Whether `action` is a namespace-declared one, not built in or `ANY_ACTION`."""
    return action not in Action.__members__ and action != ANY_ACTION


def action_name(name: str) -> Action | str:
    """
    A built-in `Action`, or a namespace-declared action name, which must be a
    lowercase name so that `"<action> <pattern>"` stays unambiguous.

    >>> action_name("call"), action_name("write")
    (<Action.call: 'call'>, 'write')
    """
    if name in Action.__members__:
        return Action(name)
    if not _ACTION_NAME.fullmatch(name):
        raise ValueError(f"Invalid action name {name!r}")
    return name


class Grant(BaseModel):
    """
    Permission to perform `action` on targets matching the glob `pattern`.

    >>> Grant.parse("call reports")
    Grant(action=<Action.call: 'call'>, pattern='reports')
    >>> str(Grant.parse("admin"))
    'admin *'

    An action that is not built in is a namespace-declared one:

    >>> Grant.parse("write cointoss")
    Grant(action='write', pattern='cointoss')
    >>> Grant.parse("* cointoss")
    Traceback (most recent call last):
    ...
    ValueError: Invalid action '*' in grant '* cointoss'; use a built-in action or a lowercase name
    """

    model_config: ConfigDict = ConfigDict(frozen=True)  # pyright: ignore[reportIncompatibleVariableOverride]

    action: Action | str
    pattern: str = "*"

    @classmethod
    def parse(cls, text: str) -> Grant:
        parts = text.split()
        if len(parts) == 1 and parts[0] == Action.admin:
            return cls(action=Action.admin)
        if len(parts) != 2:
            raise ValueError(f"Grant must be '<action> <pattern>', got {text!r}")
        try:
            action = action_name(parts[0])
        except ValueError:
            raise ValueError(
                f"Invalid action {parts[0]!r} in grant {text!r}; "
                "use a built-in action or a lowercase name"
            ) from None
        return cls(action=action, pattern=parts[1])

    def matches(self, action: Action | str, target: str) -> bool:
        if self.action == ANY_ACTION:
            return True
        if action != self.action:
            return False
        return action == Action.admin or fnmatchcase(target, self.pattern)

    @override
    def __str__(self) -> str:
        return f"{self.action} {self.pattern}"


def allows(grants: Iterable[Grant], action: Action | str, target: str = "") -> bool:
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
ALL_GRANTS: tuple[Grant, ...] = (*(Grant(action=a) for a in Action), Grant(action=ANY_ACTION))


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
    token or Session that authenticated the request. A `pending` Caller is a
    User awaiting approval: it holds no Grants, whatever its `roles`.

    A namespace-declared action is reached only by a Grant naming it (or by
    the built-in `admin` Role), never by a `call` wildcard, and it makes the
    namespace visible like any namespace action:

    >>> reader = Caller("u1", (tuple(Grant.parse(g) for g in ["call *"]),))
    >>> reader.can(Action.call, "cointoss"), reader.can("write", "cointoss")
    (True, False)
    >>> writer = Caller("u2", (tuple(Grant.parse(g) for g in ["write cointoss"]),))
    >>> writer.can("write", "cointoss"), writer.can(Action.call, "cointoss")
    (True, False)
    >>> writer.can_see("cointoss"), writer.can_see("reports")
    (True, False)
    >>> admin = Caller("u3", (resolve_roles({})[ADMIN_ROLE],))
    >>> admin.can("write", "cointoss")
    True
    """

    user_id: str | None
    grant_sets: tuple[tuple[Grant, ...], ...]
    credential_id: str | None = None
    credential_kind: str | None = None
    name: str = ""
    email: str | None = None
    roles: tuple[str, ...] = field(default_factory=tuple)
    pending: bool = False

    def can(self, action: Action | str, target: str = "") -> bool:
        return all(allows(gs, action, target) for gs in self.grant_sets)

    @property
    def narrowed(self) -> bool:
        return len(self.grant_sets) > 1

    @property
    def has_grants(self) -> bool:
        """
        Whether the User holds any Grant at all, e.g. `False` for a User with no
        Roles or a `pending` one. Only the first Grant set is the User's own;
        later sets narrow it.
        """
        return bool(self.grant_sets and self.grant_sets[0])

    @property
    def may_issue_credentials(self) -> bool:
        """
        Whether this Caller may create tokens or Sessions. Only an unnarrowed
        Personal Access Token or a Session may, and not while its User is
        pending: a narrowed token could mint one broader than itself, and a
        Delegated Token one that outlives its Session.
        """
        return (
            self.user_id is not None
            and not self.pending
            and not self.narrowed
            and self.credential_kind
            in (
                "token",
                "session",
            )
        )

    def can_see(self, namespace: str) -> bool:
        """Whether `namespace` appears in discovery for this Caller."""
        declared = {g.action for gs in self.grant_sets for g in gs if is_declared_action(g.action)}
        return any(self.can(a, namespace) for a in (*NAMESPACE_ACTIONS, *declared))


ANONYMOUS = Caller(user_id=None, grant_sets=(ALL_GRANTS,), name="anonymous")
"""The Caller of every request when auth is disabled."""


current_caller: ContextVar[Caller | None] = ContextVar("current_caller", default=None)


def required_action(requires: str | None) -> Action | str:
    """
    The Required Action of a node from its declared `requires`: `call` unless
    it names a namespace-declared action. The other built-in actions are
    reserved, since they mean the same on every server.

    >>> required_action(None), required_action("write")
    (<Action.call: 'call'>, 'write')
    >>> required_action("read")
    Traceback (most recent call last):
    ...
    ValueError: Required action 'read' is built in and reserved; declare a namespace action such as 'write'
    """
    if requires is None:
        return Action.call
    action = action_name(requires)
    if action in RESERVED_ACTIONS:
        raise ValueError(
            f"Required action {requires!r} is built in and reserved; "
            "declare a namespace action such as 'write'"
        )
    return action


class AccessDenied(Exception):
    """Raised by `check` when the current Caller lacks a Grant."""

    action: Action | str
    target: str

    def __init__(self, action: Action | str, target: str) -> None:
        self.action = action
        self.target = target
        where = f" on '{target}'" if target and action != Action.admin else ""
        super().__init__(f"Forbidden: requires '{action}'{where}")


def check(action: Action | str, target: str = "") -> None:
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
