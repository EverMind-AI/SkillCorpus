/**
 * One deadline helper, shared by the two model calls.
 *
 * Both run before the model answers the user, and both have a documented
 * fallback for the timeout, so the deadline is part of their contract rather
 * than a safety net.
 *
 * @module
 */

/**
 * Reject when `promise` has not settled within `ms`.
 * @param promise - the call to bound. It keeps running after a timeout; the
 *   caller has already moved on to its fallback.
 * @param ms - the deadline in milliseconds.
 * @returns the promise's value, or a rejection naming the elapsed deadline.
 */
export function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => { reject(new Error(`timed out after ${ms}ms`)) }, ms)
  })
  return Promise.race([promise, deadline]).finally(() => { clearTimeout(timer) })
}

/**
 * Run `run` under a deadline, and abort it when the deadline passes.
 *
 * `withTimeout` alone stops the *waiting*; the underlying call keeps
 * streaming until the provider gives up, spending tokens on a reply nobody
 * will read. This hands `run` a signal that fires on timeout, on failure,
 * and when `outer` (the turn's own cancellation) fires — so moving on and
 * hanging up happen together.
 *
 * @param run - the call to bound, receiving the signal to pass to transport.
 * @param ms - the deadline in milliseconds.
 * @param outer - the turn's signal; its abort propagates to `run`.
 * @returns `run`'s value, or the timeout rejection after aborting `run`.
 */
export async function bounded<T>(
  run: (signal: AbortSignal) => Promise<T>,
  ms: number,
  outer?: AbortSignal,
): Promise<T> {
  const controller = new AbortController()
  const onOuterAbort = () => { controller.abort() }
  outer?.addEventListener('abort', onOuterAbort, { once: true })
  const attempt = run(controller.signal)
  // The caller stops listening after a timeout, but the attempt is still a
  // live promise; a late transport rejection must not surface as unhandled.
  attempt.catch(() => {})
  try {
    return await withTimeout(attempt, ms)
  } catch (error) {
    controller.abort()
    throw error
  } finally {
    outer?.removeEventListener('abort', onOuterAbort)
  }
}
