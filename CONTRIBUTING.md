# Contributing to sgx

Thanks for your interest in contributing to sgx!

## Development Setup

1. Clone the repo
2. Install dependencies with `uv sync`
3. Run tests: `uv run pytest`
4. Lint & format: `uv run ruff check . && uv run ruff format`

## Code Style

- All Python source files must start with two `ABOUTME:` comment lines.
- We follow strict TDD: write tests before implementation.
- Keep changes minimal and maintainable.
- Never use `--no-verify` when committing.

## Pull Requests

- Open an issue first for non-trivial changes.
- Ensure all tests pass and `ruff` + `mypy` are clean.
- Update documentation (especially README and command help) when behavior changes.

## Reporting Issues

Please include:
- `sgx --version`
- Output of `sgx auth status`
- Steps to reproduce
- Expected vs actual behavior

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
