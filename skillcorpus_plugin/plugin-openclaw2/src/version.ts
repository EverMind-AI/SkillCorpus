/**
 * The version this package reports at runtime.
 *
 * One constant, and a test that fails when it drifts from `package.json`.
 * Both were separately wrong before that test existed: the OpenClaw 2.0
 * context engine announced `0.1.0` and the WorkBuddy MCP server announced
 * `0.2.0`, each against a package that said otherwise, because a release bump
 * touches manifests and forgets the string a host actually shows.
 *
 * Not imported from `package.json`: the bundle is built with
 * `--packages=external`, so a JSON import would have to resolve beside the
 * shipped file rather than inside it.
 *
 * @module
 */

export const VERSION = '0.3.0'
