"""Async Redis client with pre-registered Lua scripts.

Wraps redis.asyncio and provides a clean interface for
executing the dispatcher's atomic Lua operations.
"""

from __future__ import annotations

import logging
import ssl as _ssl
from pathlib import Path
from urllib.parse import urlparse

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Directory containing Lua scripts
_LUA_DIR = Path(__file__).parent / "lua"

# Names of Lua scripts to register on connect
_SCRIPT_NAMES = ("append_and_increment", "try_claim", "pop_messages")


class RedisClient:
    """Async Redis client with pre-registered Lua scripts.

    Usage::

        client = RedisClient("rediss://my-endpoint:6379")
        await client.connect()

        result = await client.eval_script(
            "append_and_increment",
            keys=["session:user123"],
            args=["hello", "1709012345", "[]"],
        )

        await client.close()
    """

    def __init__(
        self,
        url: str,
        decode_responses: bool = True,
        ssl_cert_reqs: str | None = None,
    ):
        """
        Args:
            url: Redis connection URL (e.g. "rediss://host:6379" for TLS,
                 "redis://host:6379" for plain).
            decode_responses: Whether to decode byte responses to str.
            ssl_cert_reqs: SSL certificate requirements. Set to ``"none"``
                to skip hostname verification (useful for SSM tunnel / localhost).
        """
        self._url = url
        self._decode_responses = decode_responses
        self._ssl_cert_reqs = ssl_cert_reqs
        self._pool: aioredis.ConnectionPool | None = None
        self.client: aioredis.Redis | None = None
        self._scripts: dict[str, aioredis.client.Script] = {}

    async def connect(self) -> None:
        """Create connection pool and register Lua scripts."""
        if self._ssl_cert_reqs == "none":
            # When skipping SSL verification (e.g. SSM tunnel to localhost),
            # we must bypass ``from_url("rediss://...")`` because it creates
            # an SSLConnection with default check_hostname=True.
            # Instead, parse the URL and build the pool manually with
            # the correct SSLConnection kwargs.
            parsed = urlparse(self._url)
            self._pool = aioredis.ConnectionPool(
                host=parsed.hostname or "localhost",
                port=parsed.port or 6379,
                password=parsed.password or None,
                db=int(parsed.path.lstrip("/") or 0),
                connection_class=aioredis.connection.SSLConnection,
                ssl_cert_reqs="none",
                ssl_check_hostname=False,
                decode_responses=self._decode_responses,
            )
        else:
            pool_kwargs: dict = {
                "decode_responses": self._decode_responses,
            }
            if self._ssl_cert_reqs is not None:
                pool_kwargs["ssl_cert_reqs"] = self._ssl_cert_reqs
            self._pool = aioredis.ConnectionPool.from_url(
                self._url,
                **pool_kwargs,
            )
        self.client = aioredis.Redis(connection_pool=self._pool)

        # Verify connectivity
        await self.client.ping()
        logger.info("Redis connection established: %s", self._url[:40] + "...")

        # Register Lua scripts
        for name in _SCRIPT_NAMES:
            script_path = _LUA_DIR / f"{name}.lua"
            script_text = script_path.read_text(encoding="utf-8")
            self._scripts[name] = self.client.register_script(script_text)
            logger.debug("Registered Lua script: %s", name)

        logger.info("Registered %d Lua scripts", len(self._scripts))

    async def eval_script(self, name: str, keys: list, args: list):
        """Execute a registered Lua script.

        Args:
            name: Script name (without .lua extension).
            keys: Redis keys passed to the script.
            args: Arguments passed to the script.

        Returns:
            Script return value.

        Raises:
            KeyError: If script name is not registered.
            redis.RedisError: On Redis communication failure.
        """
        if name not in self._scripts:
            raise KeyError(f"Unknown Lua script: {name!r}. Available: {list(self._scripts)}")
        return await self._scripts[name](keys=keys, args=args)

    async def close(self) -> None:
        """Close the Redis connection pool."""
        if self.client:
            await self.client.aclose()
            self.client = None
        if self._pool:
            await self._pool.aclose()
            self._pool = None
        logger.info("Redis connection closed")

    async def __aenter__(self) -> "RedisClient":
        await self.connect()
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        await self.close()
