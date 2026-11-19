"""pgvector column type without the pgvector package.

Postgres stores `vector(EMBED_DIM)`; SQLite (tests) stores the same text
form in a TEXT column. Both sides speak pgvector's literal `[f1,f2,...]`, so
the Python value is always a tuple of floats of exactly EMBED_DIM items.
"""
from sqlalchemy import Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine, UserDefinedType

EMBED_DIM = 1536


class _PgVector(UserDefinedType[str]):
    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **kw: object) -> str:
        return f"vector({self.dim})"


def to_literal(values: tuple[float, ...]) -> str:
    return "[" + ",".join(f"{v:.7g}" for v in values) + "]"


def from_literal(text: str) -> tuple[float, ...]:
    body = text.strip().removeprefix("[").removesuffix("]")
    return tuple(float(x) for x in body.split(",")) if body else ()


class Vector(TypeDecorator[tuple[float, ...]]):
    impl = Text
    cache_ok = True

    def __init__(self, dim: int = EMBED_DIM) -> None:
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[str]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PgVector(self.dim))
        return dialect.type_descriptor(Text())

    def process_bind_param(
        self, value: tuple[float, ...] | None, dialect: Dialect
    ) -> str | None:
        if value is None:
            return None
        if len(value) != self.dim:
            raise ValueError(f"vetor com {len(value)} dimensões; esperado {self.dim}")
        return to_literal(value)

    def process_result_value(
        self, value: str | None, dialect: Dialect
    ) -> tuple[float, ...] | None:
        return None if value is None else from_literal(value)
