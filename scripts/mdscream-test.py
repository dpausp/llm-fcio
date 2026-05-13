#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""mdscream-test — Produce streaming markdown to test mdscream.

Simulates LLM-like token-by-token output with varied markdown elements,
realistic token sizes, and configurable speed.

Usage:
    uv run scripts/mdscream-test.py | uv run scripts/mdscream.py
    uv run scripts/mdscream-test.py --fast | uv run scripts/mdscream.py
    uv run scripts/mdscream-test.py --tokens   # show token boundaries
"""

import argparse
import random
import sys
import time

# ---------------------------------------------------------------------------
# Test documents
# ---------------------------------------------------------------------------

DOCUMENT = """\
# Python Decorators Explained

Decorators are one of Python's most powerful features. They allow you to **modify** or *extend* the behavior of functions and methods without permanently modifying them.

## Basic Syntax

A decorator is a function that takes another function and extends its behavior. Here's the simplest form:

```python
def my_decorator(func):
    def wrapper(*args, **kwargs):
        print("Before function call")
        result = func(*args, **kwargs)
        print("After function call")
        return result
    return wrapper

@my_decorator
def say_hello(name):
    print(f"Hello, {name}!")

say_hello("World")
```

## Practical Example: Timer

This decorator measures how long a function takes to execute:

```python
import time
import functools

def timer(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        print(f"{func.__name__} took {elapsed:.4f}s")
        return result
    return wrapper

@timer
def slow_add(a, b):
    time.sleep(0.5)
    return a + b
```

## Key Points

Decorators follow these principles:

- They accept a function as input and return a modified function
- The `@syntax` is just syntactic sugar for `func = decorator(func)`
- Use `functools.wraps` to preserve metadata
- They can be stacked: `@decorator1` then `@decorator2`
- Class-based decorators work too (implement `__call__`)

## Common Use Cases

| Use Case | Example |
|----------|---------|
| Logging | `@log_calls` |
| Caching | `@functools.lru_cache` |
| Auth | `@require_login` |
| Validation | `@validate_input` |
| Retry | `@retry(max_attempts=3)` |

### Nested Decorators

When you stack decorators, they apply **bottom-up**:

```bash
@decorator1
@decorator2
def my_function():
    pass

# Equivalent to:
# my_function = decorator1(decorator2(my_function))
```

## Further Reading

For more details see [PEP 318](https://peps.python.org/pep-0318/) and the `functools` module documentation.

> Decorators are not magic — they're just functions that return functions. Once you understand that, everything clicks.

That's it. Happy decorating!
"""


# ---------------------------------------------------------------------------
# Streamer
# ---------------------------------------------------------------------------

def stream(
    text: str,
    delay_ms: int = 30,
    jitter_ms: int = 15,
    chunk_min: int = 1,
    chunk_max: int = 4,
    show_tokens: bool = False,
) -> None:
    """Stream text token-by-token to stdout with realistic timing."""
    rng = random.Random(42)  # Deterministic for reproducibility
    pos = 0

    while pos < len(text):
        # Simulate variable token sizes (LLMs produce 1-4 char chunks)
        chunk_size = rng.randint(chunk_min, chunk_max)
        chunk = text[pos : pos + chunk_size]
        pos += chunk_size

        if show_tokens:
            # Write to stderr so it doesn't go into mdscream's stdin
            sys.stderr.write(f"[{chunk!r}]\n")

        sys.stdout.write(chunk)
        sys.stdout.flush()

        # Realistic delay with jitter
        sleep_s = (delay_ms + rng.randint(-jitter_ms, jitter_ms)) / 1000.0
        time.sleep(max(0.0, sleep_s))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Produce streaming markdown for mdscream testing"
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast mode: 10ms delay, 2-6 char chunks",
    )
    parser.add_argument(
        "--slow",
        action="store_true",
        help="Slow mode: 80ms delay, 1-2 char chunks (watch rendering closely)",
    )
    parser.add_argument(
        "--tokens",
        action="store_true",
        help="Show token boundaries on stderr",
    )
    parser.add_argument(
        "--doc",
        default="decorators",
        help="Document to stream (default: decorators)",
    )
    args = parser.parse_args()

    if args.fast:
        delay_ms, jitter_ms, chunk_min, chunk_max = 10, 5, 2, 6
    elif args.slow:
        delay_ms, jitter_ms, chunk_min, chunk_max = 80, 20, 1, 2
    else:
        delay_ms, jitter_ms, chunk_min, chunk_max = 30, 15, 1, 4

    stream(
        DOCUMENT,
        delay_ms=delay_ms,
        jitter_ms=jitter_ms,
        chunk_min=chunk_min,
        chunk_max=chunk_max,
        show_tokens=args.tokens,
    )


if __name__ == "__main__":
    main()
