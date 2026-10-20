"""
Script to run pyupgrade --py311-plus on all Python files in the src directory.
"""

import subprocess
import sys
from pathlib import Path


def run_pyupgrade_on_file(file_path: Path) -> tuple[bool, str]:
    """Run pyupgrade on a single file."""
    try:
        result = subprocess.run(
            ["pyupgrade", "--py311-plus", str(file_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0, result.stderr or result.stdout
    except FileNotFoundError:
        print("Error: pyupgrade is not installed.")
        print("Install it with: pip install pyupgrade")
        sys.exit(1)


def main():
    """Find and upgrade all Python files in src directory."""
    src_dir = Path("src")

    if not src_dir.exists():
        print(f"Error: {src_dir} directory not found")
        sys.exit(1)

    # Find all Python files
    py_files = list(src_dir.rglob("*.py"))

    if not py_files:
        print(f"No Python files found in {src_dir}")
        return

    print(f"Found {len(py_files)} Python file(s)")
    print(f"Running pyupgrade --py311-plus...\n")

    success_count = 0
    error_count = 0

    for py_file in py_files:
        print(f"Processing: {py_file}")
        success, output = run_pyupgrade_on_file(py_file)

        if success:
            success_count += 1
        else:
            error_count += 1
            if output:
                print(f"  Error: {output}")

    print(f"\n{'=' * 60}")
    print(f"Completed: {success_count} successful, {error_count} errors")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
