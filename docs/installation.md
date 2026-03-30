# Installation

## Requirements

- Python 3.9 or newer
- A shell (`bash` on Linux/macOS; `bash` or WSL on Windows)

No Docker. No admin rights. No GitLab account.

## Install with pipx (recommended)

[pipx](https://pipx.pypa.io/) installs command-line tools in isolated environments so they don't interfere with your project dependencies.

```bash
pipx install bitrab
```

For native speedups on systems where extra compiled dependencies are acceptable:

```bash
pipx install 'bitrab[fast]'
```

Verify:

```bash
bitrab --version
```

## Install with pip

```bash
pip install bitrab
```

Optional extras:

```bash
pip install 'bitrab[fast]'
pip install 'bitrab[all]'
```

Or into a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install bitrab
```

## Install from source

```bash
git clone https://github.com/matthewdeanmartin/bitrab.git
cd bitrab
pip install -e .
```

## Windows notes

Bitrab runs jobs using `bash`. On Windows you need one of:

- [Git for Windows](https://git-scm.com/download/win) (ships `bash.exe`)
- WSL

Scripts in your `.gitlab-ci.yml` are passed to `bash -c`, so standard Unix shell syntax works as long as `bash` is on your PATH.
