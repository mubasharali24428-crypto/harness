# Probe only: log every command bash is about to run (BASH_COMMAND), %q-quoted so framing bytes are exact.
set -T
trap 'printf "%q\n" "$BASH_COMMAND" >> /probe-out/bash_command.log' DEBUG
