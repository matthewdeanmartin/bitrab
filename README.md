# Bitrab 🐰

**Local GitLab Compatible CI Runner** - Execute GitLab(TM) CI pipelines locally without Docker

Bitrab is a lightweight, Python-based tool that allows you to run GitLab CI pipelines on your local machine. Perfect for
testing CI configurations, debugging pipeline issues, and rapid development iteration.

Doesn't even require GitLab, works on any build host with python, e.g. GitHub, AWS CodeBuild, anything

GITLAB is a trademark of GitLab Inc. in the United States and other countries and regions. This tool is not affiliated,
endorsed, sponsored, or approved with or by GitLab Inc.

## ✨ Features

- 🚀 **Native execution** - Run pipelines directly on your system (no Docker required)
- 🔄 **Parallel job execution** - Run jobs within stages in parallel for faster execution
- 🎯 **Selective job running** - Execute specific jobs or stages
- 🔍 **Pipeline validation** - Validate your `.gitlab-ci.yml` configuration
- 📊 **Job listing** - View all jobs organized by stages
- 🧪 **Dry-run mode** - Preview what would be executed without running
- ♻️ **Retry logic** - Full GitLab-compatible retry support with backoff strategies
- 🌍 **Variable substitution** - Complete GitLab CI variable support (aspirational feature ATM)
- 📋 **Include directives** - Process `include:` statements like GitLab
- 🎨 **Colored output** - Beautiful terminal output with emoji indicators

## 🚀 Quick Start

### Installation

```bash
pipx install bitrab
```

### Basic Usage

```bash
# Run your .gitlab-ci.yml
bitrab run

# List all jobs in the pipeline
bitrab list

# Validate configuration
bitrab validate

# Run with dry-run to see what would execute
bitrab run --dry-run

# Run with specific parallelism
bitrab run --parallel 4
```

## 📖 Usage

### Commands

#### `bitrab run` (default)

Execute the GitLab CI pipeline locally.

```bash
bitrab run                          # Run .gitlab-ci.yml
bitrab run -c custom-ci.yml         # Use custom config file
bitrab run --dry-run                # Preview execution
bitrab run --parallel 2             # Use 2 parallel workers per stage
bitrab run --jobs build test        # Run specific jobs (planned)
```

**Options:**

- `--dry-run` - Show commands without executing them
- `--parallel, -j N` - Number of parallel jobs per stage
- `--jobs JOB...` - Run only specified jobs (coming soon)

#### `bitrab list`

Display all jobs organized by stages.

```bash
bitrab list                         # Show all jobs
bitrab list -c custom-ci.yml        # List jobs from custom config
```

#### `bitrab validate`

Validate pipeline configuration and check for common issues.

```bash
bitrab validate                     # Basic validation
bitrab validate --json              # Output validated config as JSON
```

#### `bitrab lint`

Server-side validation using GitLab's official linter (planned).

```bash
bitrab lint                         # Validate against GitLab API
```

### Global Options

- `-c, --config PATH` - Path to GitLab CI config file (default: `.gitlab-ci.yml`)
- `-q, --quiet` - Suppress non-error output
- `-v, --verbose` - Enable verbose logging
- `--version` - Show version information
- `--license` - Display license information

## 📝 Configuration

Bitrab supports standard GitLab CI YAML configuration:

```yaml
# .gitlab-ci.yml
stages:
  - build
  - test
  - deploy

variables:
  NODE_VERSION: "18"

default:
  before_script:
    - echo "Setting up environment..."

build_job:
  stage: build
  script:
    - echo "Building application..."
    - npm install
    - npm run build
  retry:
    max: 2
    when: [ script_failure ]

test_job:
  stage: test
  script:
    - echo "Running tests..."
    - npm test
  variables:
    TEST_ENV: "local"

deploy_job:
  stage: deploy
  script:
    - echo "Deploying application..."
  only:
    - main  # Note: 'only' rules are parsed but not enforced locally, not yet
```

### Supported Features

✅ **Fully Supported:**

- `stages` - Pipeline stage definitions
- `variables` - Global and job-level variables
- `default` - Default configuration for all jobs
- `script`, `before_script`, `after_script` - Job execution scripts
- `include` - Include external YAML files
- `retry` - Retry logic with `max`, `when`, and `exit_codes`
- Variable substitution (`$VAR` and `${VAR}`)

