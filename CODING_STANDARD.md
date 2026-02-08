# Coding Standards

This document describes the coding standards and conventions for the Cadence project.

## Anti-Slop: Preventing Low-Quality AI-Generated Code

This project enforces strict coding standards to prevent common "AI slop" - anti-patterns that AI code assistants tend to produce. The following rules are **enforced automatically** via ruff, basedpyright, and **sloppylint**.

### Tooling

| Tool             | Purpose              | Command                |
| ---------------- | -------------------- | ---------------------- |
| **ruff**         | Linting + formatting | `uv run poe lint`      |
| **basedpyright** | Type checking        | `uv run poe typecheck` |
| **sloppylint**   | AI slop detection    | `uv run poe slop`      |
| **gitleaks**     | Secret scanning      | Prek hook              |

**Run all checks:** `uv run poe check`

### Sloppylint Catches

Sloppylint specifically targets AI-generated code anti-patterns:

| Pattern                | Example                                    | Why It's Bad                 |
| ---------------------- | ------------------------------------------ | ---------------------------- |
| Hallucinated imports   | `from utils import helper` (doesn't exist) | Runtime ImportError          |
| Cross-language leakage | `.push()`, `.length`, `.equals()`          | JS/Java patterns in Python   |
| Placeholder code       | `def process(): pass`                      | Does nothing, fails silently |
| Mutable defaults       | `def fn(items=[])`                         | Shared state bug             |
| Bare except            | `except:`                                  | Catches Ctrl+C, SystemExit   |
| Hedging comments       | `# should work hopefully`                  | AI uncertainty               |

### 1. Pydantic Models for All I/O

Never work with raw dictionaries, JSON, or untyped data. Always define a Pydantic model and validate **immediately** at the boundary:

```python
# ❌ BAD: Raw dict from API/file (AI slop)
async def process_user_data(data: dict) -> None:
    name = data["name"]  # Could fail, no validation
    age = data.get("age", 0)  # Type is Any

# ✅ GOOD: Pydantic model + immediate validation
from pydantic import BaseModel, Field

class UserData(BaseModel):
    name: str = Field(min_length=1)
    age: int = Field(ge=0, le=150)

async def process_user_data(data: dict) -> None:
    user = UserData.model_validate(data)  # Fails fast with clear errors
    # Now user.name and user.age are fully typed and validated
```

This applies to:

- **API responses**: `response.json()` → immediate `Model.model_validate()`
- **Config files**: `json.load()` → immediate `Model.model_validate()`
- **CLI arguments**: Convert to Pydantic model
- **Environment variables**: Use `pydantic-settings` instead of raw `os.getenv()`

### 2. No Boolean Traps (FBT rule)

The `FBT` ruff rule catches ambiguous boolean parameters:

```python
# ❌ BAD: What does True mean here?
send_email(user, True, False)

# ✅ GOOD: Use enums or keyword-only args
from enum import Enum

class EmailFormat(Enum):
    HTML = "html"
    PLAIN = "plain"

async def send_email(
    user: User,
    *,  # Force keyword-only
    format: EmailFormat,
) -> None: ...

await send_email(user, format=EmailFormat.HTML)
```

### 3. No Bare Excepts (BLE rule)

```python
# ❌ BAD: Hides all errors
try:
    await risky_operation()
except Exception:  # BLE001 error
    pass

# ✅ GOOD: Catch specific exceptions
try:
    await risky_operation()
except (ValueError, KeyError) as e:
    logger.error(f"Expected error: {e}")
    raise
```

### 4. Keep Functions Simple (C90/PLR rules)

This project enforces:

- **Max complexity: 10** (McCabe)
- **Max nested blocks: 3**
- **Max arguments: 7**

```python
# ❌ BAD: Complex nested logic
async def process_order(order):
    if order.valid:
        if order.in_stock:
            if order.payment_ok:
                if order.address_valid:
                    # 4 levels deep!
                    ...

# ✅ GOOD: Early returns + helper functions
async def process_order(order: Order) -> None:
    if not order.valid:
        raise InvalidOrderError()
    if not order.in_stock:
        raise OutOfStockError()

    await validate_payment(order)
    await validate_address(order)
    await ship_order(order)
```

### 5. No Commented-Out Code (ERA rule)

Remove dead code instead of commenting it out. Use version control for history.

### 6. Security Checks (S rules)

The `S` (bandit) rules catch common security issues:

```python
# ❌ BAD: Hardcoded secrets
API_KEY = "sk-1234567890"  # S105: Hardcoded password

# ❌ BAD: SQL injection risk
query = f"SELECT * FROM users WHERE id = {user_id}"  # S608

# ✅ GOOD: Use environment variables and parameterized queries
API_KEY = os.environ["API_KEY"]
query = "SELECT * FROM users WHERE id = ?"
cursor.execute(query, (user_id,))
```

### Quality Commands

```bash
uv run poe quality    # Full pipeline (format + lint + typecheck + metrics)
uv run poe metrics    # Complexity + dead code
uv run poe typecheck  # Type checking only
```

---

## Code Style and Formatting

We use [ruff](https://github.com/astral-sh/ruff) for linting and formatting, and [basedpyright](https://github.com/DetachHead/basedpyright) for type checking:

- **Line length**: 100 characters
- **Target Python version**: 3.11+
- **Type checking**: basedpyright in standard mode
- **Google-style docstrings**: All public functions, classes, and modules

## Asynchronous Programming

This codebase is **async-first**. Assume everything is asynchronous unless explicitly synchronous.

1. **Use `async def` for I/O-bound operations** - network calls, file I/O, database queries
2. **Never block the event loop** - avoid `time.sleep()`, use `asyncio.sleep()`
3. **Use async-compatible libraries** - `aiohttp` not `requests`, `asyncpg` not `psycopg2`

| Sync Library | Async Alternative  |
| ------------ | ------------------ |
| `requests`   | `aiohttp`, `httpx` |
| `psycopg2`   | `asyncpg`          |
| `redis`      | `aioredis`         |
| `open()`     | `aiofiles`         |
| `time.sleep` | `asyncio.sleep`    |

## Import Pattern

With `src/` on the Python path, all imports use the package directly:

```python
from models import QueryTemplate
from entities.shared.search_client import AzureSearchClient
from api.step_events import emit_step_start
```

## Documentation

We follow the [Google Docstring](https://github.com/google/styleguide/blob/gh-pages/pyguide.md#383-functions-and-methods) style guide:

```python
async def create_agent(name: str, client: ClientProtocol) -> Agent:
    """Create a new agent with the specified configuration.

    Args:
        name: The name of the agent.
        client: The client to use for communication.

    Returns:
        A configured agent instance.

    Raises:
        ValueError: If the name is empty.
    """
    ...
```

## Performance Considerations

- **Cache expensive computations**: Don't recalculate on every call
- **Prefer attribute access over isinstance()**: Faster in hot paths
- **Avoid redundant serialization**: Compute once, reuse

## See Also

- [DEV_SETUP.md](DEV_SETUP.md) - Development environment setup
- [CONTRIBUTING.md](CONTRIBUTING.md) - Git conventions, PR guidelines
