/** Incremental Server-Sent Events parser (https://html.spec.whatwg.org/#event-stream-interpretation). */

export interface SseEvent {
  event: string;
  data: string;
}

const LINE_BREAK = /\r\n|\r|\n/;

export class SseParser {
  private readonly decoder = new TextDecoder();
  private buffer = '';
  private eventName = '';
  private dataLines: string[] = [];

  /**
   * Feeds raw bytes (possibly split anywhere, even inside a UTF-8 sequence) and returns the events
   * completed by them. As in the standard, an event not terminated by a blank line is never dispatched.
   */
  push(chunk: Uint8Array): SseEvent[] {
    this.buffer += this.decoder.decode(chunk, { stream: true });
    const events: SseEvent[] = [];
    let match = LINE_BREAK.exec(this.buffer);
    while (match) {
      // A trailing \r may be the first half of \r\n: wait for the next chunk.
      if (match[0] === '\r' && match.index === this.buffer.length - 1) break;
      const line = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const event = this.processLine(line);
      if (event) events.push(event);
      match = LINE_BREAK.exec(this.buffer);
    }
    return events;
  }

  private processLine(line: string): SseEvent | null {
    if (line === '') return this.dispatch();
    if (line.startsWith(':')) return null;
    const colon = line.indexOf(':');
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') this.eventName = value;
    else if (field === 'data') this.dataLines.push(value);
    return null;
  }

  private dispatch(): SseEvent | null {
    const event = this.dataLines.length ? { event: this.eventName || 'message', data: this.dataLines.join('\n') } : null;
    this.eventName = '';
    this.dataLines = [];
    return event;
  }
}

/** Reads an SSE response body to the end, calling `onEvent` for every event. */
export async function readSse(body: ReadableStream<Uint8Array>, onEvent: (event: SseEvent) => void): Promise<void> {
  const parser = new SseParser();
  const reader = body.getReader();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      parser.push(value).forEach(onEvent);
    }
  } finally {
    reader.releaseLock();
  }
}
