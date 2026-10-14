#!/usr/bin/env python3
"""Quick launcher — python run.py  (python run.py --cli … for the headless CLI)"""
import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from thundertalk import cli
        sys.exit(cli.main(sys.argv[2:]))
    from thundertalk.app import main
    main()
