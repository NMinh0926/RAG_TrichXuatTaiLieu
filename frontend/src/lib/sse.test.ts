import { describe, expect, it } from 'vitest';
import { readSse, SseParser, type SseEvent } from './sse';

const encode = (text: string) => new TextEncoder().encode(text);

function feedAll(parser: SseParser, chunks: Uint8Array[]): SseEvent[] {
  return chunks.flatMap((chunk) => parser.push(chunk));
}

describe('SseParser', () => {
  it('parses one event per chunk', () => {
    const parser = new SseParser();
    expect(parser.push(encode('event: token\ndata: {"text":"a"}\n\n'))).toEqual([
      { event: 'token', data: '{"text":"a"}' },
    ]);
  });

  it('parses multiple events in a single chunk', () => {
    const parser = new SseParser();
    const events = parser.push(encode('event: meta\ndata: 1\n\nevent: token\ndata: 2\n\nevent: done\ndata: 3\n\n'));
    expect(events.map((e) => e.event)).toEqual(['meta', 'token', 'done']);
    expect(events.map((e) => e.data)).toEqual(['1', '2', '3']);
  });

  it('reassembles events split at every possible position', () => {
    const stream = 'event: token\ndata: {"text":"hello"}\n\nevent: done\ndata: {"ok":true}\n\n';
    for (let cut = 1; cut < stream.length; cut += 1) {
      const parser = new SseParser();
      const events = feedAll(parser, [encode(stream.slice(0, cut)), encode(stream.slice(cut))]);
      expect(events).toEqual([
        { event: 'token', data: '{"text":"hello"}' },
        { event: 'done', data: '{"ok":true}' },
      ]);
    }
  });

  it('decodes multi-byte UTF-8 characters split across chunks', () => {
    const bytes = encode('event: token\ndata: {"text":"Tiếng Việt có dấu 📄"}\n\n');
    const parser = new SseParser();
    const events = feedAll(parser, Array.from(bytes, (byte) => Uint8Array.of(byte)));
    expect(events).toHaveLength(1);
    expect(JSON.parse(events[0]!.data)).toEqual({ text: 'Tiếng Việt có dấu 📄' });
  });

  it('handles CRLF line endings, including a CR/LF pair split across chunks', () => {
    const parser = new SseParser();
    const events = feedAll(parser, [encode('event: token\r'), encode('\ndata: x\r\n\r'), encode('\n')]);
    expect(events).toEqual([{ event: 'token', data: 'x' }]);
  });

  it('joins multi-line data, skips comments and defaults the event name', () => {
    const parser = new SseParser();
    expect(parser.push(encode(': keep-alive\ndata: line 1\ndata: line 2\n\n'))).toEqual([
      { event: 'message', data: 'line 1\nline 2' },
    ]);
  });

  it('does not dispatch an event until its terminating blank line arrives', () => {
    const parser = new SseParser();
    expect(parser.push(encode('event: token\ndata: partial\n'))).toEqual([]);
    expect(parser.push(encode('\n'))).toEqual([{ event: 'token', data: 'partial' }]);
  });
});

describe('readSse', () => {
  it('reads every event from a byte stream', async () => {
    const chunks = ['event: meta\nda', 'ta: {"a":1}\n\nevent: tok', 'en\ndata: {"text":"ừ"}\n\n'].map(encode);
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(chunk));
        controller.close();
      },
    });
    const events: SseEvent[] = [];
    await readSse(body, (event) => events.push(event));
    expect(events).toEqual([
      { event: 'meta', data: '{"a":1}' },
      { event: 'token', data: '{"text":"ừ"}' },
    ]);
  });
});
