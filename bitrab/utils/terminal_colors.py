import os


class Colors:
    """Simple ANSI color codes for terminal output."""

    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"

    UNDERLINE = "\033[4m"
    RED_BG = "\033[41m"
    GREEN_BG = "\033[42m"

    @classmethod
    def disable(cls):
        """Disable all color output."""
        for attr in dir(cls):
            if isinstance(getattr(cls, attr), str) and getattr(cls, attr).startswith("\033"):
                setattr(cls, attr, "")

    @classmethod
    def enable(cls):
        """Enable all color output."""
        cls.HEADER = "\033[95m"
        cls.OKBLUE = "\033[94m"
        cls.OKCYAN = "\033[96m"
        cls.OKGREEN = "\033[92m"
        cls.WARNING = "\033[93m"
        cls.FAIL = "\033[91m"
        cls.ENDC = "\033[0m"
        cls.BOLD = "\033[1m"

        cls.UNDERLINE = "\033[4m"
        cls.RED_BG = "\033[41m"
        cls.GREEN_BG = "\033[42m"


if os.environ.get("NO_COLOR") or not os.isatty(1):
    Colors.disable()
