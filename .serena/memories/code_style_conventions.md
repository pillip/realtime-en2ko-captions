# Code Style and Conventions

## Python Code Standards
- **Python Version**: 3.11+
- **Line Length**: 88 characters (Black standard)
- **Formatting**: Black auto-formatter
- **Linting**: Ruff with comprehensive rule set
- **Import Sorting**: isort integration via Ruff

## Ruff Configuration
**Selected Rules**:
- E: pycodestyle errors
- W: pycodestyle warnings  
- F: pyflakes
- I: isort (import sorting)
- B: flake8-bugbear
- C4: flake8-comprehensions
- UP: pyupgrade

**Ignored Rules**:
- E501: line too long (handled by Black)
- B008: function calls in argument defaults
- C901: too complex functions

## Code Quality Settings
- **Quote Style**: Double quotes
- **Indentation**: Spaces (4 spaces)
- **Line Endings**: Auto-detect
- **Target Version**: Python 3.11

## Pre-commit Hooks
The project uses pre-commit hooks for automatic code quality checks:
- Ruff linting and formatting
- Black formatting
- Import sorting
- Basic file checks

## Naming Conventions
- Functions: snake_case
- Classes: PascalCase
- Constants: UPPER_SNAKE_CASE
- Variables: snake_case
- Files: lowercase with underscores

## Documentation Style
- Inline comments in Korean for domain-specific logic
- Function/class docstrings in English
- README and technical docs in English
- User-facing messages in Korean