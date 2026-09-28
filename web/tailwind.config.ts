import type { Config } from 'tailwindcss'

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          primary: 'rgb(var(--brand-primary-rgb) / <alpha-value>)',
          cyan: 'rgb(var(--brand-cyan-rgb) / <alpha-value>)',
          border: 'rgb(var(--brand-border-rgb) / <alpha-value>)',
        },
        ink: { DEFAULT: 'var(--ink)', 2: 'var(--ink-2)', 3: 'var(--ink-3)' },
        surface: 'var(--surface)',
        field: 'var(--field)',
        hover: { DEFAULT: 'var(--hover)', 2: 'var(--hover-2)' },
        line: 'var(--line)',
        green: 'var(--green)',
        red: 'var(--red)',
        'green-tint': 'var(--green-tint)',
        'red-tint': 'var(--red-tint)',
      },
      borderRadius: {
        control: '6px',
        chip: '6px',
      },
      boxShadow: {
        hairline: 'inset 0 0 0 1px var(--line)',
        btn: 'inset 0 0 0 1px var(--line), 0 1px 2px rgb(0 0 0 / 0.04)',
        overlay: '0 8px 24px rgb(0 0 0 / 0.18), inset 0 0 0 1px var(--line)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
    },
  },
  plugins: [],
} satisfies Config
