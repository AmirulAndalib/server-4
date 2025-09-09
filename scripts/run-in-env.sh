#!/usr/bin/env sh
set -eu


if [ -z "${DEV_CONTAINER:-}" ]; then
  # In devcontainers the environment is already activated in the Dockerfile.
  # For everything else, activate pyenv and virtualenv if present:

  # pyenv, pyenv-virtualenv
  if [ -s .python-version ]; then
      PYENV_VERSION=$(head -n 1 .python-version)
      export PYENV_VERSION
  fi

  # other common virtualenvs
  my_path=$(git rev-parse --show-toplevel)

  for venv in venv .venv .; do
    if [ -f "${my_path}/${venv}/bin/activate" ]; then
      . "${my_path}/${venv}/bin/activate"
      break
    fi
  done
fi

# And then run the specified command:
exec "$@"
