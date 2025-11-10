#!/usr/bin/env python3
"""
Async Safety Helpers for Carla MCP Server

Provides reusable utilities for wrapping blocking operations in async-safe wrappers.
Prevents event loop blocking that causes timeout crashes.
"""

import asyncio
import functools
import logging
from typing import Callable, Any, Optional, TypeVar, ParamSpec

logger = logging.getLogger(__name__)

P = ParamSpec('P')
T = TypeVar('T')


def async_safe(timeout: float = 30.0, description: str = "operation"):
    """
    Decorator to wrap blocking functions for async-safe execution.

    Automatically runs the decorated function in a thread pool with timeout protection.

    Args:
        timeout: Maximum execution time in seconds (default: 30.0)
        description: Human-readable description for logging

    Usage:
        @async_safe(timeout=5.0, description="load project file")
        def _blocking_load(path):
            return self.carla.load_project(path)

        result = await _blocking_load(path)

    Example:
        # Before (BLOCKS event loop):
        async def load_session(self, path):
            success = self.carla.load_project(path)  # BLOCKING!

        # After (async-safe):
        async def load_session(self, path):
            @async_safe(timeout=10.0, description="load project")
            def _load():
                return self.carla.load_project(path)
            success = await _load()
    """
    def decorator(func: Callable[P, T]) -> Callable[P, asyncio.Future[T]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                # Run blocking function in thread pool with timeout
                result = await asyncio.wait_for(
                    asyncio.to_thread(func, *args, **kwargs),
                    timeout=timeout
                )
                return result
            except asyncio.TimeoutError:
                error_msg = f"{description} exceeded {timeout}s timeout"
                logger.error(error_msg)
                raise TimeoutError(error_msg)
            except Exception as e:
                logger.error(f"{description} failed: {e}")
                raise

        return wrapper
    return decorator


async def run_blocking(
    func: Callable[P, T],
    *args: P.args,
    timeout: float = 30.0,
    description: str = "blocking operation",
    **kwargs: P.kwargs
) -> T:
    """
    Run a blocking function safely in async context.

    This is a functional alternative to the @async_safe decorator.
    Use when you can't use a decorator (e.g., calling existing methods).

    Args:
        func: Blocking function to execute
        *args: Positional arguments for func
        timeout: Maximum execution time in seconds
        description: Human-readable description for logging
        **kwargs: Keyword arguments for func

    Returns:
        Result from the blocking function

    Raises:
        TimeoutError: If operation exceeds timeout

    Usage:
        # Before (BLOCKS):
        result = self.carla.load_project(path)

        # After (async-safe):
        result = await run_blocking(
            self.carla.load_project,
            path,
            timeout=10.0,
            description="load project"
        )

    Example:
        # File I/O
        content = await run_blocking(
            json.load,
            open(path),
            timeout=5.0,
            description=f"load JSON from {path}"
        )

        # Carla API
        plugin_id = await run_blocking(
            self.carla.load_plugin,
            plugin_path, plugin_type,
            timeout=15.0,
            description="load plugin"
        )
    """
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(func, *args, **kwargs),
            timeout=timeout
        )
        return result
    except asyncio.TimeoutError:
        error_msg = f"{description} exceeded {timeout}s timeout"
        logger.error(error_msg)
        raise TimeoutError(error_msg)
    except Exception as e:
        logger.error(f"{description} failed: {e}")
        raise


