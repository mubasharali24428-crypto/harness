import { BrowserWindow, app, safeStorage, session, shell } from "electron";
import path from "node:path";
import {
  WEB_PREFERENCES,
  contentSecurityPolicy,
  externalOpenAllowed,
  isAllowedNavigation,
  parseDeepLink,
  permissionDecision,
  resolveAppOrigin,
} from "./security";
import { TokenVault } from "./token-store";

const appOrigin = resolveAppOrigin(process.env.HARNESS_WEB_URL);
let win: BrowserWindow | null = null;

export const vault = () =>
  new TokenVault(path.join(app.getPath("userData"), "vault"), safeStorage);

function route(url: string): void {
  const target = parseDeepLink(url, appOrigin);
  if (target && win) void win.loadURL(target);
}

function createWindow(): void {
  win = new BrowserWindow({
    width: 1280,
    height: 860,
    show: false,
    webPreferences: { ...WEB_PREFERENCES, preload: path.join(__dirname, "preload.js") },
  });
  win.once("ready-to-show", () => win?.show());
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (externalOpenAllowed(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    if (!isAllowedNavigation(url, appOrigin)) {
      e.preventDefault();
      if (externalOpenAllowed(url)) void shell.openExternal(url);
    }
  });
  void win.loadURL(appOrigin.toString());
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.setAsDefaultProtocolClient("harness");
  app.on("second-instance", (_e, argv) => {
    const link = argv.find((a) => a.startsWith("harness://"));
    if (link) route(link);
    win?.focus();
  });
  app.on("open-url", (e, url) => {
    e.preventDefault();
    route(url);
  });
  app.whenReady().then(() => {
    session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) =>
      cb(permissionDecision(permission)),
    );
    session.defaultSession.setPermissionCheckHandler((_wc, permission) => permissionDecision(permission));
    session.defaultSession.webRequest.onHeadersReceived((details, cb) => {
      cb({
        responseHeaders: {
          ...details.responseHeaders,
          "Content-Security-Policy": [contentSecurityPolicy(appOrigin)],
        },
      });
    });
    createWindow();
  });
  app.on("window-all-closed", () => app.quit());
}
