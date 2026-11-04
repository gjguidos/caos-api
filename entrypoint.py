#!/usr/bin/env python
"""
Custom entrypoint for running the FastAPI application with Uvicorn.

Hot reload is controlled only by ``CAOS_API_RELOAD`` (default: disabled); see
``src/base/system/serve.py``.
"""

from src.base.system.serve import main

if __name__ == "__main__":
    main()
