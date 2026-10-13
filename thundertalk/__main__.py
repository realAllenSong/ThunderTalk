"""python -m thundertalk  — main entry point."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from thundertalk import cli         # no Qt: never opens a window
        sys.exit(cli.main(sys.argv[2:]))
    from thundertalk.app import main
    main()
