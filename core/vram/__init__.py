"""
core.vram — VRAM governor and hardware profiles
================================================
Wraps modules.generation_controller.resource_manager.

Public API:
    get_hardware_profile()  → HardwareProfile
    VRAMGovernor            — pre-flight VRAM check and auto-downscaling
    ResourceConfig          — dataclass for resource parameters
"""

from modules.generation_controller.resource_manager import (  # noqa: F401
    VRAMGovernor,
    ResourceConfig,
    HardwareProfile,
    get_hardware_profile,
)
