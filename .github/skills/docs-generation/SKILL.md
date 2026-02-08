---
name: docs-generation
description: Generate documentation from Python code by analyzing source code, extracting signatures, and creating structured reference docs. Use when creating API references or module documentation.
---

# Documentation Generation

Generate documentation for Python codebases by analyzing source code, extracting signatures, and creating structured reference docs.

## Quick Commands

```bash
# Generate Markdown docs with pdoc
uv run pdoc src/mypackage -o docs/api --format markdown

# Generate HTML docs
uv run pdoc --html src/mypackage -o docs/api

# Live preview server
uv run pdoc src/mypackage --http localhost:8080

# Check docstring coverage
uv run interrogate -vv src/
```

## Documentation Generation Tools

### pdoc (Recommended)

Generates documentation directly from Python source code:

```bash
# Install
uv add pdoc --dev

# Generate Markdown (best for version control)
uv run pdoc src/mypackage -o docs/api --format markdown

# Generate HTML
uv run pdoc --html src/mypackage -o docs/api

# Live preview while editing
uv run pdoc src/mypackage --http localhost:8080
```

### mkdocs + mkdocstrings

Full documentation site with auto-generated API reference:

```bash
# Install
uv add mkdocs mkdocs-material mkdocstrings[python] --dev

# In docs/api/reference.md:
# ::: mypackage.module

# Serve locally
uv run mkdocs serve

# Build static site
uv run mkdocs build
```

## Workflow

1. **Analyze** - Read source code to identify public modules, classes, functions
2. **Extract** - Pull signatures, type hints, docstrings from code
3. **Structure** - Organize into logical sections (modules --> classes --> functions)
4. **Generate** - Create Markdown files following API reference template
5. **Enrich** - Add examples extracted from test files
6. **Output** - Save to `docs/api/`

## API Reference Template

For each module, generate this structure:

````markdown
# mypackage.processor

Brief description from module docstring.

## Classes

### `DataProcessor`

```python
class DataProcessor(batch_size: int = 50)
```
````

Description from class docstring.

**Parameters:**

- `batch_size` (int): Description. Defaults to 50.

**Attributes:**

- `config` (ProcessorConfig): Description.
- `stats` (ProcessorStats): Description.

**Methods:**

#### `process(data: list[dict]) -> ProcessResult`

Description from method docstring.

**Parameters:**

- `data` (list[dict]): Description.

**Returns:**

- `ProcessResult`: Description.

**Example:**

```python
processor = DataProcessor()
result = processor.process([{"id": "1", "value": 100}])
```

## Functions

### `validate_record(record: dict) -> bool`

Description from function docstring.

**Parameters:**

- `record` (dict): Description.

**Returns:**

- `bool`: Description.

````

## Extracting Examples from Tests

Tests contain real usage patterns. Extract them for documentation:

```python
# From tests/test_processor.py
def test_process_valid_data():
    processor = DataProcessor()
    result = processor.process([
        {"id": "1", "value": 100},
        {"id": "2", "value": 200},
    ])
    assert result.total == 300
````

Becomes:

````markdown
**Example:**

```python
from mypackage import DataProcessor

processor = DataProcessor()
result = processor.process([
    {"id": "1", "value": 100},
    {"id": "2", "value": 200},
])
print(result.total)  # 300
```
````

````

## Automation

Add to `pyproject.toml`:

```toml
[tool.poe.tasks]
docs = "pdoc src/mypackage -o docs/api --format markdown"
docs-serve = "pdoc src/mypackage --http localhost:8080"
docs-check = "interrogate -vv src/"
````

Run with:

```bash
uv run poe docs        # Generate API docs
uv run poe docs-serve  # Live preview
uv run poe docs-check  # Check coverage
```

## Output Structure

```
docs/
└── api/
    ├── index.md           # Package overview
    ├── module1.md         # Module reference
    ├── module2.md         # Module reference
    └── subpackage/
        └── module3.md     # Nested module
```
