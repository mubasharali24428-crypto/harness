#!/usr/bin/env node
import { run, CliError } from "../src/cli.mjs";

run(process.argv.slice(2)).catch((e) => {
  console.error(e instanceof CliError ? e.message : `unexpected error: ${e.message}`);
  process.exit(e instanceof CliError ? 2 : 1);
});
