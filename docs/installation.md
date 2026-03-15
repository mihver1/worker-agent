# Installation

Artel supports source-based development installs and a bootstrap installer for end users.

## Requirements

- Python 3.12 or newer
- `git`
- `uv` if you are installing from source

## Install from source

Use this path if you want to work on the repository itself or run the latest checkout directly.

```bash
git clone git@github.com:mihver1/artel.git artel-agent
cd artel-agent
uv sync
artel init
```

If your shell does not expose the `artel` entry point directly yet, run it through `uv`:

```bash
uv run artel init
uv run artel --help
```

## Install with the bootstrap script

Use the installer if you want a self-contained local installation without managing the repository manually.

```bash
curl -fsSL https://raw.githubusercontent.com/mihver1/artel/main/install.sh | bash
```

The installer will:

- check for `git`
- install `uv` if needed
- ensure Python 3.12 is available
- copy or clone the project into a dedicated install directory
- create an `artel` launcher in your local bin directory

## First-run setup

Generate the default config files after installation:

```bash
artel init
```

This creates:

- `~/.config/artel/config.toml` for global settings
- `.artel/config.toml` in the current project for local overrides
- `.artel/AGENTS.md` for project-specific instructions

On first run, Artel also migrates legacy Worker config and project state when it finds them.

## Local docs preview

To preview this documentation site locally:

```bash
uv sync --dev
uv run mkdocs serve
```
