"""
Custom JSON encoder for handling MongoDB ObjectId and other
non-serializable types.
"""

import json
from datetime import datetime
from typing import Any

from bson import ObjectId


class CustomJSONEncoder(json.JSONEncoder):
    """
    Custom JSON encoder that handles MongoDB ObjectId, datetime,
    and other types.
    """

    def default(self, obj: Any) -> Any:
        if isinstance(obj, ObjectId):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)
