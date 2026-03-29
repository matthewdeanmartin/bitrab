# Bitrab 🐰

Bitrab runs GitLab-style pipelines on a plain machine, using your existing shell and tools instead of spinning up a
GitLab-managed container runner.

The value proposition is speed:

- test CI changes before pushing
- reuse `.gitlab-ci.yml` outside GitLab
- cut "push, wait, repeat" cycle time
- reduce CI startup overhead by running several jobs in parallel inside one host or container

## What bitrab does well

Bitrab is strongest when your pipeline is mostly shell commands and your goal is fast feedback. It can:

- load `.gitlab-ci.yml` or `.bitrab-ci.yml`
- merge includes
- validate against GitLab's schema plus local capability checks
- execute stage-based or DAG-based pipelines
- expand `parallel:` and `parallel: matrix:`
- collect and inject local artifacts
- re-run on config file changes
- warn when a supposedly read-only job mutates the tree

## What bitrab does not try to fake

Bitrab is intentionally honest about boundaries:

- no container isolation
- no `image:` pulling
- no `services:` sidecars
- no GitLab deployment, Pages, release, or secret-management APIs
- some GitLab config keys are ignored or only partly meaningful locally

If you need full runner semantics, use GitLab Runner. If you want faster local feedback from the same pipeline file,
bitrab is the better fit.[^loader][^runner][^capabilities]

## Start here

- [Quick start](quickstart.md)
- [Installation](installation.md)
- [Running pipelines](running.md)
- [CLI reference](cli.md)
- [Key concepts](concepts.md)
- [Local vs GitLab differences](differences.md)

[^loader]:
Source: [bitrab/config/loader.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/config/loader.py)
[^runner]: Source: [bitrab/plan.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/plan.py)
[^capabilities]:
Source: [bitrab/config/capabilities.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/config/capabilities.py)
