import * as React from "react";
import { createRoot } from "react-dom/client";
import { TaskInbox } from "../src/components/TaskInbox";
import { createIntakeApi } from "../src/lib/intakeApi";

const q = new URLSearchParams(location.search);
const api = createIntakeApi({ base: q.get("base") ?? "", getToken: () => q.get("token") });
createRoot(document.getElementById("root")!).render(<TaskInbox api={api} canDecide={q.get("write") !== "0"} />);