⚠️ **Parsed but Limited:**

- `only`, `except`, `rules` - Parsed but not enforced (all jobs run)
- `image`, `services` - Parsed but ignored (no Docker support)
- `cache`, `artifacts` - Parsed but not implemented

❌ **Not Supported:**

- Docker/container execution
- GitLab Runner specific features
- Remote includes (only local file includes)

## 🔧 Advanced Usage

### Environment Variables

Control bitrab behavior with environment variables:

```bash
# Retry configuration
export BITRAB_RETRY_DELAY_SECONDS=3        # Base delay between retries
export BITRAB_RETRY_STRATEGY=exponential   # or "constant"
export BITRAB_RETRY_NO_SLEEP=1             # Skip sleep delays

# Subprocess behavior
export BITRAB_SUBPROC_MODE=capture         # or "stream"
export NO_COLOR=1                          # Disable colored output
```

### Configuration Examples

#### Simple Pipeline

```yaml
# .gitlab-ci.yml
script:
  - echo "Hello, Bitrab!"
```

#### Multi-stage Pipeline

```yaml
stages:
  - prepare
  - build
  - test

prepare_env:
  stage: prepare
  script:
    - echo "Preparing environment..."

build_app:
  stage: build
  script:
    - echo "Building application..."
  needs: [ prepare_env ]

test_app:
  stage: test
  script:
    - echo "Running tests..."
  needs: [ build_app ]
```

#### With Includes

```yaml
# .gitlab-ci.yml
include:
  - local: 'ci/build-jobs.yml'
  - local: 'ci/test-jobs.yml'

variables:
  GLOBAL_VAR: "shared_value"
```

## 🏗️ Architecture

Bitrab consists of several key components:

- **ConfigurationLoader** - Loads and processes YAML configuration with includes
- **PipelineProcessor** - Converts raw config into structured pipeline objects
- **JobExecutor** - Executes individual jobs with retry logic
- **StageOrchestrator** - Manages parallel execution within stages
- **VariableManager** - Handles variable substitution and environment preparation

## 🐛 Troubleshooting

### Common Issues

**Pipeline not found**

```bash
❌ Configuration file not found: .gitlab-ci.yml
```

Make sure you're in a directory with a `.gitlab-ci.yml` file or specify the path with `-c`.

**Job failures**

```bash
❌ Job build_job failed after 1 attempt(s) with exit code 1
```

Check the job script and ensure all commands are valid for your local environment.

**Permission errors**

```bash
❌ Permission denied: ./script.sh
```

Ensure scripts have execute permissions: `chmod +x script.sh`

### Debug Mode

Use the debug command to troubleshoot configuration issues:

```bash
bitrab debug                        # Show debug information
bitrab validate                     # Check for configuration errors
bitrab run --dry-run --verbose      # Preview with detailed output
```

## 🤝 Contributing

We welcome contributions! Here are some areas where help is needed:

- 🎯 **Job filtering** - Implement selective job execution
- 🔍 **GitLab API integration** - Server-side linting support
- 📊 **Dependency graphs** - Visual pipeline representation
- 🧹 **Artifact management** - Cache and artifact support
- 📈 **Performance profiling** - Execution time analysis

### Development Setup

```bash
git clone https://github.com/your-org/bitrab.git
cd bitrab
pip install -e ".[dev]"
pytest tests/
```

## 📜 License

MIT License - see [LICENSE](LICENSE) file for details.

## 🙋 FAQ

**Q: Why not use Docker like GitLab Runner?**
A: Bitrab is designed for local development where you want fast iteration without container overhead. It runs jobs
directly on your system using your existing tools.

**Q: Does it support all GitLab CI features?**
A: Bitrab focuses on core pipeline execution. Features requiring GitLab infrastructure (runners, registry, etc.) are not
supported.

**Q: Can I use this in production?**
A: Bitrab is designed for local development and testing. For production CI/CD, use official GitLab Runners.

**Q: How does retry logic work?**
A: Bitrab implements GitLab-compatible retry with exponential backoff, configurable conditions, and exit code filtering.

---

**Made with ❤️ for developers who love fast local CI/CD iteration**