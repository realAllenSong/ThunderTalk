#!/usr/bin/env python3
"""Quick launcher — python run.py  (python run.py --cli … for the headless CLI)"""
import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from thundertalk import cli
        sys.exit(cli.main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--mcp":
        from thundertalk.mcp import server
        server.main()
        sys.exit(0)
    from thundertalk.app import main
    main()
