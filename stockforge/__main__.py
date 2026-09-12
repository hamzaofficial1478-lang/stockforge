"""Makes `python -m stockforge` work.

Without this the package is not runnable and the error says so in the least
helpful way available — "'stockforge' is a package and cannot be directly
executed" — which tells you nothing about what to type instead. The `stockforge`
command only exists after `pip install`, so on a plain checkout `python -m` is
the obvious thing to reach for and it has to work.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
