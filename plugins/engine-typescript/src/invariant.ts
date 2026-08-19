/**
 * Package-owned invariant companion for `@deepseek-ai/dsh-skill-search`.
 * @module @deepseek-ai/dsh-skill-search/invariant
 */

/* jscpd:ignore-start */
import type { Context } from '@deepseek-ai/cordis'
import type { InvariantInstaller } from '@deepseek-ai/dsh-invariants'

const PACKAGE_NAME = '@deepseek-ai/dsh-skill-search'

/** Cordis companion plugin name. */
export const name = 'skill-search-invariant'
/** Service required before the companion can reserve package ownership. */
export const inject = ['invariants']

/**
 * No runtime invariant: retrieval owns no durable data and emits no event stream. Its one
 * per-turn output is the injected message, which the session log already records, and every
 * step degrades to fewer skills rather than to inconsistent state.
 */
const install: InvariantInstaller = () => {}

/**
 * Register this package's invariant companion.
 * @param ctx - Cordis context carrying the invariant service.
 * @returns the installed registration's disposer after setup succeeds.
 */
export const apply = (ctx: Context): Promise<() => void> =>
  Promise.resolve(ctx.invariants.register(PACKAGE_NAME, install))
/* jscpd:ignore-end */
