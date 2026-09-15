"""Allow `python -m pymergetic.rxf` to invoke the CLI."""

import sys

from pymergetic.rxf.cli import main

if __name__ == "__main__":
    sys.exit(main())
