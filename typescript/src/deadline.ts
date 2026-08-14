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
