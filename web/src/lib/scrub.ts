/* Strips Agent8088 engine tool-call protocol from user-visible text.
 *
 * The engine's tool protocol rides in the CONTENT channel: the model types
 * `<flower>FUNCTION<flower>: name <flower>ARGS<flower>: {...}` as ordinary
 * output. The backend now scrubs streamed tokens, tool results, and command
 * output - this module is the frontend backstop for what the backend cannot
 * reach: assistant messages loaded from session history (stored raw in
 * S.messages) and any event path added later. Mirrors the ordering of
 * engine.strip_tool_json: full call blocks first (so their JSON payload goes
 * with them), then leftover fragments, then stray sentinel chars.
 *
 * Sentinel chars are written as unicode escapes so this file stays
 * ASCII-clean: \u273f = the flower sentinel.
 */

const FLOWER = '\u273f'
const TC_OPEN = '<tool_call>'
const TC_CLOSE = '</tool_call>'

// Ordered rules: full blocks first (so their JSON payload goes with them),
// then leftover fragments, then any stray flower chars.
const RULES: Array<[RegExp, string]> = [
  // <flower>FUNCTION<flower>: name <flower>ARGS<flower>: {...}
  [
    new RegExp(
      FLOWER + 'FUNCTION' + FLOWER + '[\\s\\S]*?' +
      FLOWER + 'ARGS' + FLOWER + '\\s*:\\s*\\{[\\s\\S]*?\\}',
      'g',
    ),
    '',
  ],
  // <flower>{"name": ..., "arguments": {...}}<flower>
  [new RegExp(FLOWER + '\\{[\\s\\S]*?\\}' + FLOWER, 'g'), ''],
  // <tool_call>...</tool_call>
  [
    new RegExp(
      TC_OPEN.replace(/[<>/]/g, '\\$&') + '[\\s\\S]*?' +
      TC_CLOSE.replace(/[<>/]/g, '\\$&'),
      'g',
    ),
    '',
  ],
  // <|mask_start|>...<|mask_end|>
  [/<\|mask_start\|>[\s\S]*?<\|mask_end\|>/g, ''],
  // leftover <flower>...<flower> fragments, then any stray flowers
  [new RegExp(FLOWER + '[^' + FLOWER + '\\n]*' + FLOWER, 'g'), ''],
  [new RegExp(FLOWER, 'g'), ''],
]

export function scrubMarkup(text: string): string {
  if (!text) return text
  let out = text
  for (const [re, replacement] of RULES) {
    out = out.replace(re, replacement)
  }
  return out
}
