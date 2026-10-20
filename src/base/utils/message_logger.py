import json
import logging
import textwrap
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class MessageLogger:
    """Utility class to log chat messages to file."""

    def __init__(self, log_file_path: str = "logs/chat_messages.json"):
        self.log_file_path = Path(log_file_path)
        # Ensure the logs directory exists
        self.log_file_path.parent.mkdir(parents=True, exist_ok=True)

    def _serialize_content(self, content: str | list | None) -> str:
        if not isinstance(content, list):
            return content or ""
        parts = []
        image_count = 0
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "input_text":
                text = (block.get("text") or "").strip()
                if text:
                    parts.append(text)
            elif block_type == "input_image":
                image_count += 1
        if image_count:
            parts.append(f"[{image_count} attached image{'s' if image_count > 1 else ''}]")
        return " ".join(parts)

    def _format_content(self, content: str, width: int = 80) -> str:
        """
        Format message content for better readability.

        Args:
            content: The message content to format
            width: Maximum line width for wrapping

        Returns:
            Formatted content string
        """
        if not content:
            return content

        # Split by existing newlines first to preserve intentional line breaks
        lines = content.split("\n")
        formatted_lines = []

        for line in lines:
            if line.strip():  # Non-empty line
                # Wrap long lines
                wrapped = textwrap.fill(
                    line,
                    width=width,
                    subsequent_indent="  ",
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                formatted_lines.append(wrapped)
            else:  # Empty line
                formatted_lines.append("")

        return "\n".join(formatted_lines)

    def write_messages_to_file(self, messages: str | list[dict[str, str]], deployment: str = None):
        """
        Write chat messages to a JSON file with timestamp and metadata.

        Args:
            messages: List of message dictionaries with 'role' and 'content' keys
            deployment: Optional deployment name for context
        """
        try:
            if isinstance(messages, str):
                messages = [{"role": "user", "content": messages}]

            # Format messages for better readability
            formatted_messages = []
            for msg in messages:
                formatted_msg = {
                    "role": msg.get("role", "unknown"),
                    "content": self._format_content(self._serialize_content(msg.get("content"))),
                }
                formatted_messages.append(formatted_msg)

            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "deployment": deployment,
                "messages": formatted_messages,
            }

            # Read existing data if file exists
            existing_data = []
            if self.log_file_path.exists():
                try:
                    with open(self.log_file_path, encoding="utf-8") as f:
                        existing_data = json.load(f)
                except json.JSONDecodeError as e:
                    logger.warning(f"Could not read existing log file: {e}")
                    existing_data = []

            # Append new entry
            existing_data.append(log_entry)

            # Write back to file
            with open(self.log_file_path, "w", encoding="utf-8") as f:
                json.dump(existing_data, f, indent=2, ensure_ascii=False)

            logger.info(f"Successfully wrote {len(messages)} messages to {self.log_file_path}")

        except Exception as e:
            logger.error(f"Failed to write messages to file: {e}")

    def write_messages_to_text_file(
        self, messages: str | list[dict[str, str]], deployment: str = None
    ):
        """
        Write chat messages to a simple text file format with improved readability.

        Args:
            messages: List of message dictionaries with 'role' and 'content' keys
            deployment: Optional deployment name for context
        """
        try:
            # Use .txt extension for text format
            text_file_path = self.log_file_path.with_suffix(".txt")

            with open(text_file_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'═' * 80}\n")
                f.write("📝 CONVERSATION LOG\n")
                f.write(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                if deployment:
                    f.write(f"Deployment: {deployment}\n")
                f.write(f"{'═' * 80}\n\n")

                if isinstance(messages, str):
                    messages = [{"role": "user", "content": messages}]

                for i, msg in enumerate(messages, 1):
                    role = msg.get("role", "unknown").upper()
                    content = self._serialize_content(msg.get("content"))

                    # Role-specific formatting
                    if role == "USER":
                        f.write(f"👤 {role} (Message {i}):\n")
                        f.write(f"{'─' * 40}\n")
                    elif role == "ASSISTANT":
                        f.write(f"🤖 {role} (Message {i}):\n")
                        f.write(f"{'─' * 40}\n")
                    elif role == "SYSTEM":
                        f.write(f"⚙️  {role} (Message {i}):\n")
                        f.write(f"{'─' * 40}\n")
                    else:
                        f.write(f"❓ {role} (Message {i}):\n")
                        f.write(f"{'─' * 40}\n")

                    # Format and write content with proper indentation
                    formatted_content = self._format_content(content, width=76)
                    if formatted_content:
                        # Add slight indentation to content
                        indented_content = "\n".join(
                            f"  {line}" if line.strip() else ""
                            for line in formatted_content.split("\n")
                        )
                        f.write(f"{indented_content}\n")
                    else:
                        f.write("  [No content]\n")

                    f.write("\n")

                f.write(f"{'═' * 80}\n\n")

            logger.info(f"Successfully wrote {len(messages)} messages to {text_file_path}")

        except Exception as e:
            logger.error(f"Failed to write messages to text file: {e}")

    def log_response(self, response_content: str, deployment: str = None):
        """
        Log the AI response to both JSON and text files.

        Args:
            response_content: The response content from the AI
            deployment: Optional deployment name for context
        """
        try:
            response_message = {"role": "assistant", "content": response_content}

            # Log to JSON file
            self._append_response_to_json([response_message])

            # Log to text file
            self._append_response_to_text(response_message)

            logger.info("Successfully logged AI response to files")

        except Exception as e:
            logger.error(f"Failed to log response: {e}")

    def _append_response_to_json(self, response_messages: list[dict[str, str]]):
        """Append response to the latest conversation in JSON file."""
        try:
            if not self.log_file_path.exists():
                logger.warning("No existing conversation log found for response")
                return

            with open(self.log_file_path, encoding="utf-8") as f:
                existing_data = json.load(f)

            if existing_data:
                # Add response to the most recent conversation
                for msg in response_messages:
                    formatted_msg = {
                        "role": msg.get("role", "assistant"),
                        "content": self._format_content(msg.get("content", "")),
                    }
                    existing_data[-1]["messages"].append(formatted_msg)

                # Update timestamp to show when response was received
                existing_data[-1]["response_timestamp"] = datetime.now().isoformat()

                with open(self.log_file_path, "w", encoding="utf-8") as f:
                    json.dump(existing_data, f, indent=2, ensure_ascii=False)

        except Exception as e:
            logger.error(f"Failed to append response to JSON: {e}")

    def _append_response_to_text(self, response_message: dict[str, str]):
        """Append response to text file."""
        try:
            text_file_path = self.log_file_path.with_suffix(".txt")

            with open(text_file_path, "a", encoding="utf-8") as f:
                content = response_message.get("content", "")

                f.write("🤖 ASSISTANT RESPONSE:\n")
                f.write(f"{'─' * 40}\n")
                f.write(f"Response Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"{'─' * 40}\n")

                # Format and write content with proper indentation
                formatted_content = self._format_content(content, width=76)
                if formatted_content:
                    # Add slight indentation to content
                    indented_content = "\n".join(
                        f"  {line}" if line.strip() else ""
                        for line in formatted_content.split("\n")
                    )
                    f.write(f"{indented_content}\n")
                else:
                    f.write("  [No content]\n")

                f.write(f"\n{'═' * 80}\n\n")

        except Exception as e:
            logger.error(f"Failed to append response to text file: {e}")
