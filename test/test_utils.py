
from bitrab.utils.terminal_colors import Colors


def test_colors_enable_disable():
    # Store original state
    original_header = Colors.HEADER

    try:
        Colors.disable()
        assert Colors.HEADER == ""
        assert Colors.OKBLUE == ""
        assert Colors.FAIL == ""

        Colors.enable()
        assert Colors.HEADER == "\033[95m"
        assert Colors.OKBLUE == "\033[94m"
        assert Colors.FAIL == "\033[91m"
    finally:
        # Restore if needed, but enable() should have done it
        if original_header == "":
            Colors.disable()
        else:
            Colors.enable()


def test_colors_attributes():
    assert hasattr(Colors, "HEADER")
    assert hasattr(Colors, "OKGREEN")
    assert hasattr(Colors, "BOLD")
    assert hasattr(Colors, "ENDC")
