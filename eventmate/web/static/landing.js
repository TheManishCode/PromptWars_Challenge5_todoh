/* to;doh landing - video + motion preferences only. The entrance
   animation is pure CSS so the page works with JS disabled. */
(function () {
  'use strict';
  /* the poster and the video are not in every checkout: take away what failed to load so the plain gradient shows, not a broken image */
  var poster = document.querySelector('.hero__poster');
  if (poster && poster.complete && poster.naturalWidth === 0) poster.remove();
  else if (poster) poster.addEventListener('error', function () { poster.remove(); });

  var video = document.querySelector('.hero__video');
  if (!video) return;
  var source = video.querySelector('source');
  if (source) source.addEventListener('error', function () { video.remove(); });

  var reduceQuery = window.matchMedia('(prefers-reduced-motion: reduce)');

  function syncVideo() {
    if (reduceQuery.matches || document.visibilityState === 'hidden') {
      video.pause();
    } else {
      var p = video.play();
      if (p && typeof p.catch === 'function') p.catch(function () {});
    }
  }

  if (reduceQuery.matches) {
    video.removeAttribute('autoplay');
    video.pause();
  }

  if (typeof reduceQuery.addEventListener === 'function') {
    reduceQuery.addEventListener('change', syncVideo);
  }
  document.addEventListener('visibilitychange', syncVideo);
})();
