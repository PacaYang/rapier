"""Launch the workflow builder: ``python -m rapier.workflow_builder``."""

import argparse

from .server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Rapier visual workflow builder")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: localhost only)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-demo", action="store_true", help="Do not register the mock demo methods")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    app = create_app(demo=not args.no_demo)
    print(f"Rapier workflow builder on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
