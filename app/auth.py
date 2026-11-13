from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional

from fastapi import Header, HTTPException

# In production: Entra ID JWT claims. Here: a demo token resolved server-side.
# Tenant, roles, and manager are NEVER taken from the request body.
_DIRECTORY: dict[str, dict] = {
    "emp_a1":   {"tenant_id": "A", "roles": ["employee"],               "manager_id": "mgr_a1"},
    "mgr_a1":   {"tenant_id": "A", "roles": ["employee", "approver"],   "manager_id": None},
    "emp_a2":   {"tenant_id": "A", "roles": ["employee"],               "manager_id": "mgr_a1"},
    "emp_b1":   {"tenant_id": "B", "roles": ["employee"],               "manager_id": "mgr_b1"},
    "mgr_b1":   {"tenant_id": "B", "roles": ["employee", "approver"],   "manager_id": None},
    "hr_admin": {"tenant_id": "A", "roles": ["employee", "hr_admin"],   "manager_id": None},
}


@dataclass
class Principal:
    user_id:    str
    tenant_id:  str
    roles:      List[str]
    manager_id: Optional[str]

    def has_role(self, role: str) -> bool:
        return role in self.roles


async def get_principal(x_demo_user: str = Header(...)) -> Principal:
    entry = _DIRECTORY.get(x_demo_user)
    if not entry:
        raise HTTPException(status_code=401, detail="Unknown demo user")
    return Principal(
        user_id=x_demo_user,
        tenant_id=entry["tenant_id"],
        roles=list(entry["roles"]),
        manager_id=entry["manager_id"],
    )
