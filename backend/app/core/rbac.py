"""Role → permission matrix (ARCHITECTURE.md §49, design DS-10).

USER permissions are always evaluated against the user's *own* resources: the /user API only ever
queries rows owned by the caller, so the permission grants the action and the route supplies the scope.
"""

from __future__ import annotations

from app.models.enums import RoleName

PERMISSIONS: dict[str, str] = {
    "users.read": "View users",
    "users.write": "Create, edit, suspend users and change limits",
    "campaigns.read": "View campaigns",
    "campaigns.write": "Create and edit campaigns, upload recipients",
    "campaigns.start": "Start / resume campaigns",
    "campaigns.stop": "Pause / cancel campaigns",
    "providers.read": "View providers",
    "providers.write": "Create, edit, enable/disable, assign providers",
    "workers.read": "View workers",
    "workers.write": "Provision, disable workers and rotate credentials",
    "queues.read": "View queues and jobs",
    "queues.write": "Requeue dead-letter jobs",
    "suppressions.read": "View suppression list",
    "suppressions.write": "Add / remove suppressions",
    "reports.read": "View reports and dashboards",
    "audit.read": "View audit logs",
    "settings.read": "View system settings",
    "settings.write": "Change system settings",
}

_ALL = set(PERMISSIONS)
_READ = {p for p in PERMISSIONS if p.endswith(".read")}

ROLE_PERMISSIONS: dict[RoleName, set[str]] = {
    RoleName.SUPER_ADMIN: set(_ALL),
    RoleName.ADMIN: _ALL - {"settings.write"},
    RoleName.OPERATOR: {
        "users.read",
        "campaigns.read",
        "campaigns.write",
        "campaigns.start",
        "campaigns.stop",
        "providers.read",
        "workers.read",
        "queues.read",
        "queues.write",
        "suppressions.read",
        "suppressions.write",
        "reports.read",
    },
    RoleName.VIEWER: set(_READ) - {"settings.read"},
    RoleName.USER: {
        "campaigns.read",
        "campaigns.write",
        "campaigns.start",
        "campaigns.stop",
        "providers.read",
        "reports.read",
    },
}

ROLE_DESCRIPTIONS: dict[RoleName, str] = {
    RoleName.SUPER_ADMIN: "Full access including system settings",
    RoleName.ADMIN: "Administers users, campaigns, providers and workers",
    RoleName.OPERATOR: "Operates campaigns and queues; read-only on infrastructure",
    RoleName.USER: "Sends assigned campaigns through assigned providers",
    RoleName.VIEWER: "Read-only access to the admin panel",
}


def permissions_for(role: str) -> set[str]:
    try:
        return ROLE_PERMISSIONS[RoleName(role)]
    except ValueError:
        return set()
