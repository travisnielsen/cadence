---
applyTo: "**/*.py"
---

- Run `./devsetup.sh` for first-time environment setup (installs Python, creates venv, installs dependencies).
- Use `uv run` as the main entrypoint for running Python commands with all packages available.
- Use `uv run poe <task>` for development tasks like formatting (`fmt`), linting (`lint`), type checking (`typecheck`), code quality check (`quality`),  and testing (`test`).
- Read [DEV_SETUP.md](../../DEV_SETUP.md) for detailed development environment setup and available poe tasks.
- Read [CODING_STANDARD.md](../../CODING_STANDARD.md) for the project's coding standards and best practices.
- When verifying logic with unit tests, run only the related tests, not the entire test suite.
- For new tests and samples, review existing ones to understand the coding style and reuse it.
- When generating new functions, always specify the function return type and parameter types.
- Do not use `Optional`; use `Type | None` instead.
- Use Google-style docstrings for all public functions and classes.
- Before running any commands to execute or test the code, ensure all problems and errors are resolved.
- When formatting files, format only the files you changed; do not format the entire codebase.
- Do not mark new tests with `@pytest.mark.asyncio` (asyncio_mode is auto).
- If you need debug information, use print statements as needed and remove them when done.
- Avoid adding excessive comments.
- When working with samples, update the associated README files with the latest information.
- This codebase is **async-first**: use `async def` for I/O, never block the event loop.
- Use `asyncio.sleep()` not `time.sleep()`, `aiohttp` not `requests`.
- Run blocking/CPU-bound code in executor: `await loop.run_in_executor(None, fn, args)`.
- Always `await` created tasks or use `asyncio.gather()` - don't fire-and-forget.
- Use `AsyncMock` for mocking async functions in tests.

Sample structure:

1. Copyright header: `# Copyright (c) Microsoft. All rights reserved.`
2. Required imports
3. Module docstring: `"""This sample demonstrates..."""`
4. Helper functions
5. Main function(s) demonstrating functionality
6. Entry point: `if __name__ == "__main__": asyncio.run(main())`
