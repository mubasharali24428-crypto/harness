import { contextBridge, ipcRenderer } from "electron";

// The renderer gets exactly two capabilities: a version string and deep-link notifications.
contextBridge.exposeInMainWorld("harnessDesktop", {
  version: "0.1.0",
  onDeepLink: (cb: (url: string) => void) => {
    const handler = (_e: unknown, url: unknown) => typeof url === "string" && cb(url);
    ipcRenderer.on("harness:deep-link", handler);
    return () => ipcRenderer.removeListener("harness:deep-link", handler);
  },
});
