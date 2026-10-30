"""Development entry point, inside the API container:

python -m src.domains.agentverse.nataraja inspect [--json]
python -m src.domains.agentverse.nataraja verify --redis-url redis://<disposable>:6379/0
python -m src.domains.agentverse.nataraja reconcile --dry-run [--json]
"""

import sys

from src.domains.agentverse.nataraja.cli import main

sys.exit(main())