async def batch_blocking(
    operations: list[tuple[Callable, tuple, dict]],
    batch_size: int = 20,
    timeout_per_batch: float = 30.0,
    description: str = "batch operations"
) -> list[Any]:
    """
    Execute multiple blocking operations in batches with async safety.

    Processes operations in batches to prevent long blocking periods and
    yields control to the event loop between batches.

    Args:
        operations: List of (func, args, kwargs) tuples
        batch_size: Number of operations per batch
        timeout_per_batch: Timeout for each batch in seconds
        description: Human-readable description

    Returns:
        List of results from all operations

    Usage:
        # Prepare operations
        ops = [
            (self.carla.get_plugin_info, (i,), {}),
            (self.carla.get_plugin_info, (i+1,), {}),
            ...
        ]

        # Execute in batches
        results = await batch_blocking(
            ops,
            batch_size=20,
            timeout_per_batch=30.0,
            description="get plugin info"
        )

    Example:
        # Get info for 100 plugins (batched to prevent long blocks)
        plugin_ops = [
            (self.carla.host.get_plugin_info, (i,), {})
            for i in range(100)
        ]

        plugin_infos = await batch_blocking(
            plugin_ops,
            batch_size=20,
            description="fetch plugin metadata"
        )
    """
    results = []

    for batch_start in range(0, len(operations), batch_size):
        batch_end = min(batch_start + batch_size, len(operations))
        batch = operations[batch_start:batch_end]

        # Execute batch in thread pool
        def execute_batch():
            batch_results = []
            for func, args, kwargs in batch:
                try:
                    result = func(*args, **kwargs)
                    batch_results.append(result)
                except Exception as e:
                    logger.warning(f"Batch operation failed: {e}")
                    batch_results.append(None)
            return batch_results

        # Run batch with timeout
        try:
            batch_results = await asyncio.wait_for(
                asyncio.to_thread(execute_batch),
                timeout=timeout_per_batch
            )
            results.extend(batch_results)
        except asyncio.TimeoutError:
            logger.error(f"{description} batch {batch_start}-{batch_end} timed out")
            results.extend([None] * len(batch))

        # Yield control to event loop between batches
        await asyncio.sleep(0)

    return results


class AsyncFileIO:
    """
    Async-safe file I/O operations.

    All file operations are automatically wrapped for safe async execution.
    """

    @staticmethod
    async def read_text(path: str, encoding: str = 'utf-8', timeout: float = 10.0) -> str:
        """Read text file async-safely"""
        def _read():
            with open(path, 'r', encoding=encoding) as f:
                return f.read()

        return await run_blocking(
            _read,
            timeout=timeout,
            description=f"read {path}"
        )

    @staticmethod
    async def write_text(path: str, content: str, encoding: str = 'utf-8', timeout: float = 10.0):
        """Write text file async-safely"""
        def _write():
            with open(path, 'w', encoding=encoding) as f:
                f.write(content)

        await run_blocking(
            _write,
            timeout=timeout,
            description=f"write {path}"
        )

    @staticmethod
    async def read_json(path: str, timeout: float = 10.0) -> dict:
        """Read JSON file async-safely"""
        import json

        def _read():
            with open(path, 'r') as f:
                return json.load(f)

        return await run_blocking(
            _read,
            timeout=timeout,
            description=f"read JSON {path}"
        )

    @staticmethod
    async def write_json(path: str, data: dict, timeout: float = 10.0):
        """Write JSON file async-safely"""
        import json

        def _write():
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)

        await run_blocking(
            _write,
            timeout=timeout,
            description=f"write JSON {path}"
        )

    @staticmethod
    async def copy_file(src: str, dst: str, timeout: float = 30.0):
        """Copy file async-safely"""
        import shutil

        await run_blocking(
            shutil.copy2,
            src, dst,
            timeout=timeout,
            description=f"copy {src} → {dst}"
        )

    @staticmethod
    async def remove_tree(path: str, timeout: float = 30.0):
        """Remove directory tree async-safely"""
        import shutil

        await run_blocking(
            shutil.rmtree,
            path,
            timeout=timeout,
            description=f"remove tree {path}"
        )

    @staticmethod
    async def get_size(path: str, timeout: float = 5.0) -> int:
        """Get file size async-safely"""
        import os

        return await run_blocking(
            os.path.getsize,
            path,
            timeout=timeout,
            description=f"stat {path}"
        )


# Convenience exports
__all__ = [
    'async_safe',
    'run_blocking',
    'batch_blocking',
    'AsyncFileIO'
]
