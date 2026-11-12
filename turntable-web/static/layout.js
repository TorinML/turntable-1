/* Turntable-1: pick the layout before the page draws. Phones get the mobile layout (tabs, at the
   phone's own width); tablets and computers keep the desktop layout. ?layout=mobile or
   ?layout=desktop picks one and remembers it; ?layout=auto goes back to choosing by screen size. */
(function () {
  var pick = null;
  try {
    var q = new URLSearchParams(location.search).get("layout");
    if (q === "auto") localStorage.removeItem("turntable-layout");
    else if (q === "mobile" || q === "desktop") localStorage.setItem("turntable-layout", q);
    pick = localStorage.getItem("turntable-layout");
  } catch (e) { /* storage blocked: choose by size */ }
  var phone = Math.min(screen.width, screen.height) < 700;
  if (pick ? pick === "mobile" : phone) {
    document.documentElement.classList.add("mobile");
    var vp = document.querySelector('meta[name="viewport"]');
    if (vp) vp.setAttribute("content", "width=device-width, initial-scale=1, viewport-fit=cover");
  }
})();
