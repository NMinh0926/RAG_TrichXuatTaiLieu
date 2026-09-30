import { describe, expect, it } from 'vitest';
import { newPasswordProblem } from './password';

describe('newPasswordProblem', () => {
  it('accepts a normal password, including inner spaces', () => {
    expect(newPasswordProblem('mat khau dai')).toBeNull();
  });

  it('rejects leading or trailing whitespace with a specific message', () => {
    const message = 'Mật khẩu không được bắt đầu hoặc kết thúc bằng khoảng trắng.';
    expect(newPasswordProblem(' secret123')).toBe(message);
    expect(newPasswordProblem('secret123 ')).toBe(message);
    expect(newPasswordProblem('secret123\t')).toBe(message);
  });

  it('checks the length limits', () => {
    expect(newPasswordProblem('short')).toBe('Mật khẩu cần ít nhất 8 ký tự.');
    expect(newPasswordProblem('x'.repeat(257))).toBe('Mật khẩu chỉ được tối đa 256 ký tự.');
  });
});
