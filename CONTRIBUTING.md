# Contributing to Nexus-Scraper MCP

We welcome contributions from the community! To maintain a clean and reliable codebase, please adhere to the following guidelines:

## 📐 Development Guidelines

1. **Micro-Modularization (<300 Lines Rule)**:
   Every Python module must strictly remain under **300 lines of code** with a single, clearly defined responsibility.
2. **Type Safety & Documentation**:
   All functions and classes must include Python 3.10+ Type Hints and clear docstrings.
3. **Zero Token Runtime Guarantee**:
   The runtime execution path (`engine_fast`) must never incur external LLM token overhead on cache hits.
4. **Testing**:
   Ensure all changes pass both `python test_workflow.py` and `python test_client.py`.

## 🚀 Pull Request Process

1. Fork the repo and create your feature branch: `git checkout -b feature/my-feature`.
2. Commit your changes with clear semantic messages.
3. Verify that CI tests pass across all environments.
4. Open a Pull Request with a concise description of the motivation and changes.
