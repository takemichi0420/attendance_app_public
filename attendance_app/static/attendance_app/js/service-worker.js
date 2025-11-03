self.addEventListener("install", (event) => {
    console.log("Service Worker installed");
    event.waitUntil(
      caches.open("v1").then((cache) => {
        return cache.addAll([
          "/attendance/",
          "/static/attendance_app/css/custom.css",
        ]);
      })
    );
  });
  
  self.addEventListener("fetch", (event) => {
    event.respondWith(
      caches.match(event.request).then((response) => {
        return response || fetch(event.request);
      })
    );
  });