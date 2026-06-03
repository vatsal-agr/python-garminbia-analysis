"""Allow running as: python -m garmin_bia_sync"""

import sys

from garmin_bia_sync.sync import main

if __name__ == "__main__":
    sys.exit(main())
