"""Entry point so ``python -m skill_library.export`` still runs the exporter CLI."""
import sys

from skill_library.export import main

sys.exit(main())
