"""Seed package. ``python -m app.seed`` (or ``yxp-seed``) seeds demo data."""

from __future__ import annotations

import asyncio

from app.seed.demo import bootstrap_vector_aliases, seed_all

__all__ = ["bootstrap_vector_aliases", "main", "seed_all"]


async def _run() -> None:
    from app.infra import mongo, pg

    await pg.init_models()
    await mongo.init_indexes()
    await seed_all()
    await bootstrap_vector_aliases()
    await mongo.close()
    await pg.dispose_engine()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
