"""PyInstaller entry point, including Explorer drag and drop arguments."""
if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    from converter.cli import main
    raise SystemExit(main())
