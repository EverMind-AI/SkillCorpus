/**
 * Ambient shim for the one runtime value imported from the `openclaw` peer.
 *
 * All types live in `./openclaw-types.ts`, a real module, so the emitted
 * declarations never reference the peer's internal names and a consumer can
 * typecheck without the peer installed.
 */
declare module 'openclaw/plugin-sdk/plugin-entry' {
  export function definePluginEntry(
    options: import('./openclaw-types.js').DefinePluginEntryOptions,
  ): import('./openclaw-types.js').DefinedPluginEntry
}
