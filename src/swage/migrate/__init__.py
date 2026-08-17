"""v0 to v1 recipe conversion (DESIGN.md 7)."""

from __future__ import annotations

from .convert import Conversion, convert_recipe
from .errors import MigrationError
from .forge_config import SETTINGS, ForgeConfigEdit, set_build_tools
from .licenses import license_problems, spdx_problems
from .plan import Migration, plan_migration
from .review import Condition, Review, review_conversion

__all__ = [
    "SETTINGS",
    "Condition",
    "Conversion",
    "ForgeConfigEdit",
    "Migration",
    "MigrationError",
    "Review",
    "convert_recipe",
    "license_problems",
    "plan_migration",
    "review_conversion",
    "set_build_tools",
    "spdx_problems",
]
