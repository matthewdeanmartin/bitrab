"""Metadata for bitrab."""

__all__ = [
    "__credits__",
    "__dependencies__",
    "__description__",
    "__keywords__",
    "__license__",
    "__readme__",
    "__requires_python__",
    "__status__",
    "__title__",
    "__version__",
]

__title__ = "bitrab"
__version__ = "0.5.0"
__description__ = "Run GitLab CI pipelines locally"
__readme__ = "README.md"
__credits__ = [{"name": "Matthew Martin", "email": "matthewdeanmartin@gmail.com"}]
__keywords__ = ["bash", "gitlab"]
__license__ = "MIT"
__requires_python__ = ">=3.10"
__dependencies__ = [
    "ruamel.yaml>=0.19.1",
    "jsonschema>=4.26.0",
    "importlib_resources>=7.1.0",
    "urllib3>=2.6.0",
    "certifi>=2026.4.22",
    "argcomplete>=3.6.3",
    "textual>=8.2.4",
    "colorlog>=6.10.1",
    "rich>=15.0.0",
    "watchdog>=3.0.0",
    "packaging>=26.2",
    "toml>=0.10.2",
    "tomlkit>=0.14.0",
    "tomli; python_version < '3.11'",
]
__status__ = "4 - Beta"
