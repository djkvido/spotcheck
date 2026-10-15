"use strict";

try {
  const theme = localStorage.getItem("spotcheck-theme");
  if (theme) document.documentElement.dataset.theme = theme;
} catch {}
