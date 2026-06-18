"""Unified launcher — pick which transport to start.

    python -m multiagent            # terminal console (default)
    python -m multiagent terminal   # same thing, explicit
    python -m multiagent discord    # bring the Discord bot online

Run from the folder ABOVE this package (the one containing multiagent/).
"""
import sys


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    mode = argv[0].lower() if argv else "terminal"

    if mode in ("terminal", "console"):
        from .main import main as run_terminal
        run_terminal()
    elif mode == "discord":
        from .transports.discord_bot import client, DISCORD_TOKEN
        if not DISCORD_TOKEN:
            raise SystemExit("Set DISCORD_TOKEN in your .env first.")
        client.run(DISCORD_TOKEN)
    elif mode in ("-h", "--help", "help"):
        print(__doc__)
    else:
        raise SystemExit(
            f"unknown mode: {mode!r}\n"
            "usage: python -m multiagent [terminal|discord]"
        )


if __name__ == "__main__":
    main()
