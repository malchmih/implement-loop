#!/bin/sh

set -eu

if [ "$#" -gt 1 ]; then
    printf 'Usage: %s [DESTINATION]\n' "${0##*/}" >&2
    exit 2
fi

absolute_path() {
    case $1 in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$PWD" "$1" ;;
    esac
}

case $0 in
    /*|*/*) installer_path=$0 ;;
    *) installer_path=$(command -v "$0" 2>/dev/null || printf '%s/%s' "$PWD" "$0") ;;
esac
installer_path=$(absolute_path "$installer_path")

if ! source_dir=$(CDPATH='' cd -P "$(dirname "$installer_path")" && pwd -P); then
    printf 'Error: cannot resolve the installer source directory.\n' >&2
    exit 1
fi
source_executable=$source_dir/implement-loop
if [ ! -f "$source_executable" ] || [ ! -x "$source_executable" ]; then
    printf 'Error: expected an executable at %s.\n' "$source_executable" >&2
    exit 1
fi

if [ "$#" -eq 1 ]; then
    destination=$1
else
    if [ -z "${HOME:-}" ]; then
        printf 'Error: HOME is not set; pass a destination directory explicitly.\n' >&2
        exit 1
    fi
    destination=$HOME/.local/bin
fi
if [ -z "$destination" ]; then
    printf 'Error: destination must be a non-empty directory path.\n' >&2
    exit 2
fi
destination_path=$(absolute_path "$destination")
if ! mkdir -p "$destination_path"; then
    printf 'Error: cannot create destination directory %s.\n' "$destination" >&2
    exit 1
fi
if ! destination=$(CDPATH='' cd -P "$destination_path" && pwd -P); then
    printf 'Error: cannot resolve destination directory %s.\n' "$destination_path" >&2
    exit 1
fi

link_path=$destination/implement-loop
if [ -L "$link_path" ]; then
    existing_target=$(readlink "$link_path" 2>/dev/null || true)
    if [ "$existing_target" = "$source_executable" ]; then
        printf 'Already installed: %s -> %s\n' "$link_path" "$source_executable"
    else
        printf 'Error: refusing to overwrite existing link %s. Choose another destination or move the link manually after verifying it is safe.\n' \
            "$link_path" >&2
        exit 1
    fi
elif [ -e "$link_path" ]; then
    printf 'Error: refusing to overwrite existing file or directory %s. Choose another destination or move it manually after verifying it is safe.\n' \
        "$link_path" >&2
    exit 1
else
    if ! ln -s "$source_executable" "$link_path"; then
        printf 'Error: cannot create symlink %s.\n' "$link_path" >&2
        exit 1
    fi
    printf 'Installed: %s -> %s\n' "$link_path" "$source_executable"
fi

printf 'Runtime prerequisites (checked only; this installer does not install or authenticate them):\n'
if command -v python3 >/dev/null 2>&1; then
    if python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
        printf 'Available: Python 3.11 or later (python3).\n'
    else
        printf 'Missing: Python 3.11 or later; python3 is too old or could not run.\n'
    fi
else
    printf 'Missing: Python 3.11 or later (python3).\n'
fi

report_command() {
    command_name=$1
    purpose=$2
    if command -v "$command_name" >/dev/null 2>&1; then
        printf 'Available: %s (%s).\n' "$command_name" "$purpose"
    else
        printf 'Missing: %s (%s).\n' "$command_name" "$purpose"
    fi
}

report_command git 'required for target repositories'
report_command gh 'required for GitHub issue operations'
report_command opencode 'required for default execution'
report_command copilot 'optional; needed only with --harness copilot'
printf 'GitHub authentication and the external implement skill must be configured separately.\n'

case ":${PATH:-}:" in
    *:"$destination":*)
        printf 'PATH already includes %s.\n' "$destination"
        ;;
    *)
        printf 'PATH does not include %s. Add this directory to PATH in your shell configuration; this installer does not edit shell profiles.\n' \
            "$destination"
        ;;
esac

printf 'The command remains backed by this source checkout: updates here update the linked command; moving or deleting this checkout breaks it.\n'
