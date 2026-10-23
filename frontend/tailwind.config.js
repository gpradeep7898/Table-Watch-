/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#070C17",
          900: "#0B1220",
          850: "#0F172A",
          800: "#121C31",
          750: "#17223A",
          700: "#1D2944",
          600: "#2A3858",
          500: "#3B4B70",
        },
        txt: { DEFAULT: "#E7EDF7", muted: "#93A3BE", faint: "#64748B" },
        brand: { DEFAULT: "#2DD4BF", strong: "#14B8A6", soft: "#99F6E4" },
        st: {
          free: "#2DD4BF",
          seated: "#60A5FA",
          overdue: "#F87171",
          served: "#A78BFA",
          cleaning: "#FBBF24",
          unknown: "#94A3B8",
        },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', '"SF Pro Text"', '"Inter"', '"Segoe UI"', "Roboto", "Helvetica", "Arial", "sans-serif"],
        mono: ['"SF Mono"', "ui-monospace", "Menlo", "Consolas", "monospace"],
      },
      boxShadow: { card: "0 1px 0 rgba(255,255,255,0.03) inset, 0 8px 24px rgba(0,0,0,0.25)" },
    },
  },
  plugins: [],
};
