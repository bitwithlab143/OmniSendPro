// Apply the saved theme before first paint (external file so the CSP can forbid inline scripts).
try {
  var t = localStorage.getItem("osp-theme");
  if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
} catch (e) {}
