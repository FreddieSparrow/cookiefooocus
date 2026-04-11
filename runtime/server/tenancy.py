"""
runtime.server.tenancy — Per-tenant resource isolation
=========================================================
Enforces the multi-tenancy model:

    User → Tenant → Job Queue → GPU Pool

Each tenant has:
    - queue limit (max concurrent jobs)
    - VRAM budget (hard cap in MB)
    - concurrency cap
    - priority level

Tier definitions (from config/server.json):

    Tier        Jobs    VRAM limit  Priority
    ─────────────────────────────────────────
    free          1       low (4 GB)   low
    pro           3       med (8 GB)   medium
    enterprise   10       high (all)   high

Rules:
    - VRAM budget is enforced BEFORE a job enters the queue
    - Hard rejection if budget would be exceeded — no silent degradation
    - Tenant state is per-process (not persisted between restarts)

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict

# Tier definitions — override in config/server.json
TIER_DEFAULTS: dict[str, dict] = {
    "free": {
        "max_jobs":      1,
        "vram_budget_mb": 4096,
        "priority":      2,   # lower number = higher priority in scheduler
    },
    "pro": {
        "max_jobs":      3,
        "vram_budget_mb": 8192,
        "priority":      1,
    },
    "enterprise": {
        "max_jobs":      10,
        "vram_budget_mb": 0,   # 0 = no cap (uses global GPU limit)
        "priority":      0,
    },
}


@dataclass
class TenantPolicy:
    tier: str
    max_jobs: int
    vram_budget_mb: int
    priority: int


@dataclass
class TenantState:
    user_id: str
    policy: TenantPolicy
    active_jobs: int = field(default=0)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def can_submit(self) -> bool:
        return self.active_jobs < self.policy.max_jobs

    def acquire(self) -> bool:
        with self._lock:
            if self.active_jobs >= self.policy.max_jobs:
                return False
            self.active_jobs += 1
            return True

    def release(self) -> None:
        with self._lock:
            self.active_jobs = max(0, self.active_jobs - 1)


class TenantRegistry:
    """
    Thread-safe registry of active tenant states.
    One TenantState per authenticated user_id.
    """

    def __init__(self, tier_config: dict | None = None) -> None:
        self._tiers: dict[str, dict] = {**TIER_DEFAULTS, **(tier_config or {})}
        self._tenants: Dict[str, TenantState] = {}
        self._lock = threading.Lock()

    def get_or_create(self, user_id: str, tier: str = "free") -> TenantState:
        with self._lock:
            if user_id not in self._tenants:
                policy_cfg = self._tiers.get(tier, self._tiers["free"])
                policy = TenantPolicy(
                    tier=tier,
                    max_jobs=policy_cfg["max_jobs"],
                    vram_budget_mb=policy_cfg["vram_budget_mb"],
                    priority=policy_cfg["priority"],
                )
                self._tenants[user_id] = TenantState(user_id=user_id, policy=policy)
            return self._tenants[user_id]

    def remove(self, user_id: str) -> None:
        with self._lock:
            self._tenants.pop(user_id, None)

    def stats(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "user_id":     t.user_id,
                    "tier":        t.policy.tier,
                    "active_jobs": t.active_jobs,
                    "max_jobs":    t.policy.max_jobs,
                }
                for t in self._tenants.values()
            ]


# Singleton — one registry per server process
registry = TenantRegistry()
