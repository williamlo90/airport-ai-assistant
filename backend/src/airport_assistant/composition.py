"""Composition root: assemble services once per application, never globally.

Concrete repository/provider bindings belong here as features become executable.
Do not put transactions or tenant/actor session state in this application scope.
"""

from dataclasses import dataclass

from airport_assistant.modules.system.health import HealthService, ProcessHealthService


@dataclass(frozen=True)
class ApplicationServices:
    health: HealthService


def build_services() -> ApplicationServices:
    return ApplicationServices(health=ProcessHealthService())
