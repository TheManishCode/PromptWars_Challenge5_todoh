/* to;doh landing - motion preferences only. The entrance
   animation is pure CSS so the page works with JS disabled. */
(function () {
  'use strict';
  /* lite: ?lite=1, the browser's data-saver, or a device with 2 GB of memory or less. Motion is switched off. */
  var conn = navigator.connection || {};
  var lite = /[?&]lite=1\b/.test(location.search) || conn.saveData || (navigator.deviceMemory && navigator.deviceMemory <= 2);
  if (lite) document.documentElement.classList.add('lite');
})();
