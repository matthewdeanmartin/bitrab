# bitrab
A pipeline runner that runs Gitlab syntax pipelines without docker or admin rights. Minimal feature set.

## Why?

[gitlab-runner](https://docs.gitlab.com/runner/install/) exists, you can run it on many operating systems, but requires admin rights and docker.

[gitlab-ci-local](https://github.com/firecow/gitlab-ci-local) exists, installs with npm, as far as I can tell it also requires docker.

Bitrab's design goals:

- Let you run .gitlab-ci.yml like was a Makefile on any workstation or build runner, including Github, etc.
- no admin rights, no docker dependency
- implements only the easy to implement features of Gitlab pipelines

## Installation

```bash
pipx install bitrab
```

## Usage

Run from folder where your `.gitlab-ci.yml` file is.
```bash
bitrab
```