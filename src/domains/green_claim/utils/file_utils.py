"""
File handling utilities for green claim analysis.
"""

import base64
import logging

from fastapi import HTTPException, UploadFile

logger = logging.getLogger(__name__)

# Supported file types
SUPPORTED_IMAGE_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}

SUPPORTED_DOCUMENT_TYPES = {
    "application/pdf",
}

SUPPORTED_MIME_TYPES = SUPPORTED_IMAGE_TYPES | SUPPORTED_DOCUMENT_TYPES

# Default max file size: 50 MB
DEFAULT_MAX_FILE_SIZE = 25 * 1024 * 1024


async def convert_upload_file_to_data_uri(
    file: UploadFile,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
) -> tuple[str, str]:
    """
    Convert an UploadFile to a base64-encoded data URI.

    Args:
        file: The uploaded file
        max_file_size: Maximum allowed file size in bytes

    Returns:
        Tuple of (data_uri, mime_type)

    Raises:
        HTTPException: If file validation fails
    """
    # Validate MIME type
    mime_type = file.content_type
    if mime_type not in SUPPORTED_MIME_TYPES:
        logger.warning(f"Unsupported file type: {mime_type}")
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {mime_type}. "
            f"Supported types: {', '.join(sorted(SUPPORTED_MIME_TYPES))}",
        )

    # Read file content
    try:
        file_content = await file.read()
    except Exception as err:
        logger.error(f"Failed to read uploaded file: {err}", exc_info=True)
        raise HTTPException(
            status_code=400,
            detail=f"Failed to read uploaded file: {str(err)}",
        )

    # Validate file size
    file_size = len(file_content)
    if file_size > max_file_size:
        logger.warning(f"File too large: {file_size} bytes (max: {max_file_size} bytes)")
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {file_size / (1024 * 1024):.2f} MB. "
            f"Maximum allowed size: {max_file_size / (1024 * 1024):.0f} MB",
        )

    if file_size == 0:
        logger.warning("Empty file uploaded")
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty",
        )

    # Encode to base64
    try:
        base64_encoded = base64.b64encode(file_content).decode("utf-8")
    except Exception as err:
        logger.error(f"Failed to encode file to base64: {err}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to encode file: {str(err)}",
        )

    # Construct data URI
    data_uri = f"data:{mime_type};base64,{base64_encoded}"

    logger.info(
        f"Converted file to data URI: {mime_type}, "
        f"{file_size / 1024:.2f} KB, "
        f"{len(data_uri)} characters"
    )

    return data_uri, mime_type
