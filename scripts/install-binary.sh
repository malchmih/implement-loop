#!/bin/sh

# Internal helper for `make install`; Make is the supported install interface.
set -eu

if [ "$#" -ne 2 ]; then
    printf 'Usage: %s PREFIX EXECUTABLE\n' "${0##*/}" >&2
    exit 2
fi

prefix=$1
source_binary=$2
if [ -z "$prefix" ]; then
    printf 'Error: PREFIX must not be empty. Set it to the installation prefix.\n' >&2
    exit 2
fi

bin_directory=$prefix/bin
destination=$bin_directory/implement-loop

refuse_unsafe_destination() {
    if [ -L "$destination" ]; then
        printf 'Error: refusing to replace symlink at %s; remove an old source-checkout symlink manually after verifying its target, then retry.\n' \
            "$destination" >&2
        return 1
    fi
    if [ -d "$destination" ]; then
        printf 'Error: refusing to replace directory at %s; choose another PREFIX or remove it manually after verifying its contents.\n' \
            "$destination" >&2
        return 1
    fi
    if [ -e "$destination" ] && [ ! -f "$destination" ]; then
        printf 'Error: refusing to replace non-regular destination at %s; choose another PREFIX or move it manually after verifying it is safe.\n' \
            "$destination" >&2
        return 1
    fi
}

if [ ! -f "$source_binary" ] || [ ! -x "$source_binary" ]; then
    printf 'Error: expected an executable regular file at %s; run make build first.\n' \
        "$source_binary" >&2
    exit 1
fi

refuse_unsafe_destination
if ! mkdir -p "$bin_directory"; then
    printf 'Error: cannot create installation directory %s.\n' "$bin_directory" >&2
    exit 1
fi
refuse_unsafe_destination

temporary_file=
cleanup() {
    if [ -n "$temporary_file" ]; then
        rm -f "$temporary_file"
    fi
}
trap cleanup 0
trap 'exit 1' HUP INT TERM

if ! temporary_file=$(mktemp "$bin_directory/.implement-loop.XXXXXX"); then
    printf 'Error: cannot create a temporary executable in %s.\n' "$bin_directory" >&2
    exit 1
fi
if ! install -m 755 "$source_binary" "$temporary_file"; then
    printf 'Error: cannot stage the executable for installation in %s.\n' "$bin_directory" >&2
    exit 1
fi
refuse_unsafe_destination
if ! mv -f "$temporary_file" "$destination"; then
    printf 'Error: cannot replace the installed executable at %s; the previous file was left in place.\n' \
        "$destination" >&2
    exit 1
fi
temporary_file=
trap - 0 HUP INT TERM

printf 'Installed: %s\n' "$destination"
