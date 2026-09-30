import { useState, type InputHTMLAttributes } from 'react';
import { Icon } from './Icon';

interface PasswordInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  /** Layout classes for the wrapper (e.g. `flex-1` inside a flex row). */
  wrapperClassName?: string;
}

/** Password field with a show/hide toggle, so a pasted password can be checked before submitting. */
export function PasswordInput({ className = '', wrapperClassName = '', ...props }: PasswordInputProps) {
  const [visible, setVisible] = useState(false);
  return (
    <div className={`relative ${wrapperClassName}`}>
      <input {...props} type={visible ? 'text' : 'password'} className={`${className} pr-10`} spellCheck={false} />
      <button
        type="button"
        onClick={() => setVisible((value) => !value)}
        className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
        aria-label={visible ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
        aria-pressed={visible}
        title={visible ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
      >
        <Icon name={visible ? 'eyeOff' : 'eye'} className="size-4" />
      </button>
    </div>
  );
}
