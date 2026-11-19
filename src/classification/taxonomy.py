"""Typed taxonomy: categories and flags the classifier works with."""
from datetime import date
from typing import Literal, Self, get_args

from pydantic import model_validator

from src.llm.types import Schema

FlagName = Literal["is_relevant", "deadline", "has_document", "ephemeral"]
FLAG_NAMES: tuple[FlagName, ...] = get_args(FlagName)


class CategoryDef(Schema):
    name: str
    definition: str
    examples: tuple[str, ...]
    color: str | None = None


class FlagDef(Schema):
    name: FlagName
    definition: str


class TaxonomySpec(Schema):
    categories: tuple[CategoryDef, ...]
    flags: tuple[FlagDef, ...]

    @model_validator(mode="after")
    def _check(self) -> Self:
        names = [c.name for c in self.categories]
        if not names or len(set(names)) != len(names):
            raise ValueError("categorias devem ser não vazias e com nomes únicos")
        if sorted(f.name for f in self.flags) != sorted(FLAG_NAMES):
            raise ValueError(f"flags devem ser exatamente {FLAG_NAMES}")
        return self


class Taxonomy(Schema):
    set_id: int
    version: int
    spec: TaxonomySpec

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.spec.categories]


class PreferenceSeed(Schema):
    scope: str
    text: str


class Classification(Schema):
    """Outcome of classifying one email against one taxonomy version."""

    category: str
    reason: str
    is_relevant: bool
    deadline: date | None
    has_document: bool
    ephemeral: bool
    category_set_id: int
