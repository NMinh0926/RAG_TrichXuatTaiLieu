/** Client-side checks mirroring the backend's NewPassword rules, so the user sees why a password is refused. */

export const MIN_PASSWORD = 8;
export const MAX_PASSWORD = 256;

/** Why a new password would be rejected, or null when it is acceptable. */
export function newPasswordProblem(password: string): string | null {
  if (password !== password.trim()) return 'Mật khẩu không được bắt đầu hoặc kết thúc bằng khoảng trắng.';
  if (password.length < MIN_PASSWORD) return `Mật khẩu cần ít nhất ${MIN_PASSWORD} ký tự.`;
  if (password.length > MAX_PASSWORD) return `Mật khẩu chỉ được tối đa ${MAX_PASSWORD} ký tự.`;
  return null;
}
