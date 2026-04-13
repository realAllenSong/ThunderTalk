# Contributing to ThunderTalk

Thank you for your interest in contributing to ThunderTalk! This guide will help you get started.

## Development Setup

1. Ensure you have [uv](https://docs.astral.sh/uv/) and Python >= 3.12 installed.
2. Fork and clone the repository.
3. Install dependencies:

```bash
uv sync
```

4. Run the application:

```bash
uv run python run.py
```

## Project Structure

```
thundertalk/      Python package
  app.py          Application entry point
  core/           Audio, ASR, hotkey, text output, model management
  ui/             PySide6 windows and widgets
docs/             Documentation
```

## Code Style

- **Python**: Format with `ruff format`, lint with `ruff check`.
- **Commits**: Use clear, descriptive commit messages.

## Pull Request Process

1. Create a feature branch from `main`.
2. Make your changes.
3. Ensure `uv run ruff check thundertalk/` and `uv run ruff format --check thundertalk/` pass.
4. Submit a pull request with a clear description of the changes.

## Reporting Issues

- Use the GitHub issue templates for bug reports and feature requests.
- Include your OS, Python version, and steps to reproduce for bugs.

## License

By contributing, you agree that your contributions will be licensed under the Apache-2.0 License.
