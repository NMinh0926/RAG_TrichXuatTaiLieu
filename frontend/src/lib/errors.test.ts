import { describe, expect, it } from 'vitest';
import { validationMessage } from './errors';

describe('validationMessage', () => {
  it("returns our validators' Vietnamese message without Pydantic's prefix", () => {
    const detail = [
      {
        type: 'value_error',
        loc: ['body', 'new_password'],
        msg: 'Value error, Mật khẩu không được bắt đầu hoặc kết thúc bằng khoảng trắng.',
        ctx: { error: {} },
      },
    ];
    expect(validationMessage(detail)).toBe('Mật khẩu không được bắt đầu hoặc kết thúc bằng khoảng trắng.');
  });

  it('does not show English validator text', () => {
    const detail = [{ type: 'value_error', loc: ['body', 'question'], msg: 'Value error, Question must not be blank' }];
    expect(validationMessage(detail)).toBe('Câu hỏi không hợp lệ.');
  });

  it('explains length limits per field', () => {
    expect(validationMessage([{ type: 'too_long', loc: ['body', 'document_ids'], msg: 'List should have at most 50 items', ctx: { max_length: 50 } }])).toBe(
      'Phạm vi hỏi chỉ được tối đa 50 mục.',
    );
    expect(validationMessage([{ type: 'string_too_short', loc: ['body', 'password'], msg: '...', ctx: { min_length: 8 } }])).toBe(
      'Mật khẩu cần ít nhất 8 ký tự.',
    );
    expect(validationMessage([{ type: 'string_too_long', loc: ['body', 'question'], ctx: { max_length: 4000 } }])).toBe(
      'Câu hỏi chỉ được tối đa 4000 ký tự.',
    );
    expect(validationMessage([{ type: 'missing', loc: ['body', 'username'] }])).toBe('Thiếu tên đăng nhập.');
  });

  it('falls back to the raw field name or a generic label', () => {
    expect(validationMessage([{ type: 'uuid_parsing', loc: ['path', 'document_id'] }])).toBe('document_id không hợp lệ.');
    expect(validationMessage([{ type: 'json_invalid', loc: ['body', 0] }])).toBe('Dữ liệu không hợp lệ.');
  });

  it('ignores anything that is not a validation list', () => {
    expect(validationMessage('Not Found')).toBeNull();
    expect(validationMessage([])).toBeNull();
    expect(validationMessage(undefined)).toBeNull();
  });
});
