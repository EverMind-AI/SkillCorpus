/**
 * A ZIP reader, because Node has no equivalent of Python's `zipfile`.
 *
 * `node:zlib` decompresses a deflate stream but knows nothing about the ZIP
 * container, so reading a bundle means parsing the central directory. This
 * is that, and nothing more: entries out, in the order the directory lists
 * them. It reads a hostile archive, so every field is bounds-checked
 * against the buffer before it is used and every entry is rejected rather
 * than repaired.
 *
 * A dependency would have done this too. The engine has no third-party
 * runtime dependency, and the OpenClaw plugin bundles it into a single
 * file, so one is a cost paid by both hosts for one function.
 *
 * @module
 */

import { inflateRawSync } from 'node:zlib'

/** One file in the archive. Directories are not reported. */
export interface ZipEntry {
  /** The stored path, exactly as the archive spells it. Never trusted. */
  readonly name: string
  /** Uncompressed size the directory claims. Verified after inflating. */
  readonly declaredSize: number
  /** Read and decompress this entry. Throws on a corrupt or hostile one. */
  read(): Buffer
}

const EOCD_SIGNATURE = 0x06054b50
const CENTRAL_SIGNATURE = 0x02014b50
const LOCAL_SIGNATURE = 0x04034b50

/** Stored, and deflated. Every other method is refused rather than guessed at. */
const STORED = 0
const DEFLATED = 8

/**
 * The end-of-central-directory record, which is the only fixed point in a
 * ZIP file: it sits last, and everything else is found through it. It may
 * be followed by a comment, so the signature is searched for backwards.
 */
function findEndOfCentralDirectory(buffer: Buffer): number {
  const minimum = 22
  if (buffer.length < minimum) throw new Error('not a zip archive: too short')
  // A comment is at most 0xffff bytes, so the record cannot be further back.
  const earliest = Math.max(0, buffer.length - minimum - 0xffff)
  for (let offset = buffer.length - minimum; offset >= earliest; offset -= 1) {
    if (buffer.readUInt32LE(offset) === EOCD_SIGNATURE) return offset
  }
  throw new Error('not a zip archive: no end-of-central-directory record')
}

/** Read a length-prefixed name, refusing one that runs past the buffer. */
function readName(buffer: Buffer, start: number, length: number): string {
  if (start + length > buffer.length) throw new Error('zip entry name runs past the archive')
  return buffer.toString('utf8', start, start + length)
}

/**
 * List every file in an archive.
 *
 * Reading is deferred: the returned entries carry a `read()` so a caller can
 * decide per entry — by name, by size — before spending memory on one.
 *
 * @param buffer - the archive bytes.
 * @returns one entry per stored file, in central-directory order.
 * @throws Error when the archive is not a readable ZIP. A caller treats
 *   that the same way it treats any other unusable download.
 */
export function readZipEntries(buffer: Buffer): ZipEntry[] {
  const eocd = findEndOfCentralDirectory(buffer)
  const entryCount = buffer.readUInt16LE(eocd + 10)
  const directoryOffset = buffer.readUInt32LE(eocd + 16)
  if (directoryOffset > buffer.length) throw new Error('zip central directory is out of range')

  const entries: ZipEntry[] = []
  let cursor = directoryOffset
  for (let index = 0; index < entryCount; index += 1) {
    if (cursor + 46 > buffer.length) throw new Error('zip central directory is truncated')
    if (buffer.readUInt32LE(cursor) !== CENTRAL_SIGNATURE) {
      throw new Error(`zip central directory entry ${index} has a bad signature`)
    }
    const method = buffer.readUInt16LE(cursor + 10)
    const compressedSize = buffer.readUInt32LE(cursor + 20)
    const declaredSize = buffer.readUInt32LE(cursor + 24)
    const nameLength = buffer.readUInt16LE(cursor + 28)
    const extraLength = buffer.readUInt16LE(cursor + 30)
    const commentLength = buffer.readUInt16LE(cursor + 32)
    const localOffset = buffer.readUInt32LE(cursor + 42)
    const name = readName(buffer, cursor + 46, nameLength)
    cursor += 46 + nameLength + extraLength + commentLength

    // A directory entry carries no data; the extractor creates parents itself.
    if (name.endsWith('/')) continue

    entries.push({
      name,
      declaredSize,
      read(): Buffer {
        if (method !== STORED && method !== DEFLATED) {
          throw new Error(`${name}: unsupported compression method ${method}`)
        }
        if (localOffset + 30 > buffer.length) throw new Error(`${name}: local header out of range`)
        if (buffer.readUInt32LE(localOffset) !== LOCAL_SIGNATURE) {
          throw new Error(`${name}: bad local header signature`)
        }
        // The local header repeats the name and extra lengths, and the extra
        // field legitimately differs from the directory's — so both are read
        // here rather than reused from above.
        const localNameLength = buffer.readUInt16LE(localOffset + 26)
        const localExtraLength = buffer.readUInt16LE(localOffset + 28)
        const start = localOffset + 30 + localNameLength + localExtraLength
        const end = start + compressedSize
        if (end > buffer.length) throw new Error(`${name}: data runs past the archive`)

        const raw = buffer.subarray(start, end)
        const out = method === STORED ? Buffer.from(raw) : inflateRawSync(raw)
        // A directory that understates a size is how a zip bomb gets past a
        // budget check made before inflating.
        if (out.length !== declaredSize) {
          throw new Error(
            `${name}: inflated to ${out.length} bytes, directory declared ${declaredSize}`,
          )
        }
        return out
      },
    })
  }
  return entries
}
