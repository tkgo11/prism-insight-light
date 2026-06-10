```markdown
# prism-insight-light Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill teaches the core development patterns and conventions used in the `prism-insight-light` Python repository. It covers file organization, import/export styles, commit message habits, and testing patterns, providing practical examples and command suggestions for efficient contribution and maintenance.

## Coding Conventions

### File Naming
- Use **snake_case** for all file names.
  - Example:  
    ```plaintext
    data_loader.py
    utils/helpers.py
    ```

### Import Style
- Use **relative imports** within the package.
  - Example:
    ```python
    from .utils import data_parser
    from ..core import engine
    ```

### Export Style
- Use **named exports** (i.e., explicitly define what is exported from modules).
  - Example:
    ```python
    __all__ = ['DataLoader', 'parse_data']
    ```

### Commit Messages
- Freeform style, no strict prefixing.
- Average commit message length: ~41 characters.
  - Example:
    ```
    Fix bug in data parsing for edge cases
    ```

## Workflows

### Adding a New Module
**Trigger:** When you need to introduce new functionality.
**Command:** `/add-module`

1. Create a new file using snake_case (e.g., `my_feature.py`).
2. Implement your module, using relative imports for dependencies.
3. Define `__all__` for named exports.
4. Write corresponding test in a file matching `*.test.*` (e.g., `my_feature.test.py`).
5. Commit with a clear, concise message.

### Writing Tests
**Trigger:** When adding or updating code that requires validation.
**Command:** `/write-test`

1. Create a test file with the pattern `*.test.*` (e.g., `utils.test.py`).
2. Implement test cases for your functions/classes.
3. Use the unknown (custom or standard) testing framework as appropriate.
4. Run tests to ensure correctness.

### Refactoring Imports
**Trigger:** When reorganizing or cleaning up code dependencies.
**Command:** `/refactor-imports`

1. Replace absolute imports with relative imports within the package.
2. Ensure all import paths are correct and modules are discoverable.
3. Run tests to verify nothing is broken.

## Testing Patterns

- Test files follow the pattern `*.test.*` (e.g., `module.test.py`).
- The specific testing framework is not detected; use standard Python testing practices (e.g., `unittest`, `pytest`, or custom).
- Place tests alongside or near the modules they test.
- Example test file:
  ```python
  # utils.test.py
  import unittest
  from .utils import parse_data

  class TestParseData(unittest.TestCase):
      def test_basic(self):
          self.assertEqual(parse_data("1,2,3"), [1, 2, 3])
  ```

## Commands
| Command           | Purpose                                         |
|-------------------|------------------------------------------------|
| /add-module       | Scaffold and add a new module with tests       |
| /write-test       | Create or update test files for your code      |
| /refactor-imports | Refactor code to use relative imports          |
```
