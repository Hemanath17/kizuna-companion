"""Entry point for the `kizuna` CLI PLAN.md section 11 describes
(`kizuna eval --suite safety`). Without a packaged console-script, run it as:

    python kizuna.py eval --suite safety
"""

import sys

from eval.cli import main

if __name__ == "__main__":
    sys.exit(main())
