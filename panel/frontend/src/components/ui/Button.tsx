// Button —— 统一按钮样式（primary / secondary / ghost / danger）
import type { ButtonHTMLAttributes, ReactNode } from 'react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  children: ReactNode
}

const STYLES: Record<Variant, string> = {
  primary: 'bg-accent text-white hover:bg-accent/85',
  secondary: 'border border-line text-fg-1 hover:bg-bg-3',
  ghost: 'text-fg-2 hover:bg-bg-3 hover:text-fg-1',
  danger: 'border border-error/30 bg-error/15 text-error hover:bg-error/25',
}

export function Button({
  variant = 'primary',
  className = '',
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-1.5 rounded-md px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${STYLES[variant]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  )
}
