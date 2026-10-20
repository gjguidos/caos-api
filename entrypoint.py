#!/usr/bin/env python
"""
Custom entrypoint for running the FastAPI application with Uvicorn.
"""

import uvicorn

from src.base.utils.env_utils import is_local_development

if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=is_local_development(),
        log_config=None,  # Use our own logging config
    )