// Apply the saved theme before first paint to avoid a light/dark flash.
// A file rather than an inline script so the Content-Security-Policy can forbid inline scripts.
try {
  var saved = localStorage.getItem('theme');
  var dark = saved ? saved === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.classList.toggle('dark', dark);
} catch (e) {}
