"""`python -m chapterize` — GUI by default, CLI if args are given."""
import sys


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] not in ("gui", "--gui"):
        from .cli import main as cli_main
        return cli_main(argv)
    if argv and argv[0] in ("gui", "--gui"):
        argv = argv[1:]
    from .gui import main as gui_main
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
