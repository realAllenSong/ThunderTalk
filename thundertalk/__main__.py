"""python -m thundertalk  — main entry point."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from thundertalk.app import main
    main()
