/** Tailwind 仅作为新增样式工具，关闭 preflight 避免影响既有 main.css。 */
export default {
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: {
        primary: '#2e57e5',
        surface: {
          sunken: '#f7f9fc',
          DEFAULT: '#ffffff',
        },
        text: {
          primary: '#1f2329',
          secondary: '#646a73',
          placeholder: '#8f959e',
        },
        border: '#dee0e3',
      },
      borderRadius: {
        lg: '10px',
      },
      spacing: {
        loose: '24px',
      },
    },
  },
  plugins: [],
}
