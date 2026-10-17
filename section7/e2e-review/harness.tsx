import * as React from "react";
import { createRoot } from "react-dom/client";
import { ReviewPanel } from "../src/components/ReviewPanel";
import { createDevWorkflowApi } from "../src/lib/devWorkflowApi";

const q = new URLSearchParams(location.search);
const api = createDevWorkflowApi({ base: q.get("base") ?? "", getToken: () => q.get("token") });
createRoot(document.getElementById("root")!).render(
  <ReviewPanel api={api} changeSetId={q.get("cs") ?? ""} canWrite={q.get("write") !== "0"} />,
);
